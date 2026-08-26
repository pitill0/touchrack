from dataclasses import replace
from datetime import datetime, timedelta, timezone

from homelab_console.models import ContainersSnapshot, HostSnapshot, StorageSnapshot
from homelab_console.nodes import LastSuccessfulSnapshotStore


def host_snapshot(*, error: str | None = None) -> HostSnapshot:
    return HostSnapshot(
        hostname="rpi3",
        uptime_seconds=100,
        cpu_percent=10,
        load_average=(0.1, 0.2, 0.3),
        memory_used=1,
        memory_total=2,
        disk_used=1,
        disk_total=2,
        temperature_c=40,
        ip_address="192.168.1.252",
        collected_at=datetime.now(timezone.utc),
        error=error,
    )


def test_store_returns_last_success_as_stale_with_original_timestamp() -> None:
    store = LastSuccessfulSnapshotStore()
    collected_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    fresh = replace(host_snapshot(), collected_at=collected_at)

    store.resolve("rpi3", "host", fresh)
    stale = store.resolve(
        "rpi3", "host", host_snapshot(error="SSH timed out")
    )

    assert stale.hostname == "rpi3"
    assert stale.cpu_percent == 10
    assert stale.collected_at == collected_at
    assert stale.error is None
    assert stale.stale is True
    assert stale.stale_error == "SSH timed out"


def test_store_does_not_cross_nodes_or_capabilities() -> None:
    store = LastSuccessfulSnapshotStore()
    store.resolve("rpi3", "host", host_snapshot())

    containers_error = ContainersSnapshot(
        (), datetime.now(timezone.utc), error="containers failed"
    )
    storage_error = StorageSnapshot(
        (), datetime.now(timezone.utc), error="storage failed"
    )

    assert store.resolve("lokalix", "containers", containers_error).stale is False
    assert store.resolve("rpi3", "storage", storage_error).stale is False


def test_new_success_replaces_stale_snapshot() -> None:
    store = LastSuccessfulSnapshotStore()
    store.resolve("rpi3", "host", host_snapshot())
    stale = store.resolve("rpi3", "host", host_snapshot(error="offline"))
    recovered = store.resolve("rpi3", "host", host_snapshot())

    assert stale.stale is True
    assert recovered.stale is False
    assert recovered.stale_error is None
