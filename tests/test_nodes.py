from __future__ import annotations

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.nodes import Node, NodeRegistry, NodeRuntime


class StubProvider:
    async def collect(self):
        raise AssertionError("collection is not part of these tests")


def runtime(node_id: str, title: str | None = None) -> NodeRuntime:
    provider = StubProvider()
    return NodeRuntime(
        node=Node(id=node_id, title=title or node_id),
        host=provider,
        storage=provider,
        containers=provider,
        disk_health=provider,
    )


@pytest.mark.parametrize("node_id", ["local", "rpi3", "opi5-pro", "compute_01"])
def test_node_accepts_stable_ids(node_id: str) -> None:
    assert Node(id=node_id, title="Node").id == node_id


@pytest.mark.parametrize("node_id", ["", "Local", "-rpi3", "rpi.3", "rpi 3"])
def test_node_rejects_invalid_ids(node_id: str) -> None:
    with pytest.raises(ValueError, match="node id"):
        Node(id=node_id, title="Node")


def test_node_rejects_empty_title_and_transport() -> None:
    with pytest.raises(ValueError, match="title"):
        Node(id="local", title="  ")
    with pytest.raises(ValueError, match="transport"):
        Node(id="local", title="Local", transport="  ")


def test_registry_preserves_order_and_resolves_default() -> None:
    local = runtime("local")
    rpi3 = runtime("rpi3")
    registry = NodeRegistry((local, rpi3), default_node_id="local")

    assert registry.all() == (local, rpi3)
    assert tuple(registry) == (local, rpi3)
    assert len(registry) == 2
    assert registry.default is local
    assert registry.get("rpi3") is rpi3


def test_registry_rejects_empty_duplicate_and_missing_default() -> None:
    with pytest.raises(ValueError, match="at least one"):
        NodeRegistry((), default_node_id="local")

    local = runtime("local")
    with pytest.raises(ValueError, match="duplicate"):
        NodeRegistry((local, runtime("local")), default_node_id="local")
    with pytest.raises(ValueError, match="default node"):
        NodeRegistry((local,), default_node_id="missing")


def test_registry_reports_unknown_node_id() -> None:
    registry = NodeRegistry((runtime("local"),), default_node_id="local")
    with pytest.raises(KeyError, match="unknown node id"):
        registry.get("missing")


def test_app_composes_injected_providers_into_local_runtime() -> None:
    host = StubProvider()
    storage = StubProvider()
    containers = StubProvider()
    disk_health = StubProvider()

    app = HomelabConsole(
        provider=host,
        storage_provider=storage,
        containers_provider=containers,
        disk_health_provider=disk_health,
        touch_enabled=False,
    )

    assert app.active_node_id == "local"
    assert app.active_node is app.nodes.default
    assert app.active_node.node == Node(id="local", title="Local")
    assert app.active_node.host is host
    assert app.active_node.storage is storage
    assert app.active_node.containers is containers
    assert app.active_node.disk_health is disk_health
    # Compatibility aliases always resolve through the active runtime.
    assert app.provider is host
    assert app.storage_provider is storage
    assert app.containers_provider is containers
    assert app.disk_health_provider is disk_health
