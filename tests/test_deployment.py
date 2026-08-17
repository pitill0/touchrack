from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_systemd_template_uses_root_owned_deployment_paths() -> None:
    unit = (ROOT / "systemd/homelab-touch-console.service.in").read_text()

    assert "WorkingDirectory=@RUNTIME_DIR@" in unit
    assert "Environment=HOMELAB_CONFIG=@CONFIG_DIR@/config.yaml" in unit
    assert (
        "ExecStart=/usr/bin/openvt -c 1 -f -s -w -- "
        "@VENV_BIN@/homelab-console"
    ) in unit
    assert "@PROJECT_DIR@" not in unit


def test_installer_uses_non_editable_root_owned_runtime() -> None:
    script = (ROOT / "scripts/install-systemd.sh").read_text()

    assert 'RUNTIME_DIR="/opt/touchrack"' in script
    assert 'CONFIG_DIR="/etc/touchrack"' in script
    assert '"$PYTHON_BIN" -m venv "$BUILD_VENV"' in script
    assert '"$BUILD_VENV/bin/python" -m pip wheel' in script
    assert '"$PYTHON_BIN" -m pip wheel' not in script
    assert '"$PYTHON_BIN" -m venv "$RUNTIME_DIR/venv"' in script
    assert '"$RUNTIME_DIR/venv/bin/pip" install "$WHEEL_PATH"' in script
    assert 'EXPECTED_SHEBANG="#!$RUNTIME_DIR/venv/bin/python"' in script
    assert "pip install -e" not in script
    assert "install -o root -g root -m 0644" in script
    assert 'if [ -e "$destination" ]; then' in script


def test_installer_refuses_to_replace_a_running_runtime() -> None:
    script = (ROOT / "scripts/install-systemd.sh").read_text()

    assert 'systemctl is-active --quiet "$SERVICE_NAME"' in script
    assert "Stop it before deploying a new runtime" in script


def test_uninstaller_preserves_configuration() -> None:
    script = (ROOT / "scripts/uninstall-systemd.sh").read_text()

    assert 'rm -rf "$RUNTIME_DIR"' in script
    assert 'rm -rf "$CONFIG_DIR"' not in script
    assert "Preserved configuration" in script


def test_installer_does_not_require_system_python_pip() -> None:
    script = (ROOT / "scripts/install-systemd.sh").read_text()

    assert '"$BUILD_VENV/bin/python" -m pip --version' in script
    assert '"$BUILD_VENV/bin/python" -m pip wheel' in script
    assert '"$PYTHON_BIN" -m pip ' not in script


def test_systemd_template_has_conservative_hardening() -> None:
    unit = (ROOT / "systemd/homelab-touch-console.service.in").read_text()

    expected = {
        "NoNewPrivileges=yes",
        "PrivateTmp=yes",
        "ProtectClock=yes",
        "ProtectControlGroups=yes",
        "ProtectHostname=yes",
        "ProtectKernelLogs=yes",
        "ProtectKernelModules=yes",
        "ProtectKernelTunables=yes",
        "LockPersonality=yes",
        "RestrictRealtime=yes",
        "RestrictSUIDSGID=yes",
        "SystemCallArchitectures=native",
    }
    for directive in expected:
        assert directive in unit

    # These remain deliberately unrestricted until hardware/provider testing
    # proves a narrower policy is safe.
    assert "PrivateDevices=yes" not in unit
    assert "DevicePolicy=" not in unit
    assert "SystemCallFilter=" not in unit


def test_systemd_template_limits_capabilities_and_umask() -> None:
    unit = (ROOT / "systemd/homelab-touch-console.service.in").read_text()

    bounding_line = next(
        line
        for line in unit.splitlines()
        if line.startswith("CapabilityBoundingSet=")
    )
    assert bounding_line == (
        "CapabilityBoundingSet=CAP_SYS_ADMIN CAP_SYS_RAWIO "
        "CAP_SYS_TTY_CONFIG CAP_MKNOD"
    )
    assert "UMask=0077" in unit

    forbidden = (
        "CAP_SETUID",
        "CAP_SETGID",
        "CAP_SETPCAP",
        "CAP_SYS_PTRACE",
        "CAP_KILL",
        "CAP_NET_ADMIN",
        "CAP_NET_RAW",
        "CAP_SYS_BOOT",
        "CAP_BPF",
        "CAP_AUDIT_CONTROL",
    )
    for capability in forbidden:
        assert capability not in bounding_line


def test_systemd_template_hardens_filesystem_and_namespaces() -> None:
    unit = (ROOT / "systemd/homelab-touch-console.service.in").read_text()

    expected = {
        "ProtectSystem=strict",
        "ProtectHome=yes",
        "RuntimeDirectory=touchrack",
        "RuntimeDirectoryMode=0750",
        "Environment=HOMELAB_LOCK_FILE=/run/touchrack/homelab-touch-console.lock",
        "RestrictNamespaces=yes",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
        "MemoryDenyWriteExecute=yes",
        "UMask=0077",
    }
    for directive in expected:
        assert directive in unit

    # Hardware access remains deliberately outside this step.
    assert "PrivateDevices=yes" not in unit
    assert "DevicePolicy=" not in unit
    assert "DeviceAllow=" not in unit


def test_systemd_address_family_allowlist_keeps_required_interfaces() -> None:
    unit = (ROOT / "systemd/homelab-touch-console.service.in").read_text()
    line = next(
        line
        for line in unit.splitlines()
        if line.startswith("RestrictAddressFamilies=")
    )

    assert line.split("=", 1)[1].split() == [
        "AF_UNIX",
        "AF_INET",
        "AF_INET6",
        "AF_NETLINK",
    ]
    assert "AF_PACKET" not in line
