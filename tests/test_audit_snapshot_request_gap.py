"""Detect stale publication between exclusive refresh request and startup."""

import asyncio
from datetime import UTC, datetime

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.models import HostSnapshot


def make_snapshot(hostname, error=None):
    return HostSnapshot(
        hostname=hostname,
        uptime_seconds=100,
        cpu_percent=12,
        load_average=(0.1, 0.2, 0.3),
        memory_used=1,
        memory_total=2,
        disk_used=1,
        disk_total=2,
        temperature_c=40,
        ip_address=None,
        collected_at=datetime.now(UTC),
        error=error,
    )


class ImmediateOnCancellation:
    def __init__(self, obsolete_error):
        self.obsolete_error = obsolete_error
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.new_started = asyncio.Event()

    async def collect(self):
        self.calls += 1
        if self.calls > 1:
            self.new_started.set()
            return make_snapshot("fresh", None)

        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            return make_snapshot("obsolete", self.obsolete_error)

        raise AssertionError("unexpected completion")


@pytest.mark.asyncio
@pytest.mark.parametrize("obsolete_error", [None, "obsolete timeout"])
async def test_replaced_worker_cannot_publish_before_replacement_starts(
    obsolete_error,
):
    provider = ImmediateOnCancellation(obsolete_error)
    app = HomelabConsole(
        provider=provider,
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await asyncio.wait_for(provider.started.wait(), timeout=5)

        published = []
        original_resolve = app.node_snapshots.resolve

        def observe_resolve(node_id, capability, snapshot):
            published.append(
                (node_id, capability, snapshot.hostname, snapshot.error)
            )
            return original_resolve(node_id, capability, snapshot)

        app.node_snapshots.resolve = observe_resolve

        # The replacement is requested synchronously, but its async
        # function body has not necessarily started yet.
        replacement = app.refresh_host()

        await asyncio.wait_for(provider.cancelled.wait(), timeout=5)
        await asyncio.wait_for(provider.new_started.wait(), timeout=5)
        await pilot.pause()

        assert provider.calls == 2
        assert replacement is not None

        # The obsolete worker must never publish, even transiently.
        assert all(entry[2] != "obsolete" for entry in published), published
        assert published
        assert published[-1][2] == "fresh"
        assert app.node_connectivity.get("local").status == "online"
