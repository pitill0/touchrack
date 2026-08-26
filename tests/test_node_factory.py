from __future__ import annotations

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.config import AppConfig, StorageConfig
from homelab_console.nodes import (
    ConfiguredNode,
    LocalTransportConfig,
    Node,
    NodeRegistry,
    RemoteContainersConfig,
    RemoteDiskHealthConfig,
    SshTransportConfig,
    build_node_registry,
)
from homelab_console.providers import (
    LocalHostProvider,
    RemoteHostProvider,
    RemoteSmartctlDiskHealthProvider,
    RemoteContainersProvider,
    RemoteStorageProvider,
    UnavailableContainersProvider,
    UnavailableDiskHealthProvider,
    UnavailableStorageProvider,
)
from homelab_console.transports import CircuitBreakerCommandRunner, SshCommandRunner


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
    assert isinstance(remote_runtime.host.runner, CircuitBreakerCommandRunner)
    assert isinstance(remote_runtime.host.runner.runner, SshCommandRunner)
    assert remote_runtime.host.runner.runner.config is ssh_config
    assert isinstance(remote_runtime.storage, RemoteStorageProvider)
    assert remote_runtime.storage.runner is remote_runtime.host.runner
    assert isinstance(remote_runtime.containers, RemoteContainersProvider)
    assert remote_runtime.containers.runner is remote_runtime.host.runner
    assert isinstance(remote_runtime.disk_health, UnavailableDiskHealthProvider)
    assert remote_runtime.container_access == "ssh-user"


def test_factory_enables_remote_disk_health_only_when_explicitly_configured() -> None:
    transport = SshTransportConfig(
        host="192.168.1.33",
        user="touchrack-monitor",
        identity_file="/etc/touchrack/id_ed25519",
        known_hosts_file="/etc/touchrack/known_hosts",
    )
    remote = ConfiguredNode(
        node=Node(id="nas", title="NAS", transport="ssh"),
        transport=transport,
        disk_health=RemoteDiskHealthConfig(
            devices=("/dev/sda",),
            helper="/usr/local/libexec/touchrack-smartctl",
        ),
    )

    runtime = build_node_registry(AppConfig(nodes=(remote,))).default

    assert isinstance(runtime.disk_health, RemoteSmartctlDiskHealthProvider)
    assert runtime.disk_health.runner is runtime.command_runner
    assert runtime.disk_health.devices == ("/dev/sda",)


def test_factory_enables_privileged_containers_only_when_configured() -> None:
    remote = ConfiguredNode(
        node=Node(id="runner", title="CI runner", transport="ssh"),
        transport=SshTransportConfig(
            host="192.168.50.30",
            user="touchrack-monitor",
            identity_file="/etc/touchrack/ssh/runner",
            known_hosts_file="/etc/touchrack/ssh/known_hosts",
        ),
        containers=RemoteContainersConfig(),
    )

    runtime = build_node_registry(AppConfig(nodes=(remote,))).default

    assert isinstance(runtime.containers, RemoteContainersProvider)
    assert runtime.containers.runner is runtime.command_runner
    assert runtime.containers.helper == (
        "/usr/local/libexec/touchrack-container-inventory"
    )
    assert runtime.container_access == "ssh-helper"


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
