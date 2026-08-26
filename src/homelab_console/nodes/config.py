from __future__ import annotations

from dataclasses import dataclass

from homelab_console.nodes.models import Node


@dataclass(frozen=True, slots=True)
class LocalTransportConfig:
    """Configuration marker for collection on the TouchRack host itself."""

    type: str = "local"


@dataclass(frozen=True, slots=True)
class SshTransportConfig:
    """Validated OpenSSH connection settings for one remote node."""

    host: str
    user: str
    identity_file: str
    known_hosts_file: str
    port: int = 22
    connect_timeout: float = 3.0
    command_timeout: float = 5.0
    type: str = "ssh"


TransportConfig = LocalTransportConfig | SshTransportConfig


@dataclass(frozen=True, slots=True)
class RemoteDiskHealthConfig:
    devices: tuple[str, ...]
    helper: str = "/usr/local/libexec/touchrack-smartctl"


@dataclass(frozen=True, slots=True)
class RemoteContainersConfig:
    """Optional privileged, read-only container inventory helper."""

    helper: str = "/usr/local/libexec/touchrack-container-inventory"


@dataclass(frozen=True, slots=True)
class ConfiguredNode:
    """One node identity paired with its transport configuration."""

    node: Node
    transport: TransportConfig
    disk_health: RemoteDiskHealthConfig | None = None
    containers: RemoteContainersConfig | None = None

    def __post_init__(self) -> None:
        if self.node.transport != self.transport.type:
            raise ValueError("node and transport types must match")
