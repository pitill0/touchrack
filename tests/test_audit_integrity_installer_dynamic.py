from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-systemd.sh"


def transaction_code() -> str:
    """Use actual installer functions and traps, not test replicas."""
    source = INSTALLER.read_text()

    start = "DEPLOYMENT_STARTED=0\n"
    end = '\n# The lock covers the service check and the entire deployment.'

    assert source.count(start) == 1
    assert source.count(end) == 1

    beginning = source.index(start)
    ending = source.index(end, beginning)

    code = source[beginning:ending]

    assert "rollback_runtime() {" in code
    assert "cleanup() {" in code
    assert "trap cleanup EXIT" in code
    assert "trap 'exit 143' TERM" in code
    assert "trap 'exit 130' INT" in code

    # Verify the real installer enters the transaction before moving
    # the old runtime and commits after daemon-reload.
    assert source.index("DEPLOYMENT_STARTED=1") < source.index(
        'mv "$RUNTIME_DIR" "$OLD_DIR"'
    )
    assert source.index("systemctl daemon-reload") < source.index(
        "DEPLOYMENT_COMMITTED=1"
    )

    return code


@pytest.mark.parametrize(
    ("scenario", "existing", "expected_rc", "restore_old"),
    [
        ("failure", True, 41, True),
        ("term", True, 143, True),
        ("int", True, 130, True),
        ("after-move", True, 42, True),
        ("first-install-failure", False, 43, False),
        ("success", True, 0, False),
        ("restore-failure", True, 1, False),
    ],
)
def test_runtime_transaction_under_failure_and_signals(
    tmp_path: Path,
    scenario: str,
    existing: bool,
    expected_rc: int,
    restore_old: bool,
) -> None:
    runtime = tmp_path / "runtime"
    backup = tmp_path / "old"
    build = tmp_path / "build"
    config = tmp_path / "config"
    unit = tmp_path / "homelab-touch-console.service"
    unit_backup = tmp_path / "unit-backup"

    build.mkdir()
    (build / "temporary-wheel").write_text("build")

    config.mkdir()
    (config / "config.yaml").write_text("preserve-me")
    (config / "services.yaml").write_text("preserve-services")

    if existing:
        runtime.mkdir()
        (runtime / "version").write_text("old")

    lifecycle = transaction_code()

    shell = (
        "set -eu\n"
        'RUNTIME_DIR="$1"\n'
        'OLD_DIR="$2"\n'
        'BUILD_DIR="$3"\n'
        'SERVICE_PATH="$4"\n'
        'UNIT_BACKUP="$5"\n'
        + lifecycle
        + "\n"
        + "BUILD_OWNED=1\n"
        + 'if [ -d "$RUNTIME_DIR" ]; then HAD_RUNTIME=1; fi\n'
        + "DEPLOYMENT_STARTED=1\n"
        + 'if [ "$HAD_RUNTIME" -eq 1 ]; then\n'
        + '    mv "$RUNTIME_DIR" "$OLD_DIR"\n'
        + "fi\n"
    )

    if scenario == "after-move":
        shell += "exit 42\n"
    else:
        shell += (
            'mkdir "$RUNTIME_DIR"\n'
            'printf "%s" "new" > "$RUNTIME_DIR/version"\n'
        )

        if scenario == "failure":
            shell += "exit 41\n"
        elif scenario == "term":
            shell += "kill -TERM $$\nexit 99\n"
        elif scenario == "int":
            shell += "kill -INT $$\nexit 99\n"
        elif scenario == "first-install-failure":
            shell += "exit 43\n"
        elif scenario == "success":
            shell += (
                "DEPLOYMENT_COMMITTED=1\n"
                'rm -rf "$OLD_DIR"\n'
                "exit 0\n"
            )
        elif scenario == "restore-failure":
            # Simulate the backup restore command failing, rather than
            # relying on filesystem permissions or the current UID.
            shell += (
                'mv() { return 1; }\n'
                "exit 44\n"
            )
        else:
            raise AssertionError(f"unknown scenario: {scenario}")

    completed = subprocess.run(
        ["/bin/sh", "-c", shell, "rollback-test",
         str(runtime), str(backup), str(build),
         str(unit), str(unit_backup)],
        text=True,
        capture_output=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == expected_rc, (
        scenario,
        completed.returncode,
        completed.stderr,
    )

    assert not build.exists(), scenario
    assert (config / "config.yaml").read_text() == "preserve-me"
    assert (config / "services.yaml").read_text() == "preserve-services"

    if restore_old:
        assert (runtime / "version").read_text() == "old"
        assert not backup.exists()
    elif scenario == "success":
        assert (runtime / "version").read_text() == "new"
        assert not backup.exists()
    elif scenario == "restore-failure":
        # Do not destroy the only remaining copy of the previous
        # runtime if moving it back fails.
        assert (backup / "version").read_text() == "old"
        assert "rollback failed" in completed.stderr
    else:
        assert not runtime.exists()
        assert not backup.exists()
