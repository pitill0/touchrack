from homelab_console.nodes import NodeConnectivityRegistry


def test_node_connectivity_starts_unknown_and_records_health() -> None:
    registry = NodeConnectivityRegistry(("lokalix", "rpi3"))

    assert registry.get("rpi3").status == "unknown"
    assert registry.get("rpi3").checked_at is None

    online = registry.record("rpi3", error=None)
    assert online.status == "online"
    assert online.checked_at is not None
    assert online.error is None

    offline = registry.record("rpi3", error="connection refused")
    assert offline.status == "offline"
    assert offline.error == "connection refused"
    assert registry.get("lokalix").status == "unknown"
