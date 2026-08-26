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
    RemoteContainersProvider,
    RemoteSmartctlDiskHealthProvider,
    RemoteStorageProvider,
    SmartctlDiskHealthProvider,
    UnavailableDiskHealthProvider,
)
from homelab_console.transports import (
    CircuitBreakerCommandRunner,
    SshCommandRunner,
    SshTransportError,
)

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
        runner = CircuitBreakerCommandRunner(
            SshCommandRunner(transport),
            cooldown_seconds=max(5.0, transport.connect_timeout),
            failure_types=(SshTransportError,),
        )
        return NodeRuntime(
            node=configured.node,
            host=RemoteHostProvider(
                runner,
                fallback_hostname=configured.node.title,
            ),
            storage=RemoteStorageProvider(runner),
            containers=RemoteContainersProvider(
                runner,
                helper=(
                    configured.containers.helper
                    if configured.containers is not None
                    else None
                ),
            ),
            disk_health=(
                RemoteSmartctlDiskHealthProvider(
                    runner,
                    devices=configured.disk_health.devices,
                    helper=configured.disk_health.helper,
                )
                if configured.disk_health is not None
                else UnavailableDiskHealthProvider(_REMOTE_CAPABILITY_REASON)
            ),
            command_runner=runner,
            container_access=(
                "ssh-helper"
                if configured.containers is not None
                else "ssh-user"
            ),
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
