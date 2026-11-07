"""Regression probe for delayed results after node A -> B -> A."""

import asyncio
from datetime import UTC, datetime

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.models import HostSnapshot
from homelab_console.nodes import Node, NodeRegistry, NodeRuntime
from homelab_console.providers import (
    UnavailableContainersProvider,
    UnavailableDiskHealthProvider,
    UnavailableStorageProvider,
)
from homelab_console.screens import HostView


def snapshot(name: str, *, error: str | None = None) -> HostSnapshot:
    return HostSnapshot(
        hostname=name,
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


class DelayedFirstProvider:
    def __init__(self, obsolete_error: str | None):
        self.obsolete_error = obsolete_error
        self.calls = 0
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release_old = asyncio.Event()
        self.old_finished = asyncio.Event()
        self.new_started = asyncio.Event()

    async def collect(self):
        self.calls += 1

        if self.calls > 1:
            self.new_started.set()
            return snapshot("a-fresh")

        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()

            # Emulate a provider that continues despite repeated
            # cancellation requests from exclusive workers.
            while not self.release_old.is_set():
                try:
                    await self.release_old.wait()
                except asyncio.CancelledError:
                    continue

            try:
                return snapshot("a-obsolete", error=self.obsolete_error)
            finally:
                self.old_finished.set()

        raise AssertionError("unexpected collection completion")


class FixedProvider:
    async def collect(self):
        return snapshot("b-fresh")


def runtime(node_id, provider):
    reason = "Capability not available in isolated test"
    return NodeRuntime(
        node=Node(
            id=node_id,
            title=node_id.upper(),
            transport="local" if node_id == "a" else "ssh",
        ),
        host=provider,
        containers=UnavailableContainersProvider(reason),
        storage=UnavailableStorageProvider(reason),
        disk_health=UnavailableDiskHealthProvider(reason),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("obsolete_error", [None, "obsolete timeout"])
async def test_obsolete_a_result_after_a_b_a_cannot_override_new_a(
    obsolete_error: str | None,
):
    first = DelayedFirstProvider(obsolete_error)
    app = HomelabConsole(
        nodes=NodeRegistry(
            (runtime("a", first), runtime("b", FixedProvider())),
            default_node_id="a",
        ),
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await asyncio.wait_for(first.started.wait(), timeout=5)

        app.select_node("b")
        await asyncio.wait_for(first.cancelled.wait(), timeout=5)
        await pilot.pause()

        app.select_node("a")
        await asyncio.wait_for(first.new_started.wait(), timeout=5)
        await pilot.pause()

        current = app.query_one(HostView).snapshot
        assert current is not None
        assert current.hostname == "a-fresh"
        assert current.stale is False
        assert app.node_connectivity.get("a").status == "online"

        # Release the cancelled, older A operation only after A is fresh.
        first.release_old.set()
        await asyncio.wait_for(first.old_finished.wait(), timeout=5)
        await pilot.pause()
        await pilot.pause()

        final = app.query_one(HostView).snapshot
        assert final is not None
        assert final.hostname == "a-fresh"
        assert final.stale is False
        assert final.stale_error is None
        assert app.node_connectivity.get("a").status == "online"
        assert app.node_connectivity.get("a").error is None
        assert first.calls == 2
