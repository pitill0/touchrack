from __future__ import annotations

from datetime import datetime, timezone

import pytest

from textual.app import App, ComposeResult

from homelab_console.screens.services import ServicesView
from homelab_console.services import ServiceState, ServicesSnapshot


class ServicesViewApp(App[None]):
    CSS_PATH = "../src/homelab_console/console.tcss"

    def compose(self) -> ComposeResult:
        yield ServicesView(id="view-services")

    def on_mount(self) -> None:
        # HomelabConsole applies this profile automatically at 64x18.
        # This isolated view test must mirror that runtime condition.
        self.screen.add_class("compact-touch")


def _state(
    index: int = 0,
    status: str = "OK",
    *,
    title: str | None = None,
) -> ServiceState:
    return ServiceState(
        id=f"service-{index}",
        title=title or f"Service {index}",
        provider="container",
        target=f"svc-{index}",
        status=status,
        primary=f"CPU {index}.00%",
        secondary="Up",
        checked_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_healthy_services_use_status_row_as_blank_breathing_space() -> None:
    app = ServicesViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(ServicesView)
        view.show_snapshot(
            ServicesSnapshot(
                services=(_state(),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        status = app.query_one("#services-status")
        assert str(status.content) == ""
        assert status.region.height == 1
        assert str(app.query_one("#services-health").content) == "● OK"


@pytest.mark.asyncio
async def test_warning_services_keep_context_status_visible() -> None:
    app = ServicesViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(ServicesView)
        view.show_snapshot(
            ServicesSnapshot(
                services=(_state(status="WARN"),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        status = app.query_one("#services-status")
        assert str(status.content) == "1 visible · 1 warning"
        assert status.has_class("has-warning")
        assert str(app.query_one("#services-health").content) == "● WARN"


@pytest.mark.asyncio
async def test_service_matrix_is_status_only_and_preserves_detail_state() -> None:
    app = ServicesViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(ServicesView)
        state = _state(title="Prometheus")
        view.show_snapshot(
            ServicesSnapshot(
                services=(state,),
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        card = app.query_one("#service-open-0")
        label = str(card.label)
        assert "PROMETHEUS" in label
        assert "OK" in label
        assert "CPU" not in label
        assert view.state_for_button("service-open-0") == state


@pytest.mark.asyncio
async def test_services_paginate_twelve_per_page_with_visible_touch_controls() -> None:
    app = ServicesViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(ServicesView)
        states = tuple(_state(index) for index in range(13))
        view.show_snapshot(
            ServicesSnapshot(
                services=states,
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        pager = app.query_one("#services-pager")
        assert not pager.has_class("pager-hidden")
        assert str(app.query_one("#services-page-indicator").content) == "1 / 2"
        assert view.state_for_button("service-open-11") == states[11]

        next_button = app.query_one("#services-page-next")
        assert app.screen.region.contains_region(next_button.region)
        await pilot.click("#services-page-next")
        await pilot.pause()

        assert str(app.query_one("#services-page-indicator").content) == "2 / 2"
        assert view.state_for_button("service-open-0") == states[12]
        assert app.query_one("#service-open-1").disabled

        prev_button = app.query_one("#services-page-prev")
        assert app.screen.region.contains_region(prev_button.region)
        await pilot.click("#services-page-prev")
        await pilot.pause()
        assert str(app.query_one("#services-page-indicator").content) == "1 / 2"



@pytest.mark.asyncio
async def test_service_detail_renders_provider_specific_rows() -> None:
    from homelab_console.screens.services import ServiceDetailScreen

    state = ServiceState(
        id="prometheus",
        title="Prometheus",
        provider="container",
        target="svc-prometheus",
        status="OK",
        primary="CPU 0.04%",
        secondary="Up 7 days",
        checked_at=datetime.now(timezone.utc),
        details=(
            ("STATE", "RUNNING"),
            ("CPU", "0.04%"),
            ("MEMORY", "0.86%"),
            ("USAGE", "132MiB / 15.4GiB"),
            ("IMAGE", "prom/prometheus:latest"),
            ("CONTAINER", "svc-prometheus"),
        ),
    )

    class DetailApp(App[None]):
        CSS_PATH = "../src/homelab_console/console.tcss"

        def on_mount(self) -> None:
            self.push_screen(ServiceDetailScreen(state))

    app = DetailApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()

        assert isinstance(app.screen, ServiceDetailScreen)
        lines = [
            str(widget.content)
            for widget in app.screen.query(".service-detail-line")
        ]
        joined = "\n".join(lines)

        assert "STATE       RUNNING" in joined
        assert "CPU         0.04%" in joined
        assert "MEMORY      0.86%" in joined
        assert "USAGE       132MiB / 15.4GiB" in joined
        assert "IMAGE       prom/prometheus:latest" in joined
        assert "CONTAINER   svc-prometheus" in joined
        assert "CHECKED" in joined
        back_button = app.screen.query_one("#service-detail-back")
        assert app.screen.region.contains_region(back_button.region)



@pytest.mark.asyncio
async def test_services_config_error_is_distinct_from_service_outage() -> None:
    app = ServicesViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(ServicesView)
        view.show_snapshot(
            ServicesSnapshot(
                services=(),
                collected_at=datetime.now(timezone.utc),
                config_path="/etc/touchrack/services.yaml",
                error="services[0].pinned must be a boolean",
            )
        )
        await pilot.pause()

        status = app.query_one("#services-status")
        health = app.query_one("#services-health")

        assert str(status.content) == (
            "CONFIG · services[0].pinned must be a boolean"
        )
        assert status.has_class("has-config-error")
        assert not status.has_class("has-error")
        assert str(health.content) == "● CFG"
        assert health.has_class("config-error")
        assert not health.has_class("unavailable")


@pytest.mark.asyncio
async def test_service_error_remains_health_outage_not_config_error() -> None:
    app = ServicesViewApp()
    async with app.run_test(size=(64, 18)) as pilot:
        await pilot.pause()
        view = app.query_one(ServicesView)
        view.show_snapshot(
            ServicesSnapshot(
                services=(_state(status="ERROR"),),
                collected_at=datetime.now(timezone.utc),
            )
        )
        await pilot.pause()

        status = app.query_one("#services-status")
        health = app.query_one("#services-health")

        assert str(status.content) == "1 visible · 1 error"
        assert status.has_class("has-error")
        assert not status.has_class("has-config-error")
        assert str(health.content) == "● DOWN"
        assert health.has_class("unavailable")
        assert not health.has_class("config-error")
