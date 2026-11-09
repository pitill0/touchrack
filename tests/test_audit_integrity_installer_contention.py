from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-systemd.sh"


def real_lock_script() -> str:
    """Extract actual lifecycle/traps and lock acquisition/guards."""
    source = INSTALLER.read_text()

    lifecycle_start = source.index("DEPLOYMENT_STARTED=0\n")
    lifecycle_end = source.index(
        "\n# The lock covers the service check",
        lifecycle_start,
    )
    lifecycle = source[lifecycle_start:lifecycle_end]

    lock_start = source.index(
        'if ! mkdir "$INSTALL_LOCK"; then',
        lifecycle_end,
    )
    lock_end = source.index(
        '\nif systemctl is-active --quiet "$SERVICE_NAME"; then',
        lock_start,
    )
    lock = source[lock_start:lock_end]

    assert 'LOCK_ACQUIRED=1' in lock
    assert 'rmdir "$INSTALL_LOCK"' in lifecycle
    assert 'trap cleanup EXIT' in lifecycle
    assert 'if [ -e "$OLD_DIR" ]' in lock
    assert 'if [ -e "$UNIT_BACKUP" ]' in lock
    assert 'if [ -e "$BUILD_DIR" ]' in lock

    return (
        "set -eu\n"
        'RUNTIME_PARENT="$1"\n'
        'RUNTIME_DIR="$RUNTIME_PARENT/runtime"\n'
        'BUILD_DIR="$RUNTIME_PARENT/build"\n'
        'OLD_DIR="$RUNTIME_PARENT/old"\n'
        'UNIT_BACKUP="$RUNTIME_PARENT/unit-backup"\n'
        'INSTALL_LOCK="$RUNTIME_PARENT/install.lock"\n'
        'SERVICE_PATH="$RUNTIME_PARENT/service"\n'
        'READY="$2"\n'
        'RELEASE="$3"\n'
        'MODE="$4"\n'
        + lifecycle
        + "\n"
        + lock
        + "\n"
        + """
case "$MODE" in
    hold)
        : > "$READY"
        while [ ! -e "$RELEASE" ]; do
            sleep 0.02
        done
        ;;
    quick)
        ;;
    *)
        exit 64
        ;;
esac
exit 0
"""
    )


def invocation(
    script: str, root: Path, mode: str,
) -> list[str]:
    return [
        "/bin/sh",
        "-c",
        script,
        "touchrack-lock-test",
        str(root),
        str(root / "ready"),
        str(root / "release"),
        mode,
    ]


def test_two_processes_cannot_own_same_install_lock(
    tmp_path: Path,
) -> None:
    script = real_lock_script()
    owner = subprocess.Popen(
        invocation(script, tmp_path, "hold"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        deadline = time.monotonic() + 5

        while not (tmp_path / "ready").exists():
            if owner.poll() is not None:
                stdout, stderr = owner.communicate()
                pytest.fail(
                    f"owner exited early: {owner.returncode}, "
                    f"{stdout!r}, {stderr!r}"
                )

            if time.monotonic() >= deadline:
                pytest.fail("owner did not acquire the lock")

            time.sleep(0.01)

        assert (tmp_path / "install.lock").is_dir()

        contender = subprocess.run(
            invocation(script, tmp_path, "quick"),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

        assert contender.returncode == 1, contender.stderr
        assert "installation already locked" in contender.stderr
        assert (tmp_path / "install.lock").is_dir()
        assert owner.poll() is None

    finally:
        (tmp_path / "release").touch()
        try:
            owner.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            owner.kill()
            owner.communicate(timeout=5)
            raise

    assert owner.returncode == 0
    assert not (tmp_path / "install.lock").exists()

    # A later installation must be able to acquire a released lock.
    next_install = subprocess.run(
        invocation(script, tmp_path, "quick"),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert next_install.returncode == 0, next_install.stderr
    assert not (tmp_path / "install.lock").exists()


@pytest.mark.parametrize("artifact", ["build", "old", "unit-backup"])
def test_existing_artifact_is_preserved_and_lock_released(
    tmp_path: Path,
    artifact: str,
) -> None:
    script = real_lock_script()
    target = tmp_path / artifact

    if artifact == "unit-backup":
        target.write_text("previous-unit")
    else:
        target.mkdir()
        (target / "recovery-data").write_text("previous-runtime")

    attempt = subprocess.run(
        invocation(script, tmp_path, "quick"),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert attempt.returncode == 1, attempt.stderr
    assert "existing" in attempt.stderr
    assert not (tmp_path / "install.lock").exists()

    if artifact == "unit-backup":
        assert target.read_text() == "previous-unit"
    else:
        assert (target / "recovery-data").read_text() == (
            "previous-runtime"
        )



@pytest.mark.parametrize(
    ("artifact", "kind"),
    [
        (".touchrack-old.12345", "directory"),
        (".touchrack-unit.54321", "file"),
        (".touchrack-build.11111", "directory"),
        (".touchrack-old.99999", "dangling-symlink"),
    ],
)
def test_previous_pid_artifact_blocks_new_install_without_data_loss(
    tmp_path: Path,
    artifact: str,
    kind: str,
) -> None:
    script = real_lock_script()
    previous = tmp_path / artifact
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "version").write_text("existing-runtime")

    if kind == "directory":
        previous.mkdir()
        (previous / "recovery").write_text("critical-backup")
    elif kind == "file":
        previous.write_text("critical-backup")
    else:
        previous.symlink_to(tmp_path / "missing-target")

    result = subprocess.run(
        invocation(script, tmp_path, "quick"),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert "unresolved previous installation artifact" in result.stderr
    assert not (tmp_path / "install.lock").exists()
    assert (runtime / "version").read_text() == "existing-runtime"

    if kind == "directory":
        assert (previous / "recovery").read_text() == "critical-backup"
    elif kind == "file":
        assert previous.read_text() == "critical-backup"
    else:
        assert previous.is_symlink()



def test_existing_lock_is_never_removed_by_contender(
    tmp_path: Path,
) -> None:
    script = real_lock_script()
    lock = tmp_path / "install.lock"
    lock.mkdir()
    (lock / "owner").write_text("different-process")

    attempt = subprocess.run(
        invocation(script, tmp_path, "quick"),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert attempt.returncode == 1, attempt.stderr
    assert (lock / "owner").read_text() == "different-process"
