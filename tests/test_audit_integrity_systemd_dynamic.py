from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-systemd.sh"


def transaction_code() -> str:
    """Extract the actual installer functions and signal traps."""
    source = INSTALLER.read_text()
    start = "DEPLOYMENT_STARTED=0\n"
    end = '\n# The lock covers the service check and the entire deployment.'

    assert source.count(start) == 1
    assert source.count(end) == 1

    fragment = source[
        source.index(start):source.index(end)
    ]

    assert "rollback_runtime() {" in fragment
    assert "rollback_service_unit() {" in fragment
    assert "cleanup() {" in fragment

    # Check the real script still backs up the unit before writing it.
    backup = source.index('cp -p "$SERVICE_PATH" "$UNIT_BACKUP"')
    replace = source.index('"$TEMPLATE" > "$SERVICE_PATH"')
    assert backup < replace
    assert source.index("DEPLOYMENT_STARTED=1") < backup

    return fragment


@pytest.mark.parametrize(
    ("scenario", "existing_unit", "expected_rc"),
    [
        ("success", True, 0),
        ("reload-failure", True, 42),
        ("first-install-failure", False, 42),
        ("signal", True, 143),
        ("restore-copy-failure", True, 1),
        ("restore-reload-failure", True, 1),
    ],
)
def test_systemd_transaction_recovers_consistently(
    tmp_path: Path,
    scenario: str,
    existing_unit: bool,
    expected_rc: int,
) -> None:
    runtime = tmp_path / "runtime"
    old_runtime = tmp_path / "old-runtime"
    build = tmp_path / "build"
    unit = tmp_path / "touchrack.service"
    unit_backup = tmp_path / "unit-backup"
    reload_log = tmp_path / "reload-log"
    failure_marker = tmp_path / "reload-failed-once"
    config = tmp_path / "config"

    runtime.mkdir()
    (runtime / "version").write_text("old")
    build.mkdir()
    (build / "wheel").write_text("temporary")

    config.mkdir()
    (config / "config.yaml").write_text("original-config")

    if existing_unit:
        unit.write_text("old-unit")

    script = (
        "set -eu\n"
        'RUNTIME_DIR="$1"\n'
        'OLD_DIR="$2"\n'
        'BUILD_DIR="$3"\n'
        'SERVICE_PATH="$4"\n'
        'UNIT_BACKUP="$5"\n'
        'RELOAD_LOG="$6"\n'
        'FAILURE_MARKER="$7"\n'
        + transaction_code()
        + "\n"
        + """
BUILD_OWNED=1

systemctl() {
    if [ "$1" != "daemon-reload" ]; then
        return 65
    fi

    printf '%s\\n' reload >> "$RELOAD_LOG"

    case "$SCENARIO" in
        reload-failure|first-install-failure)
            if [ ! -e "$FAILURE_MARKER" ]; then
                : > "$FAILURE_MARKER"
                return 42
            fi
            ;;
        restore-reload-failure)
            return 42
            ;;
    esac

    return 0
}

# Simulate the runtime replacement performed by the installer.
HAD_RUNTIME=1
DEPLOYMENT_STARTED=1
mv "$RUNTIME_DIR" "$OLD_DIR"
mkdir "$RUNTIME_DIR"
printf '%s' new > "$RUNTIME_DIR/version"

# Use the same backup and replacement boundaries as the installer.
if [ -f "$SERVICE_PATH" ]; then
    HAD_SERVICE_UNIT=1
    cp -p "$SERVICE_PATH" "$UNIT_BACKUP"
fi

UNIT_REPLACEMENT_STARTED=1
printf '%s' new-unit > "$SERVICE_PATH"

case "$SCENARIO" in
    signal)
        kill -TERM $$
        exit 99
        ;;
    restore-copy-failure)
        cp() {
            if [ "$1" = "-p" ] &&
               [ "$2" = "$UNIT_BACKUP" ]; then
                return 66
            fi
            command cp "$@"
        }
        exit 53
        ;;
esac

systemctl daemon-reload

DEPLOYMENT_COMMITTED=1
rm -rf "$OLD_DIR"
rm -f "$UNIT_BACKUP"
exit 0
"""
    )

    completed = subprocess.run(
        [
            "/bin/sh", "-c", script, "systemd-test",
            str(runtime),
            str(old_runtime),
            str(build),
            str(unit),
            str(unit_backup),
            str(reload_log),
            str(failure_marker),
        ],
        env={
            "PATH": "/usr/bin:/bin",
            "SCENARIO": scenario,
        },
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
    assert (config / "config.yaml").read_text() == "original-config"

    if scenario == "success":
        assert (runtime / "version").read_text() == "new"
        assert unit.read_text() == "new-unit"
        assert not old_runtime.exists()
        assert not unit_backup.exists()
        assert reload_log.read_text().splitlines() == ["reload"]
        return

    assert (runtime / "version").read_text() == "old"
    assert not old_runtime.exists()

    if scenario == "first-install-failure":
        assert not unit.exists()
        assert not unit_backup.exists()
    elif scenario == "restore-copy-failure":
        assert unit.read_text() == "new-unit"
        assert unit_backup.read_text() == "old-unit"
        assert "systemd unit rollback failed" in completed.stderr
    else:
        assert unit.read_text() == "old-unit"

        if scenario == "restore-reload-failure":
            assert unit_backup.read_text() == "old-unit"
            assert "systemd unit rollback failed" in completed.stderr
        else:
            assert not unit_backup.exists()

    reloads = (
        reload_log.read_text().splitlines()
        if reload_log.exists()
        else []
    )
    if scenario == "signal":
        assert reloads == ["reload"]
    elif scenario == "restore-copy-failure":
        assert reloads == []
    else:
        assert reloads == ["reload", "reload"]
