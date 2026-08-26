from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from homelab_console.nodes.models import Node
from homelab_console.providers import (
    ContainersProvider,
    DiskHealthProvider,
    HostProvider,
    StorageProvider,
)

if TYPE_CHECKING:
    from homelab_console.transports import CommandRunner


ContainerAccess = Literal["local", "ssh-user", "ssh-helper"]


@dataclass(frozen=True, slots=True)
class NodeRuntime:
    """Runtime capability providers composed for one monitored node."""

    node: Node
    host: HostProvider
    storage: StorageProvider
    containers: ContainersProvider
    disk_health: DiskHealthProvider
    command_runner: CommandRunner | None = None
    container_access: ContainerAccess = "local"
