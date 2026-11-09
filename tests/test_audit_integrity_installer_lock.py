from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts/install-systemd.sh"


def source() -> str:
    return INSTALLER.read_text()


def function_body(text: str, name: str) -> str:
    lines = text.splitlines()
    header = f"{name}() {{"
    assert lines.count(header) == 1

    start = lines.index(header) + 1
    for end in range(start, len(lines)):
        if lines[end] == "}":
            return "\n".join(lines[start:end])

    raise AssertionError(f"unterminated function: {name}")


def test_installer_acquires_exclusive_lock_before_mutating_runtime():
    script = source()

    assert 'INSTALL_LOCK=' in script
    assert 'mkdir "$INSTALL_LOCK"' in script
    assert "LOCK_ACQUIRED=1" in script

    acquisition = script.index('mkdir "$INSTALL_LOCK"')
    runtime_move = script.index('mv "$RUNTIME_DIR" "$OLD_DIR"')
    build_setup = script.index(
        'install -d -o root -g root -m 0755 "$BUILD_DIR"'
    )

    assert acquisition < build_setup < runtime_move


def test_cleanup_releases_only_its_own_lock():
    script = source()
    cleanup = function_body(script, "cleanup")

    assert "LOCK_ACQUIRED" in cleanup
    assert 'rmdir "$INSTALL_LOCK"' in cleanup
    assert "trap cleanup EXIT" in script

    # Lock removal must be conditional: another installer may
    # own an existing lock when this process fails to acquire it.
    assert 'if [ "$LOCK_ACQUIRED" -eq 1 ]; then' in cleanup


def test_existing_recovery_artifacts_are_never_preemptively_deleted():
    script = source()

    # PID reuse or an interrupted installation must not make an
    # earlier runtime backup eligible for deletion.
    assert 'rm -rf "$BUILD_DIR" "$OLD_DIR"' not in script

    assert 'OLD_DIR="$RUNTIME_PARENT/.touchrack-old.$$"' in script
    assert 'UNIT_BACKUP="$RUNTIME_PARENT/.touchrack-unit.$$"' in script

    # Collision handling is required before creating build files
    # or moving the previous runtime.
    assert 'if [ -e "$OLD_DIR" ]' in script
    assert 'if [ -e "$UNIT_BACKUP" ]' in script
    assert 'if [ -e "$BUILD_DIR" ]' in script


def test_existing_rollback_guards_remain_intact():
    script = source()
    cleanup = function_body(script, "cleanup")

    assert "rollback_runtime" in cleanup
    assert "rollback_service_unit" in cleanup
    assert "DEPLOYMENT_COMMITTED" in cleanup
