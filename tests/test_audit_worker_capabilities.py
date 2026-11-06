"""Regression coverage for capability refresh worker failures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from textual.worker import WorkerState

from homelab_console.app import HomelabConsole
from homelab_console.models import (
    ContainersSnapshot,
    DiskHealthSnapshot,
    FilesystemInfo,
    HostSnapshot,
    StorageSnapshot,
)
from homelab_console.screens import ContainersView, HostView, ServicesView, StorageView
from homelab_console.services import ServicesSnapshot


class SwitchableProvider:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.fail = False
        self.calls = 0

    async def collect(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("simulated capability failure")
        return self.snapshot


def build_app():
    now = datetime.now(UTC)

    host = SwitchableProvider(
        HostSnapshot(
            hostname="audit-host",
            uptime_seconds=3600,
            cpu_percent=12.0,
            load_average=(0.1, 0.2, 0.3),
            memory_used=1,
            memory_total=2,
            disk_used=3,
            disk_total=4,
            temperature_c=40.0,
            ip_address="127.0.0.1",
            collected_at=now,
        )
    )

    containers = SwitchableProvider(
        ContainersSnapshot(
            containers=(),
            collected_at=now,
            engine="audit",
        )
    )

    storage = SwitchableProvider(
        StorageSnapshot(
            filesystems=(),
            collected_at=now,
        )
    )

    disk = SwitchableProvider(
        DiskHealthSnapshot(
            disks=(),
            collected_at=now,
            available=True,
        )
    )

    services = SwitchableProvider(
        ServicesSnapshot(
            services=(),
            collected_at=now,
        )
    )

    app = HomelabConsole(
        provider=host,
        containers_provider=containers,
        storage_provider=storage,
        disk_health_provider=disk,
        refresh_seconds=60,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    # Replace only the collection boundary, preserving the actual
    # ServicesManager registration and Textual worker implementation.
    app.services_manager.collect = services.collect

    return app, {
        "containers": containers,
        "storage": storage,
        "services": services,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "view_class"),
    [
        ("containers", ContainersView),
        ("storage", StorageView),
        ("services", ServicesView),
    ],
)
async def test_capability_worker_survives_failure_and_recovers(
    capability, view_class
):
    app, providers = build_app()
    provider = providers[capability]

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        view = app.query_one(view_class)

        assert view.snapshot is not None
        initial_snapshot = view.snapshot

        # Force an exception in the selected refresh worker.
        provider.fail = True
        failed_worker = getattr(app, f"refresh_{capability}")()

        await pilot.pause()
        await pilot.pause()

        assert failed_worker.state == WorkerState.ERROR
        assert app.is_running

        # The last valid snapshot must not be discarded.
        assert view.snapshot is initial_snapshot

        # Verify that the next refresh succeeds.
        provider.fail = False
        recovered_worker = getattr(app, f"refresh_{capability}")()

        await pilot.pause()
        await pilot.pause()

        assert recovered_worker.state == WorkerState.SUCCESS
        assert app.is_running
        assert provider.calls >= 3
        assert view.snapshot is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "status_id", "health_id"),
    [
        ("containers", "#containers-status", "#containers-health"),
        ("storage", "#storage-status", "#storage-health"),
        ("services", "#services-status", "#services-health"),
    ],
)
async def test_worker_failure_is_visible_and_clears(
    capability, status_id, health_id
):
    app, providers = build_app()
    provider = providers[capability]

    view_classes = {
        "containers": ContainersView,
        "storage": StorageView,
        "services": ServicesView,
    }

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        view = app.query_one(view_classes[capability])
        assert view.snapshot is not None
        previous_snapshot = view.snapshot

        provider.fail = True
        failed_worker = getattr(app, f"refresh_{capability}")()

        await pilot.pause()
        await pilot.pause()

        assert failed_worker.state == WorkerState.ERROR
        assert app.is_running
        assert view.snapshot is previous_snapshot

        status = str(app.query_one(status_id).content)
        health = str(app.query_one(health_id).content)

        assert "STALE" in health
        assert "simulated capability failure" in status

        provider.fail = False
        recovered_worker = getattr(app, f"refresh_{capability}")()

        await pilot.pause()
        await pilot.pause()

        assert recovered_worker.state == WorkerState.SUCCESS
        assert app.is_running
        assert "STALE" not in str(app.query_one(health_id).content)
        assert "simulated capability failure" not in str(
            app.query_one(status_id).content
        )


@pytest.mark.asyncio
async def test_smart_exception_does_not_discard_filesystem_refresh():
    """Filesystems remain refreshable if SMART collection raises."""
    app, _ = build_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        view = app.query_one(StorageView)
        assert view.snapshot is not None
        assert view.disk_snapshot is not None

        # A newly collected filesystem snapshot must still reach the UI.
        fresh = StorageSnapshot(
            filesystems=(),
            collected_at=datetime.now(UTC),
            error="filesystem audit marker",
        )
        app.storage_provider.snapshot = fresh

        # Failure occurs only in the independent SMART provider.
        app.disk_health_provider.fail = True

        worker = app.refresh_storage()
        await pilot.pause()
        await pilot.pause()

        assert app.is_running
        assert worker.state == WorkerState.SUCCESS

        assert view.snapshot is not None
        assert view.snapshot.error is None
        assert view.snapshot.stale is True
        assert view.snapshot.stale_error == "filesystem audit marker"

        assert view.disk_snapshot is not None
        assert not view.disk_snapshot.available
        assert "simulated capability failure" in (
            view.disk_snapshot.error or ""
        )

        # Recovery of SMART must not require application restart.
        app.disk_health_provider.fail = False
        recovered = app.refresh_storage()
        await pilot.pause()
        await pilot.pause()

        assert recovered.state == WorkerState.SUCCESS
        assert view.disk_snapshot is not None
        assert view.disk_snapshot.available


@pytest.mark.asyncio
async def test_smart_failure_preserves_healthy_filesystem_refresh():
    """SMART failure must not invalidate a successful filesystem collection."""
    app, _ = build_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        view = app.query_one(StorageView)
        assert view.snapshot is not None
        previous_time = view.snapshot.collected_at

        fresh_time = datetime.now(UTC)
        filesystem = FilesystemInfo(
            device="/dev/audit1",
            mountpoint="/audit",
            filesystem="ext4",
            mount_options="rw",
            total=1000,
            used=200,
            free=800,
            usage_percent=20.0,
        )

        app.storage_provider.snapshot = StorageSnapshot(
            filesystems=(filesystem,),
            collected_at=fresh_time,
        )
        app.disk_health_provider.fail = True

        worker = app.refresh_storage()
        await pilot.pause()
        await pilot.pause()

        assert worker.state == WorkerState.SUCCESS
        assert app.is_running

        assert view.snapshot is not None
        assert view.snapshot.collected_at == fresh_time
        assert view.snapshot.collected_at != previous_time
        assert view.snapshot.filesystems == (filesystem,)
        assert view.snapshot.error is None
        assert view.snapshot.stale is False
        assert view.snapshot.stale_error is None

        assert view.disk_snapshot is not None
        assert view.disk_snapshot.available is False
        assert "simulated capability failure" in (
            view.disk_snapshot.error or ""
        )

        # A subsequent successful SMART read restores availability.
        app.disk_health_provider.fail = False
        recovered = app.refresh_storage()
        await pilot.pause()
        await pilot.pause()

        assert recovered.state == WorkerState.SUCCESS
        assert app.is_running
        assert view.snapshot is not None
        assert view.snapshot.stale is False
        assert view.disk_snapshot is not None
        assert view.disk_snapshot.available is True



@pytest.mark.asyncio
async def test_storage_stale_survives_smart_refresh_and_mode_switch():
    """SMART and navigation must not hide a stale filesystem snapshot."""
    app, _ = build_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        view = app.query_one(StorageView)
        assert view.snapshot is not None
        assert view.snapshot.error is None

        # The filesystem provider returns an error snapshot, while
        # SMART continues to collect successfully.
        app.storage_provider.snapshot = StorageSnapshot(
            filesystems=(),
            collected_at=datetime.now(UTC),
            error="simulated filesystem outage",
        )

        worker = app.refresh_storage()
        await pilot.pause()
        await pilot.pause()

        assert worker.state == WorkerState.SUCCESS
        assert app.is_running
        assert view.snapshot is not None
        assert view.snapshot.stale is True
        assert view.snapshot.stale_error == "simulated filesystem outage"
        assert view.disk_snapshot is not None
        assert view.disk_snapshot.available is True

        health = app.query_one("#storage-health")
        status = app.query_one("#storage-status")

        # The successful SMART refresh must not overwrite STALE.
        assert "STALE" in str(health.content)
        assert "simulated filesystem outage" in str(status.content)

        # The warning must survive both storage sub-navigation modes.
        view.set_mode("disks")
        await pilot.pause()
        assert "STALE" in str(health.content)

        view.set_mode("files")
        await pilot.pause()
        assert "STALE" in str(health.content)
        assert "simulated filesystem outage" in str(status.content)

        # A successful filesystem refresh clears the stale condition.
        app.storage_provider.snapshot = StorageSnapshot(
            filesystems=(),
            collected_at=datetime.now(UTC),
        )
        recovered = app.refresh_storage()
        await pilot.pause()
        await pilot.pause()

        assert recovered.state == WorkerState.SUCCESS
        assert view.snapshot is not None
        assert view.snapshot.stale is False
        assert "STALE" not in str(health.content)
        assert "simulated filesystem outage" not in str(status.content)



@pytest.mark.asyncio
async def test_storage_exception_warning_survives_mode_switch():
    """Navigating Storage must not hide a failed filesystem collection."""
    app, providers = build_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        view = app.query_one(StorageView)
        previous = view.snapshot
        assert previous is not None

        health = app.query_one("#storage-health")
        status = app.query_one("#storage-status")

        providers["storage"].fail = True
        failed = app.refresh_storage()

        await pilot.pause()
        await pilot.pause()

        assert failed.state == WorkerState.ERROR
        assert app.is_running
        assert view.snapshot is previous
        assert "STALE" in str(health.content)
        assert "simulated capability failure" in str(status.content)

        view.set_mode("disks")
        await pilot.pause()

        assert "STALE" in str(health.content)
        assert "simulated capability failure" in str(status.content)

        view.set_mode("files")
        await pilot.pause()

        assert "STALE" in str(health.content)
        assert "simulated capability failure" in str(status.content)

        providers["storage"].fail = False
        recovered = app.refresh_storage()

        await pilot.pause()
        await pilot.pause()

        assert recovered.state == WorkerState.SUCCESS
        assert app.is_running
        assert "STALE" not in str(health.content)
        assert "simulated capability failure" not in str(status.content)



@pytest.mark.asyncio
async def test_host_exception_updates_connectivity_and_recovers():
    """Host collection exceptions must be visible in node connectivity."""
    app, _ = build_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        node_id = app.active_node_id
        view = app.query_one(HostView)
        previous = view.snapshot

        assert previous is not None
        assert previous.error is None
        assert app.node_connectivity.get(node_id).status == "online"

        host = app.active_node.host
        host.fail = True
        failed = app.refresh_host()

        await pilot.pause()
        await pilot.pause()

        assert failed.state == WorkerState.ERROR
        assert app.is_running
        assert view.snapshot is previous

        # The last metrics remain available, but must not appear healthy.
        health = str(app.query_one("#host-health").content)
        assert "STALE" in health

        connectivity = app.node_connectivity.get(node_id)
        assert connectivity.status == "offline"
        assert "simulated capability failure" in (
            connectivity.error or ""
        )

        host.fail = False
        recovered = app.refresh_host()

        await pilot.pause()
        await pilot.pause()

        assert recovered.state == WorkerState.SUCCESS
        assert app.is_running
        assert app.node_connectivity.get(node_id).status == "online"
        assert app.node_connectivity.get(node_id).error is None
        assert "STALE" not in str(
            app.query_one("#host-health").content
        )
