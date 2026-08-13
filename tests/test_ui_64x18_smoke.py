from datetime import datetime, timezone

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.models import HostSnapshot


class SmokeHostProvider:
    async def collect(self) -> HostSnapshot:
        return HostSnapshot(
            hostname="smoke-host",
            uptime_seconds=3600,
            cpu_percent=10.0,
            load_average=(0.1, 0.1, 0.1),
            memory_used=4 * 1024**3,
            memory_total=16 * 1024**3,
            disk_used=100 * 1024**3,
            disk_total=500 * 1024**3,
            temperature_c=42.0,
            ip_address="127.0.0.1",
            collected_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_physical_64x18_ui_and_modals_mount() -> None:
    """Catch TCSS/parser and modal regressions at the physical target size."""
    app = HomelabConsole(
        provider=SmokeHostProvider(),
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        assert app.screen.has_class("compact-touch")

        await pilot.click("#menu-button")
        await pilot.pause()
        assert app.screen.query_one("#menu-dialog")

        await pilot.click("#menu-diagnostics")
        await pilot.pause()
        assert app.screen.query_one("#diagnostics-dialog")

        await pilot.click("#diagnostics-back")
        await pilot.pause()
