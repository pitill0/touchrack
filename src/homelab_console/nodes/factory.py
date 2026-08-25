from __future__ import annotations

from typing import TYPE_CHECKING

from homelab_console.nodes.config import (
    ConfiguredNode,
    LocalTransportConfig,
    SshTransportConfig,
)
from homelab_console.nodes.models import Node
from homelab_console.nodes.registry import NodeRegistry
from homelab_console.nodes.runtime import NodeRuntime
from homelab_console.providers import (
    AutoContainersProvider,
    LocalHostProvider,
    LocalStorageProvider,
    RemoteHostProvider,
    SmartctlDiskHealthProvider,
    UnavailableContainersProvider,
    UnavailableDiskHealthProvider,
    UnavailableStorageProvider,
)
from homelab_console.transports import SshCommandRunner

if TYPE_CHECKING:
    from homelab_console.config import AppConfig


_REMOTE_CAPABILITY_REASON = "Remote capability is not available yet"


def build_node_registry(config: AppConfig) -> NodeRegistry:
    """Compose configured node transports into capability providers."""
    configured_nodes = config.nodes
    if configured_nodes is None:
        runtimes = (_local_runtime(Node(id="local", title="Local"), config),)
    else:
        runtimes = tuple(
            _configured_runtime(configured, config) for configured in configured_nodes
        )

    return NodeRegistry(runtimes, default_node_id=runtimes[0].node.id)


def _configured_runtime(
    configured: ConfiguredNode,
    config: AppConfig,
) -> NodeRuntime:
    transport = configured.transport
    if isinstance(transport, LocalTransportConfig):
        return _local_runtime(configured.node, config)
    if isinstance(transport, SshTransportConfig):
        return NodeRuntime(
            node=configured.node,
            host=RemoteHostProvider(
                SshCommandRunner(transport),
                fallback_hostname=configured.node.title,
            ),
            storage=UnavailableStorageProvider(_REMOTE_CAPABILITY_REASON),
            containers=UnavailableContainersProvider(_REMOTE_CAPABILITY_REASON),
            disk_health=UnavailableDiskHealthProvider(_REMOTE_CAPABILITY_REASON),
        )
    raise TypeError(f"unsupported transport config: {type(transport).__name__}")


def _local_runtime(node: Node, config: AppConfig) -> NodeRuntime:
    return NodeRuntime(
        node=node,
        host=LocalHostProvider(),
        storage=LocalStorageProvider(config.storage.mounts),
        containers=AutoContainersProvider(),
        disk_health=SmartctlDiskHealthProvider(devices=config.storage.disks),
    )
