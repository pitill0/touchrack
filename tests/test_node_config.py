from pathlib import Path

import pytest

from homelab_console.config import load_config
from homelab_console.nodes import (
    LocalTransportConfig,
    RemoteContainersConfig,
    RemoteDiskHealthConfig,
    SshTransportConfig,
)


def test_config_loads_local_and_ssh_nodes(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
nodes:
  - id: local
    title: TouchRack
    transport:
      type: local
  - id: rpi3
    title: Raspberry Pi 3
    transport:
      type: ssh
      host: 192.168.1.40
      user: touchrack-monitor
      port: 2222
      identity_file: /etc/touchrack/keys/rpi3
      known_hosts_file: /etc/touchrack/ssh_known_hosts
      connect_timeout: 4
      command_timeout: 8
    disk_health:
      devices:
        - /dev/sda
      helper: /usr/local/libexec/touchrack-smartctl
    containers:
      helper: /usr/local/libexec/touchrack-container-inventory
""".strip(),
        encoding="utf-8",
    )

    cfg = load_config(path)

    assert cfg.nodes is not None
    assert tuple(item.node.id for item in cfg.nodes) == ("local", "rpi3")
    assert cfg.nodes[0].node.title == "TouchRack"
    assert cfg.nodes[0].transport == LocalTransportConfig()
    assert cfg.nodes[1].node.transport == "ssh"
    assert cfg.nodes[1].transport == SshTransportConfig(
        host="192.168.1.40",
        user="touchrack-monitor",
        port=2222,
        identity_file="/etc/touchrack/keys/rpi3",
        known_hosts_file="/etc/touchrack/ssh_known_hosts",
        connect_timeout=4.0,
        command_timeout=8.0,
    )
    assert cfg.nodes[1].disk_health == RemoteDiskHealthConfig(
        devices=("/dev/sda",),
        helper="/usr/local/libexec/touchrack-smartctl",
    )
    assert cfg.nodes[1].containers == RemoteContainersConfig(
        helper="/usr/local/libexec/touchrack-container-inventory"
    )


@pytest.mark.parametrize(
    ("containers", "message"),
    [
        ("[]", "containers must be a mapping"),
        ("{helper: relative}", "helper must be an absolute path"),
        ("{helper: ''}", "helper must be a non-empty string"),
        ("{unexpected: true}", "unknown keys: unexpected"),
    ],
)
def test_config_rejects_invalid_remote_containers(
    tmp_path: Path,
    containers: str,
    message: str,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
nodes:
  - id: remote
    title: Remote
    transport:
      type: ssh
      host: 192.168.50.20
      user: touchrack-monitor
      identity_file: /etc/touchrack/ssh/remote
      known_hosts_file: /etc/touchrack/ssh/known_hosts
    containers: {containers}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_config_rejects_privileged_containers_on_local_node(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
nodes:
  - id: local
    title: Local
    transport:
      type: local
    containers: {}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="containers requires SSH transport"):
        load_config(path)


@pytest.mark.parametrize(
    ("disk_health", "message"),
    [
        ("{}", "devices must contain at least one device"),
        ("{devices: [sda]}", "entries must be absolute paths"),
        ("{devices: [/dev/sda], helper: relative}", "helper must be an absolute path"),
    ],
)
def test_config_rejects_invalid_remote_disk_health(
    tmp_path: Path,
    disk_health: str,
    message: str,
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
nodes:
  - id: rpi3
    title: Raspberry Pi 3
    transport:
      type: ssh
      host: 192.168.1.40
      user: touchrack-monitor
      identity_file: /etc/touchrack/keys/rpi3
      known_hosts_file: /etc/touchrack/known_hosts
    disk_health: {disk_health}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_config(path)


@pytest.mark.parametrize(
    ("nodes_yaml", "message"),
    [
        ("{}", "nodes must be a list"),
        ("[]", "at least one"),
        (
            """
  - id: local
    title: Local
    transport: local
""",
            r"nodes\[0\]\.transport must be a mapping",
        ),
        (
            """
  - id: local
    title: Local
    transport:
      type: telnet
""",
            "must be one of",
        ),
        (
            """
  - id: local
    title: Local
    transport:
      type: local
  - id: local
    title: Duplicate
    transport:
      type: ssh
""",
            "duplicate node id",
        ),
        (
            """
  - id: first
    title: First
    transport:
      type: local
  - id: second
    title: Second
    transport:
      type: local
""",
            "at most one local",
        ),
    ],
)
def test_config_rejects_invalid_node_structures(
    tmp_path: Path,
    nodes_yaml: str,
    message: str,
) -> None:
    path = tmp_path / "config.yaml"
    document = (
        f"nodes: {nodes_yaml}\n"
        if nodes_yaml in {"{}", "[]"}
        else f"nodes:\n{nodes_yaml}\n"
    )
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("host", "-oProxyCommand=bad", "safe SSH host"),
        ("host", "bad host", "safe SSH host"),
        ("user", "root@other", "safe SSH user"),
        ("port", "0", "between 1 and 65535"),
        ("port", "22.5", "must be an integer"),
        ("connect_timeout", "0", "between 0.1 and 30.0"),
        ("command_timeout", "301", "between 0.1 and 300.0"),
        ("identity_file", "relative-key", "must be an absolute path"),
        ("known_hosts_file", "known_hosts", "must be an absolute path"),
    ],
)
def test_config_rejects_unsafe_ssh_values(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    values = {
        "host": "192.168.1.40",
        "user": "touchrack-monitor",
        "port": "22",
        "identity_file": "/etc/touchrack/keys/rpi3",
        "known_hosts_file": "/etc/touchrack/ssh_known_hosts",
        "connect_timeout": "3",
        "command_timeout": "5",
    }
    values[field] = value
    path = tmp_path / "config.yaml"
    path.write_text(
        f"""
nodes:
  - id: rpi3
    title: Raspberry Pi 3
    transport:
      type: ssh
      host: {values["host"]}
      user: {values["user"]}
      port: {values["port"]}
      identity_file: {values["identity_file"]}
      known_hosts_file: {values["known_hosts_file"]}
      connect_timeout: {values["connect_timeout"]}
      command_timeout: {values["command_timeout"]}
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=message):
        load_config(path)


def test_config_rejects_unknown_and_password_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
nodes:
  - id: rpi3
    title: Raspberry Pi 3
    transport:
      type: ssh
      host: 192.168.1.40
      user: touchrack-monitor
      password: forbidden
      identity_file: /etc/touchrack/keys/rpi3
      known_hosts_file: /etc/touchrack/ssh_known_hosts
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unknown keys: password"):
        load_config(path)
