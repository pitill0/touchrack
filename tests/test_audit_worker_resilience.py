"""Regression tests for Textual worker resilience."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from textual.worker import WorkerState

from homelab_console.app import HomelabConsole
from homelab_console.models import HostSnapshot
from homelab_console.screens import HostView


class FailingHostProvider:
    def __init__(self) -> None:
        self.fail = False
        self.calls = 0

    async def collect(self) -> HostSnapshot:
        self.calls += 1

        if self.fail:
            raise RuntimeError("simulated host collection failure")

        return HostSnapshot(
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
            collected_at=datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_host_worker_failure_does_not_terminate_app_and_recovers():
    provider = FailingHostProvider()

    app = HomelabConsole(
        provider=provider,
        refresh_seconds=60,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.pause()

        view = app.query_one(HostView)
        assert view.snapshot is not None

        # Deliberately trigger an unhandled provider exception.
        provider.fail = True
        failed_worker = app.refresh_host()

        await pilot.pause()
        await pilot.pause()

        assert failed_worker.state == WorkerState.ERROR
        assert app.is_running

        # The original valid snapshot must remain available.
        assert view.snapshot is not None
        assert view.snapshot.hostname == "audit-host"

        # A subsequent refresh must still be possible.
        provider.fail = False
        recovered_worker = app.refresh_host()

        await pilot.pause()
        await pilot.pause()

        assert recovered_worker.state == WorkerState.SUCCESS
        assert app.is_running
        assert provider.calls >= 3
        assert view.snapshot.hostname == "audit-host"
