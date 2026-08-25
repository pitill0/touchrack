from __future__ import annotations

from dataclasses import dataclass

from homelab_console.nodes.models import Node
from homelab_console.providers import (
    ContainersProvider,
    DiskHealthProvider,
    HostProvider,
    StorageProvider,
)


@dataclass(frozen=True, slots=True)
class NodeRuntime:
    """Runtime capability providers composed for one monitored node."""

    node: Node
    host: HostProvider
    storage: StorageProvider
    containers: ContainersProvider
    disk_health: DiskHealthProvider
