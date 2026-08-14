from __future__ import annotations

from datetime import datetime, timezone
from math import ceil

from rich.text import Text

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from homelab_console.services import ServiceState, ServicesSnapshot


_STATUS_MARK = {
    "OK": "● OK",
    "IDLE": "○ IDLE",
    "WARN": "● WARN",
    "ERROR": "● ERR",
    "UNKNOWN": "? UNK",
}

_STATUS_STYLE = {
    "OK": "bold bright_green",
    "IDLE": "dim",
    "WARN": "bold yellow",
    "ERROR": "bold red",
    "UNKNOWN": "dim",
}


class ServiceCard(Button):
    """Persistent slot in the compact 3×4 service-health matrix."""

    def __init__(self, index: int) -> None:
        super().__init__(
            "",
            id=f"service-open-{index}",
            classes="service-card service-card-empty",
            disabled=True,
        )
        self.state: ServiceState | None = None

    def set_state(self, state: ServiceState | None) -> None:
        self.remove_class(
            "status-ok",
            "status-idle",
            "status-warn",
            "status-error",
            "status-unknown",
            "service-card-empty",
        )
        self.state = state
        if state is None:
            self.label = ""
            self.disabled = True
            self.add_class("service-card-empty")
            return

        # The full title and provider data are available in the detail modal.
        # Keep the overview compact enough for three touch targets per row.
        title = state.title.upper()[:11]
        status = _STATUS_MARK.get(state.status, "? UNK")
        style = _STATUS_STYLE.get(state.status, "dim")
        self.label = Text.assemble(title, " ", (status, style))
        self.disabled = False
        self.add_class(f"status-{state.status.lower()}")


class ServiceDetailScreen(ModalScreen[None]):
    """Full service information reached by touching a matrix entry."""

    def __init__(self, state: ServiceState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        with Vertical(id="service-detail-dialog"):
            with Horizontal(id="service-detail-header"):
                yield Button("BACK", id="service-detail-back", classes="detail-back")
                with Vertical(id="service-detail-title-block"):
                    yield Label(self.state.title.upper(), id="service-detail-title")
                    yield Label(
                        f"{self.state.provider} · {self.state.group}",
                        id="service-detail-provider",
                    )
                yield Static(
                    _STATUS_MARK.get(self.state.status, "? UNK"),
                    id="service-detail-state",
                    classes=f"status-{self.state.status.lower()}",
                )
            detail_rows = self.state.details or (
                ("PRIMARY", self.state.primary),
                ("DETAIL", self.state.secondary),
                ("TARGET", self.state.target),
            )
            for label, value in detail_rows:
                yield Static(
                    f"{label[:10]:<10}  {value}",
                    classes="service-detail-line",
                )
            yield Static(
                f"{'CHECKED':<10}  {_format_age(self.state.checked_at)}",
                classes="service-detail-line",
            )
            if self.state.error:
                yield Static(
                    f"{'ERROR':<10}  {self.state.error}",
                    classes="service-detail-line service-detail-error",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "service-detail-back":
            self.dismiss()


class ServicesView(Vertical):
    PAGE_SIZE = 12

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.snapshot: ServicesSnapshot | None = None
        self._visible_states: tuple[ServiceState, ...] = ()
        self._page_states: tuple[ServiceState, ...] = ()
        self._page = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="services-header"):
            yield Button("≡", id="menu-button-services", classes="flat-control")
            yield Label("SERVICES", id="services-title")
            yield Static("● --", id="services-health")
            yield Button(
                "REF 5s",
                id="refresh-interval-services",
                classes="refresh-interval-button flat-control",
            )
            yield Button(
                "SLEEP 1m",
                id="screen-blank-services",
                classes="screen-blank-button flat-control",
            )

        # Healthy: this is an intentional blank breathing row.
        # Warning/error/config state: the same row becomes contextual status.
        yield Static("", id="services-status")

        with Grid(id="services-grid"):
            for index in range(self.PAGE_SIZE):
                yield ServiceCard(index)

        with Horizontal(id="services-pager", classes="pager-hidden"):
            yield Button(
                "◀",
                id="services-page-prev",
                classes="services-page-button flat-control",
            )
            yield Static("1 / 1", id="services-page-indicator")
            yield Button(
                "▶",
                id="services-page-next",
                classes="services-page-button flat-control",
            )

    def show_refreshing(self) -> None:
        if self.snapshot is None:
            status = self.query_one("#services-status", Static)
            status.update("Loading configured services…")
            status.remove_class("has-error", "has-warning")

    def show_snapshot(self, snapshot: ServicesSnapshot) -> None:
        self.snapshot = snapshot
        self._visible_states = snapshot.services

        if self._page >= self._page_count:
            self._page = max(0, self._page_count - 1)
        self._render_page()

        status = self.query_one("#services-status", Static)
        status.remove_class("has-error", "has-warning")
        health = self.query_one("#services-health", Static)
        health.remove_class("healthy", "degraded", "unavailable")

        if snapshot.error:
            status.update(snapshot.error)
            status.add_class("has-error")
            health.update("● DOWN")
            health.add_class("unavailable")
        elif not snapshot.services:
            path = snapshot.config_path or "services.yaml"
            status.update(f"No services configured · {path}")
            status.add_class("has-warning")
            health.update("● WARN")
            health.add_class("degraded")
        else:
            errors = sum(state.status == "ERROR" for state in snapshot.services)
            warnings = sum(state.status == "WARN" for state in snapshot.services)
            if errors:
                status.update(f"{len(snapshot.services)} visible · {errors} error")
                status.add_class("has-error")
                health.update("● DOWN")
                health.add_class("unavailable")
            elif warnings:
                status.update(f"{len(snapshot.services)} visible · {warnings} warning")
                status.add_class("has-warning")
                health.update("● WARN")
                health.add_class("degraded")
            else:
                status.update("")
                health.update("● OK")
                health.add_class("healthy")

    def update_snapshot_age(self) -> None:
        return

    @property
    def _page_count(self) -> int:
        if not self._visible_states:
            return 1
        return max(1, ceil(len(self._visible_states) / self.PAGE_SIZE))

    def _render_page(self) -> None:
        start = self._page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        self._page_states = self._visible_states[start:end]

        for index in range(self.PAGE_SIZE):
            card = self.query_one(f"#service-open-{index}", ServiceCard)
            card.set_state(
                self._page_states[index] if index < len(self._page_states) else None
            )

        pager = self.query_one("#services-pager", Horizontal)
        indicator = self.query_one("#services-page-indicator", Static)
        prev_button = self.query_one("#services-page-prev", Button)
        next_button = self.query_one("#services-page-next", Button)

        page_count = self._page_count
        indicator.update(f"{self._page + 1} / {page_count}")
        pager.set_class(page_count <= 1, "pager-hidden")
        prev_button.disabled = self._page <= 0
        next_button.disabled = self._page >= page_count - 1

    def page_previous(self) -> None:
        if self._page <= 0:
            return
        self._page -= 1
        self._render_page()

    def page_next(self) -> None:
        if self._page >= self._page_count - 1:
            return
        self._page += 1
        self._render_page()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "services-page-prev":
            self.page_previous()
            event.stop()
        elif event.button.id == "services-page-next":
            self.page_next()
            event.stop()

    def state_for_button(self, button_id: str) -> ServiceState | None:
        try:
            index = int(button_id.removeprefix("service-open-"))
        except ValueError:
            return None
        if 0 <= index < len(self._page_states):
            return self._page_states[index]
        return None


def _format_age(checked_at: datetime) -> str:
    elapsed = max(0, int((datetime.now(timezone.utc) - checked_at).total_seconds()))
    return f"{elapsed}s ago"
