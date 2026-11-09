import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-systemd.sh"


def installer_source() -> str:
    return INSTALLER.read_text()


def function_body(source: str, name: str) -> str:
    """Extract a top-level POSIX shell function for static checks."""
    header = f"{name}() {{"
    lines = source.splitlines()

    matches = [
        index
        for index, line in enumerate(lines)
        if line == header
    ]
    assert len(matches) == 1, f"missing function {name}"

    begin = matches[0] + 1
    for index in range(begin, len(lines)):
        if lines[index] == "}":
            return "\\n".join(lines[begin:index])

    raise AssertionError(f"unterminated function {name}")


def test_exit_cleanup_includes_transaction_rollback():
    source = installer_source()
    body = function_body(source, "cleanup")

    # On abnormal exit, cleanup must be able to restore the
    # previous runtime rather than only removing build files.
    assert "rollback_runtime" in body
    assert "trap cleanup EXIT" in source


def test_signals_propagate_failure_to_exit_cleanup():
    source = installer_source()

    # Merely trapping a signal to a function that returns is
    # insufficient: the interrupted installer may continue.
    for signal in ("HUP", "INT", "TERM"):
        assert re.search(
            rf"(?m)^trap .*exit [0-9]+.* {signal}$",
            source,
        ), f"{signal} must terminate with a nonzero status"


def test_backup_preserved_until_deployment_commit():
    source = installer_source()

    # Completion must be explicit, after systemd configuration
    # and daemon reload, before the old runtime is discarded.
    assert "DEPLOYMENT_COMMITTED=1" in source
    assert source.index("systemctl daemon-reload") < source.index(
        "DEPLOYMENT_COMMITTED=1"
    )
    assert source.index("DEPLOYMENT_COMMITTED=1") < source.index(
        'rm -rf "$OLD_DIR"',
        source.index("DEPLOYMENT_COMMITTED=1"),
    )


def test_installer_still_preserves_user_configuration():
    source = installer_source()
    assert 'if [ -e "$destination" ]; then' in source
    assert 'copy_initial_config "$CONFIG_DIR/config.yaml"' in source
    assert 'copy_initial_config "$CONFIG_DIR/services.yaml"' in source
