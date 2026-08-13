from datetime import datetime, timezone

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.models import ContainerInfo, ContainersSnapshot, HostSnapshot


class FakeHostProvider:
    async def collect(self) -> HostSnapshot:
        return HostSnapshot(
            hostname="test-host",
            uptime_seconds=100,
            cpu_percent=10,
            load_average=(0.1, 0.2, 0.3),
            memory_used=1,
            memory_total=2,
            disk_used=1,
            disk_total=2,
            temperature_c=40,
            ip_address="127.0.0.1",
            collected_at=datetime.now(timezone.utc),
        )


class FakeContainersProvider:
    async def collect(self) -> ContainersSnapshot:
        return ContainersSnapshot(
            containers=(
                ContainerInfo(
                    container_id="abc",
                    name="fluxtuner",
                    image="localhost/fluxtuner:latest",
                    state="running",
                    status="Up 2 hours",
                    cpu_percent="1.20%",
                    memory_percent="2.30%",
                ),
                ContainerInfo(
                    container_id="def",
                    name="old-worker",
                    image="localhost/worker:latest",
                    state="exited",
                    status="Exited (0) 3 days ago",
                ),
            ),
            collected_at=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_containers_view_lists_local_podman_rows() -> None:
    app = HomelabConsole(
        provider=FakeHostProvider(),
        containers_provider=FakeContainersProvider(),
        refresh_seconds=3600,
        containers_refresh_seconds=3600,
        touch_enabled=False,
    )
    async with app.run_test(size=(100, 37)) as pilot:
        await pilot.pause()
        await pilot.click("#nav-containers")
        await pilot.pause()

        # Current summary UI exposes totals through the compact context/status
        # and the three btop-style total cells.
        assert str(app.query_one("#containers-status").content) == (
            "2 containers · 1 running · 1 stopped"
        )
        totals = [str(widget.content) for widget in app.query(".btop-total")]
        assert "1\nRUNNING" in totals
        assert "1\nSTOPPED" in totals
        assert "2\nTOTAL" in totals

        # LIST mode is the current row-oriented container view.
        await pilot.click("#containers-view-button")
        await pilot.pause()
        assert len(app.query(".compact-container-row")) == 2


@pytest.mark.asyncio
async def test_containers_view_shows_detected_engine_and_menu() -> None:
    app = HomelabConsole(
        provider=FakeHostProvider(),
        containers_provider=FakeContainersProvider(),
        refresh_seconds=3600,
        containers_refresh_seconds=3600,
        touch_enabled=False,
    )
    async with app.run_test(size=(100, 37)) as pilot:
        await pilot.pause()
        await pilot.click("#nav-containers")
        await pilot.pause()

        # The engine/read-only label now lives in container detail rather than
        # in the old containers subtitle.
        await pilot.click("#containers-view-button")
        await pilot.pause()
        await pilot.click("#container-open-0-name")
        await pilot.pause()
        assert "Unknown · read-only" in str(
            app.screen.query_one("#container-detail-engine").content
        )

        await pilot.click("#container-detail-back")
        await pilot.pause()
        await pilot.click("#menu-button-containers")
        await pilot.pause()
        assert app.screen.query_one("#menu-dialog")
