from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-systemd.sh"


def source() -> str:
    return INSTALLER.read_text()


def shell_function(name: str) -> str:
    lines = source().splitlines()
    header = f"{name}() {{"
    assert lines.count(header) == 1

    start = lines.index(header) + 1
    for end in range(start, len(lines)):
        if lines[end] == "}":
            return "\n".join(lines[start:end])

    raise AssertionError(f"unterminated function: {name}")


def test_existing_systemd_unit_is_backed_up_before_replacement():
    script = source()

    assert "UNIT_BACKUP=" in script
    assert "HAD_SERVICE_UNIT=" in script

    backup = script.index('cp -p "$SERVICE_PATH" "$UNIT_BACKUP"')
    replace = script.index('"$TEMPLATE" > "$SERVICE_PATH"')

    assert backup < replace
    assert script.index("DEPLOYMENT_STARTED=1") < backup


def test_rollback_restores_systemd_unit_and_reloads_daemon():
    script = source()
    cleanup = shell_function("cleanup")

    assert "rollback_service_unit" in cleanup

    restore = shell_function("rollback_service_unit")
    assert '"$UNIT_BACKUP" "$SERVICE_PATH"' in restore
    assert "systemctl daemon-reload" in restore

    assert script.index("systemctl daemon-reload") < script.index(
        "DEPLOYMENT_COMMITTED=1"
    )


def test_runtime_rollback_regression_remains_present():
    script = source()
    cleanup = shell_function("cleanup")

    assert "rollback_runtime" in cleanup
    assert "DEPLOYMENT_COMMITTED" in cleanup
    assert "trap cleanup EXIT" in script
