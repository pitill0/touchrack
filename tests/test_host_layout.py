from datetime import datetime, timezone

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.models import HostSnapshot


class HostLayoutProvider:
    async def collect(self) -> HostSnapshot:
        return HostSnapshot(
            hostname="test-host",
            uptime_seconds=90061,
            cpu_percent=17.0,
            load_average=(0.1, 0.2, 0.3),
            memory_used=4 * 1024**3,
            memory_total=16 * 1024**3,
            disk_used=100 * 1024**3,
            disk_total=500 * 1024**3,
            temperature_c=45.5,
            ip_address="192.168.1.10",
            collected_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_compact_host_footer_shows_temperature_and_uptime_without_age_ticker() -> None:
    app = HomelabConsole(
        provider=HostLayoutProvider(),
        refresh_seconds=3600,
        screen_blank_minutes=0,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()

        assert str(app.query_one("#temperature-value").content) == "46°C"
        assert str(app.query_one("#temperature-state").content) == "● NORMAL"
        assert str(app.query_one("#host-uptime").content) == "UP 1d 1h"
        assert len(app.query("#host-status")) == 0
