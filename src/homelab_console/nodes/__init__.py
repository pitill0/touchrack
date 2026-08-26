from __future__ import annotations

from typing import TYPE_CHECKING

from homelab_console.nodes.config import (
    ConfiguredNode,
    LocalTransportConfig,
    RemoteContainersConfig,
    RemoteDiskHealthConfig,
    SshTransportConfig,
    TransportConfig,
)
from homelab_console.nodes.models import Node
from homelab_console.nodes.connectivity import (
    NodeConnectivity,
    NodeConnectivityRegistry,
)
from homelab_console.nodes.registry import NodeRegistry
from homelab_console.nodes.runtime import NodeRuntime
from homelab_console.nodes.snapshots import LastSuccessfulSnapshotStore

if TYPE_CHECKING:
    from homelab_console.config import AppConfig


def build_node_registry(config: AppConfig) -> NodeRegistry:
    """Import runtime composition lazily to keep node value objects acyclic."""
    from homelab_console.nodes.factory import build_node_registry as build

    return build(config)

__all__ = [
    "ConfiguredNode",
    "LocalTransportConfig",
    "Node",
    "NodeConnectivity",
    "NodeConnectivityRegistry",
    "NodeRegistry",
    "NodeRuntime",
    "RemoteContainersConfig",
    "RemoteDiskHealthConfig",
    "LastSuccessfulSnapshotStore",
    "build_node_registry",
    "SshTransportConfig",
    "TransportConfig",
]
