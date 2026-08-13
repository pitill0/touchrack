from datetime import datetime, timezone

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.models import HostSnapshot


class FakeProvider:
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
async def test_navigation_is_click_only() -> None:
    app = HomelabConsole(provider=FakeProvider(), refresh_seconds=3600)
    async with app.run_test(size=(100, 37)) as pilot:
        await pilot.pause()
        assert app.active_section == "host"

        await pilot.click("#nav-services")
        assert app.active_section == "services"
        assert app.query_one("#view-host").has_class("hidden")
        assert not app.query_one("#view-services").has_class("hidden")

        await pilot.click("#nav-containers")
        assert app.active_section == "containers"
        assert not app.query_one("#view-containers").has_class("hidden")

        await pilot.click("#nav-host")
        assert app.active_section == "host"
        assert not app.query_one("#view-host").has_class("hidden")


@pytest.mark.asyncio
async def test_compact_menu_opens_and_closes_by_click() -> None:
    app = HomelabConsole(provider=FakeProvider(), refresh_seconds=3600)
    async with app.run_test(size=(100, 37)) as pilot:
        await pilot.pause()
        await pilot.click("#menu-button")
        await pilot.pause()
        assert app.screen.query_one("#menu-dialog")

        await pilot.click("#menu-close")
        await pilot.pause()
        assert app.screen.id != "menu-dialog"
