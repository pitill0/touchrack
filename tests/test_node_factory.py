from __future__ import annotations

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.config import AppConfig, StorageConfig
from homelab_console.nodes import (
    ConfiguredNode,
    LocalTransportConfig,
    Node,
    NodeRegistry,
    SshTransportConfig,
    build_node_registry,
)
from homelab_console.providers import (
    LocalHostProvider,
    RemoteHostProvider,
    UnavailableContainersProvider,
    UnavailableDiskHealthProvider,
    UnavailableStorageProvider,
)
from homelab_console.transports import SshCommandRunner


def test_factory_preserves_implicit_local_node() -> None:
    registry = build_node_registry(AppConfig())

    assert len(registry) == 1
    assert registry.default.node == Node(id="local", title="Local")
    assert isinstance(registry.default.host, LocalHostProvider)


def test_factory_composes_configured_local_and_ssh_nodes() -> None:
    local = ConfiguredNode(
        node=Node(id="console", title="TouchRack"),
        transport=LocalTransportConfig(),
    )
    ssh_config = SshTransportConfig(
        host="192.168.1.33",
        user="touchrack-monitor",
        identity_file="/etc/touchrack/id_ed25519",
        known_hosts_file="/etc/touchrack/known_hosts",
    )
    remote = ConfiguredNode(
        node=Node(id="rpi3", title="Raspberry Pi 3", transport="ssh"),
        transport=ssh_config,
    )

    registry = build_node_registry(
        AppConfig(
            nodes=(local, remote),
            storage=StorageConfig(mounts=("/",), disks=()),
        )
    )

    assert [runtime.node.id for runtime in registry] == ["console", "rpi3"]
    assert registry.default.node.id == "console"
    remote_runtime = registry.get("rpi3")
    assert isinstance(remote_runtime.host, RemoteHostProvider)
    assert isinstance(remote_runtime.host.runner, SshCommandRunner)
    assert remote_runtime.host.runner.config is ssh_config
    assert isinstance(remote_runtime.storage, UnavailableStorageProvider)
    assert isinstance(remote_runtime.containers, UnavailableContainersProvider)
    assert isinstance(remote_runtime.disk_health, UnavailableDiskHealthProvider)


async def test_remote_unavailable_capabilities_return_explicit_state() -> None:
    reason = "not collected remotely"

    storage = await UnavailableStorageProvider(reason).collect()
    containers = await UnavailableContainersProvider(reason).collect()
    disk_health = await UnavailableDiskHealthProvider(reason).collect()

    assert storage.error == reason
    assert containers.error == reason
    assert disk_health.error == reason
    assert disk_health.available is False


def test_app_accepts_registry_and_uses_its_default_node() -> None:
    registry = build_node_registry(AppConfig())
    app = HomelabConsole(nodes=registry, touch_enabled=False)

    assert app.nodes is registry
    assert app.active_node_id == registry.default.node.id


def test_app_rejects_registry_combined_with_provider_injection() -> None:
    registry = build_node_registry(AppConfig())

    with pytest.raises(ValueError, match="cannot be combined"):
        HomelabConsole(nodes=registry, provider=registry.default.host)
