import asyncio
from datetime import datetime, timezone

import pytest

from textual.widgets import Button

from homelab_console.app import HomelabConsole
from homelab_console.models import ContainerInfo, ContainersSnapshot, HostSnapshot
from homelab_console.providers.containers import PodmanContainersProvider

from homelab_console.screens import ContainersView
from homelab_console.screens.containers import ResourceRankingRow

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



class ManyContainersProvider:
    async def collect(self) -> ContainersSnapshot:
        return ContainersSnapshot(
            containers=tuple(
                ContainerInfo(
                    container_id=f"id-{index}",
                    name=f"container-{index:02d}",
                    image="localhost/example:latest",
                    state="running",
                    status="Up",
                    cpu_percent=f"{index + 1}.00%",
                    memory_percent=f"{index + 2}.00%",
                )
                for index in range(12)
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
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.click("#nav-containers")
        await pilot.pause()

        view_button = app.query_one("#containers-view-button")
        assert str(view_button.label) == "LIST"
        assert app.screen.region.contains_region(view_button.region)

        # Current summary UI exposes totals through the compact context/status
        # and the three btop-style total cells.
        # Healthy totals are shown by the large RUNNING / STOPPED / TOTAL cells;
        # the context status line is reserved for warning/error messages.
        assert str(app.query_one("#containers-status").content) == ""
        totals = [str(widget.content) for widget in app.query(".btop-total")]
        assert "1\nRUNNING" in totals
        assert "1\nSTOPPED" in totals
        assert "2\nTOTAL" in totals

        # LIST mode is the current row-oriented container view.
        await pilot.click("#containers-view-button")
        await pilot.pause()
        back_button = app.query_one("#containers-view-button")
        assert str(back_button.label) == "BACK"
        assert app.screen.region.contains_region(back_button.region)
        assert len(app.query(".compact-container-row")) == 2
        assert str(app.query_one("#containers-page-indicator").content) == "1/1"
        assert app.query_one("#containers-page-prev", Button).disabled
        assert app.query_one("#containers-page-next", Button).disabled


def test_resource_ranking_bar_uses_compact_seven_cell_meter() -> None:
    row = ResourceRankingRow("svc-cadvisor", "50.00%", maximum=100.0)
    children = list(row.compose())

    assert str(children[0].content) == "svc-cadvisor"
    assert len(str(children[1].content)) == 7
    assert str(children[2].content) == "50.00%"


@pytest.mark.asyncio
async def test_containers_view_shows_detected_engine_and_menu() -> None:
    app = HomelabConsole(
        provider=FakeHostProvider(),
        containers_provider=FakeContainersProvider(),
        refresh_seconds=3600,
        containers_refresh_seconds=3600,
        touch_enabled=False,
    )
    async with app.run_test(size=(64, 18)) as pilot:
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



@pytest.mark.asyncio
async def test_container_cli_cancellation_kills_and_reaps_process(monkeypatch) -> None:
    started = asyncio.Event()
    released = asyncio.Event()

    class FakeProcess:
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.communicate_calls = 0

        async def communicate(self):
            self.communicate_calls += 1
            if self.killed:
                self.returncode = -9
                return b"", b""
            started.set()
            await released.wait()
            return b"[]", b""

        def kill(self) -> None:
            self.killed = True
            released.set()

    process = FakeProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = PodmanContainersProvider(timeout_seconds=30)
    task = asyncio.create_task(provider._run("ps"))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed is True
    assert process.communicate_calls >= 2




@pytest.mark.asyncio
async def test_touch_paging_and_nav_hit_test_with_many_containers() -> None:
    app = HomelabConsole(
        provider=FakeHostProvider(),
        containers_provider=ManyContainersProvider(),
        refresh_seconds=3600,
        containers_refresh_seconds=3600,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.click("#nav-containers")
        await pilot.pause()
        await pilot.click("#containers-view-button")
        await pilot.pause()

        assert str(app.query_one("#containers-view-button").label) == "BACK"
        assert str(app.query_one("#containers-page-indicator").content) == "1/3"
        assert app.query_one("#containers-page-prev", Button).disabled
        assert not app.query_one("#containers-page-next", Button).disabled
        assert len(app.query(".compact-container-row")) == 4
        assert app.query_one("#container-open-0-name")
        assert app.query_one("#container-open-3-name")

        await pilot.click("#containers-page-next")
        await pilot.pause()

        assert str(app.query_one("#containers-page-indicator").content) == "2/3"
        assert not app.query_one("#containers-page-prev", Button).disabled
        assert app.query_one("#container-open-4-name")
        assert app.query_one("#container-open-7-name")

        # The page controls use normal Button.Pressed events, so the same
        # controls are reachable from the physical evdev bridge.
        nav_host = app.query_one("#nav-host")
        x = nav_host.region.x + nav_host.region.width // 2
        y = nav_host.region.y + nav_host.region.height // 2
        widget = app._widget_at(x, y)
        assert widget is nav_host

        assert isinstance(widget, Button)
        widget.press()
        await pilot.pause()

        assert app.active_section == "host"
        assert not app.query_one("#view-host").has_class("hidden")
        assert app.query_one("#view-containers").has_class("hidden")


@pytest.mark.asyncio
async def test_container_touch_pager_reaches_last_page_and_keeps_detail_mapping() -> None:
    app = HomelabConsole(
        provider=FakeHostProvider(),
        containers_provider=ManyContainersProvider(),
        refresh_seconds=3600,
        containers_refresh_seconds=3600,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.click("#nav-containers")
        await pilot.pause()
        await pilot.click("#containers-view-button")
        await pilot.pause()

        await pilot.click("#containers-page-next")
        await pilot.pause()
        await pilot.click("#containers-page-next")
        await pilot.pause()

        assert str(app.query_one("#containers-page-indicator").content) == "3/3"
        assert app.query_one("#containers-page-next", Button).disabled
        assert app.query_one("#container-open-8-name")
        assert app.query_one("#container-open-11-name")
        assert app.query_one(ContainersView).container_for_button(
            "container-open-8-name"
        ).name == "container-08"



@pytest.mark.asyncio
async def test_container_list_layout_separates_back_table_and_pager() -> None:
    app = HomelabConsole(
        provider=FakeHostProvider(),
        containers_provider=ManyContainersProvider(),
        refresh_seconds=3600,
        containers_refresh_seconds=3600,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.click("#nav-containers")
        await pilot.pause()
        await pilot.click("#containers-view-button")
        await pilot.pause()

        back = app.query_one("#containers-view-button")
        header = app.query_one("#containers-header")
        name_header = app.query_one("#container-sort-name")
        state_header = app.query_one("#container-sort-state")
        cpu_header = app.query_one("#container-sort-cpu")
        memory_header = app.query_one("#container-sort-memory")
        pager = app.query_one(".containers-page-footer")
        navigation = app.query_one("#navigation")
        rows = list(app.query(".compact-container-row"))

        assert len(rows) == 4
        assert str(back.label) == "BACK"
        assert header.region.contains_region(back.region)
        assert back.region.bottom <= name_header.region.y
        assert name_header.region.right <= state_header.region.x
        assert state_header.region.right <= cpu_header.region.x
        assert cpu_header.region.right <= memory_header.region.x
        assert pager.region.y >= max(row.region.bottom for row in rows)
        assert pager.region.bottom <= navigation.region.y

        name_cell = app.query_one("#container-open-0-name", Button)
        assert name_cell.region.width > state_header.region.width
        assert name_cell.styles.text_wrap == "nowrap"
        assert name_cell.styles.text_overflow == "ellipsis"



@pytest.mark.asyncio
async def test_container_mode_action_stays_in_primary_header() -> None:
    app = HomelabConsole(
        provider=FakeHostProvider(),
        containers_provider=FakeContainersProvider(),
        refresh_seconds=3600,
        containers_refresh_seconds=3600,
        touch_enabled=False,
    )

    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        await pilot.click("#nav-containers")
        await pilot.pause()

        mode = app.query_one("#containers-view-button", Button)
        header = app.query_one("#containers-header")
        assert str(mode.label) == "LIST"
        assert header.region.contains_region(mode.region)

        await pilot.click("#containers-view-button")
        await pilot.pause()

        mode = app.query_one("#containers-view-button", Button)
        assert str(mode.label) == "BACK"
        assert header.region.contains_region(mode.region)
        assert len(app.query("#containers-view-button")) == 1
