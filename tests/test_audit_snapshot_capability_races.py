"""Prevent obsolete publications from all capability refresh workers."""

import asyncio
from datetime import UTC, datetime

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.models import (
    ContainersSnapshot,
    DiskHealthSnapshot,
    HostSnapshot,
    StorageSnapshot,
)
from homelab_console.screens import ContainersView, ServicesView, StorageView
from homelab_console.services import ServicesSnapshot


def make_snapshot(capability, label, error=None):
    collected_at = datetime(
        {"initial": 2023, "obsolete": 2024, "fresh": 2025}[label],
        1, 1, tzinfo=UTC,
    )

    if capability == "containers":
        return ContainersSnapshot(
            containers=(),
            collected_at=collected_at,
            engine=label,
            error=error,
        )

    if capability == "storage":
        return StorageSnapshot(
            filesystems=(),
            collected_at=collected_at,
            error=error,
        )

    if capability == "services":
        return ServicesSnapshot(
            services=(),
            collected_at=collected_at,
            error=error,
        )

    raise AssertionError(f"Unknown capability: {capability}")


class FixedProvider:
    def __init__(self, value):
        self.value = value

    async def collect(self):
        return self.value


class SwitchableProvider:
    def __init__(self, delegate):
        self.delegate = delegate

    async def collect(self):
        return await self.delegate.collect()


class DelayedProvider:
    def __init__(self, capability, obsolete_error):
        self.capability = capability
        self.obsolete_error = obsolete_error
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.new_started = asyncio.Event()

    async def collect(self):
        self.calls += 1

        if self.calls > 1:
            self.new_started.set()
            return make_snapshot(self.capability, "fresh")

        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            return make_snapshot(
                self.capability, "obsolete", self.obsolete_error
            )

        raise AssertionError("Unexpected provider completion")


def build_app():
    now = datetime(2025, 1, 1, tzinfo=UTC)
    host = FixedProvider(
        HostSnapshot(
            hostname="local",
            uptime_seconds=100,
            cpu_percent=12,
            load_average=(0.1, 0.2, 0.3),
            memory_used=1,
            memory_total=2,
            disk_used=1,
            disk_total=2,
            temperature_c=40,
            ip_address=None,
            collected_at=now,
        )
    )

    providers = {
        capability: SwitchableProvider(
            FixedProvider(make_snapshot(capability, "initial"))
        )
        for capability in ("containers", "storage")
    }

    disk = FixedProvider(
        DiskHealthSnapshot(
            disks=(),
            collected_at=now,
            available=True,
        )
    )

    app = HomelabConsole(
        provider=host,
        containers_provider=providers["containers"],
        storage_provider=providers["storage"],
        disk_health_provider=disk,
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )
    return app, providers


@pytest.mark.asyncio
@pytest.mark.parametrize("obsolete_error", [None, "obsolete timeout"])
@pytest.mark.parametrize(
    ("capability", "view_type"),
    [
        ("containers", ContainersView),
        ("storage", StorageView),
        ("services", ServicesView),
    ],
)
async def test_replaced_capability_never_publishes_obsolete_snapshot(
    capability, view_type, obsolete_error, monkeypatch
):
    app, providers = build_app()

    async with app.run_test(size=(64, 18)) as pilot:
        # Finish automatic startup refreshes before injecting the race.
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        delayed = DelayedProvider(capability, obsolete_error)

        if capability == "services":
            manager = app._services_manager(app.active_node)
            monkeypatch.setattr(manager, "collect", delayed.collect)
        else:
            providers[capability].delegate = delayed

        view = app.query_one(view_type)
        published = []
        original_show = view.show_snapshot

        def observe(snapshot):
            published.append(snapshot)
            return original_show(snapshot)

        monkeypatch.setattr(view, "show_snapshot", observe)

        # Start an old request; a new request cancels it.
        old_worker = getattr(app, f"refresh_{capability}")()
        await asyncio.wait_for(delayed.started.wait(), timeout=5)

        new_worker = getattr(app, f"refresh_{capability}")()

        await asyncio.wait_for(delayed.cancelled.wait(), timeout=5)
        await asyncio.wait_for(delayed.new_started.wait(), timeout=5)
        await pilot.pause()
        await pilot.pause()

        fresh = make_snapshot(capability, "fresh")
        obsolete = make_snapshot(
            capability, "obsolete", obsolete_error
        )

        assert old_worker is not None
        assert new_worker is not None
        assert delayed.calls == 2

        # No obsolete publication is permitted, even transiently.
        assert obsolete not in published, published
        assert published, "Expected a fresh snapshot publication"
        assert published[-1] == fresh
        assert view.snapshot == fresh
        assert view.snapshot.error is None

        if capability == "containers":
            assert app._container_snapshots["local"] == fresh

        assert app.is_running



class RaiseAfterCancellation:
    """Old worker raises on cancellation; replacement succeeds."""

    def __init__(self, capability):
        self.capability = capability
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.new_started = asyncio.Event()

    async def collect(self):
        self.calls += 1

        if self.calls > 1:
            self.new_started.set()
            return make_snapshot(self.capability, "fresh")

        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise RuntimeError(
                f"obsolete-{self.capability}-exception"
            ) from None

        raise AssertionError("Unexpected completion")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "view_type"),
    [
        ("containers", ContainersView),
        ("storage", StorageView),
        ("services", ServicesView),
    ],
)
async def test_cancelled_capability_exception_cannot_pollute_new_result(
    capability, view_type, monkeypatch
):
    app, providers = build_app()

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()
        await pilot.pause()

        delayed = RaiseAfterCancellation(capability)

        if capability == "services":
            manager = app._services_manager(app.active_node)
            monkeypatch.setattr(manager, "collect", delayed.collect)
        else:
            providers[capability].delegate = delayed

        view = app.query_one(view_type)

        published = []
        errors = []
        original_show = view.show_snapshot
        original_error = view.show_refresh_error

        def observe_snapshot(snapshot):
            published.append(snapshot)
            return original_show(snapshot)

        def observe_error(*args, **kwargs):
            errors.append((args, kwargs))
            return original_error(*args, **kwargs)

        monkeypatch.setattr(view, "show_snapshot", observe_snapshot)
        monkeypatch.setattr(view, "show_refresh_error", observe_error)

        old_worker = getattr(app, f"refresh_{capability}")()
        await asyncio.wait_for(delayed.started.wait(), timeout=5)

        new_worker = getattr(app, f"refresh_{capability}")()
        await asyncio.wait_for(delayed.cancelled.wait(), timeout=5)
        await asyncio.wait_for(delayed.new_started.wait(), timeout=5)

        await pilot.pause()
        await pilot.pause()

        fresh = make_snapshot(capability, "fresh")

        assert old_worker is not None
        assert new_worker is not None
        assert delayed.calls == 2
        assert errors == [], errors
        assert published
        assert published[-1] == fresh
        assert view.snapshot == fresh
        assert view.snapshot.error is None

        if capability == "containers":
            assert app._container_snapshots["local"] == fresh

        assert app.is_running
