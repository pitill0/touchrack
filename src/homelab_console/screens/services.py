from __future__ import annotations

from datetime import datetime, timezone

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
    "ERROR": "● ERROR",
    "UNKNOWN": "? UNKNOWN",
}


class ServiceCard(Button):
    """Persistent service slot.

    The six buttons are mounted once with the view and only their content and
    status classes are updated on refresh. Rebuilding Textual buttons every
    refresh caused visible flashes of the default Button rendering on the
    physical Linux console.
    """

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

        title = state.title.upper()[:18]
        status = _STATUS_MARK.get(state.status, "? UNKNOWN")
        primary = state.primary[:18]
        if state.status == "OK":
            self.label = Text.assemble(
                title,
                "\n",
                (status, "bold bright_green"),
                "   ",
                primary,
            )
        else:
            self.label = f"{title}\n{status}   {primary}"
        self.disabled = False
        self.add_class(f"status-{state.status.lower()}")


class ServiceDetailScreen(ModalScreen[None]):
    def __init__(self, state: ServiceState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        with Vertical(id="service-detail-dialog"):
            with Horizontal(id="service-detail-header"):
                yield Button("BACK", id="service-detail-back", classes="detail-back")
                with Vertical(id="service-detail-title-block"):
                    yield Label(self.state.title.upper(), id="service-detail-title")
                    yield Label(f"{self.state.provider} · {self.state.group}", id="service-detail-provider")
                yield Static(
                    _STATUS_MARK.get(self.state.status, "? UNKNOWN"),
                    id="service-detail-state",
                    classes=f"status-{self.state.status.lower()}",
                )
            yield Static(f"PRIMARY     {self.state.primary}", classes="service-detail-line")
            yield Static(f"DETAIL      {self.state.secondary}", classes="service-detail-line")
            yield Static(f"TARGET      {self.state.target}", classes="service-detail-line")
            yield Static(f"CHECKED     {_format_age(self.state.checked_at)}", classes="service-detail-line")
            if self.state.error:
                yield Static(f"ERROR       {self.state.error}", classes="service-detail-line service-detail-error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "service-detail-back":
            self.dismiss()


class ServicesView(Vertical):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.snapshot: ServicesSnapshot | None = None
        self._visible_states: tuple[ServiceState, ...] = ()

    def compose(self) -> ComposeResult:
        with Horizontal(id="services-header"):
            yield Button("≡", id="menu-button-services", classes="flat-control")
            yield Label("SERVICES", id="services-title")
            yield Static("● --", id="services-health")
            yield Button("REF 5s", id="refresh-interval-services", classes="refresh-interval-button flat-control")
            yield Button("SLEEP 1m", id="screen-blank-services", classes="screen-blank-button flat-control")
        yield Static("Loading configured services…", id="services-status")
        with Grid(id="services-grid"):
            for index in range(6):
                yield ServiceCard(index)

    def show_refreshing(self) -> None:
        if self.snapshot is None:
            self.query_one("#services-status", Static).update("Loading configured services…")

    def show_snapshot(self, snapshot: ServicesSnapshot) -> None:
        self.snapshot = snapshot
        self._visible_states = snapshot.services
        for index in range(6):
            card = self.query_one(f"#service-open-{index}", ServiceCard)
            card.set_state(snapshot.services[index] if index < len(snapshot.services) else None)

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
                status.update(f"{len(snapshot.services)} services · all clear")
                health.update("● OK")
                health.add_class("healthy")

    def update_snapshot_age(self) -> None:
        # SERVICES intentionally keeps its summary stable between refreshes.
        # REF in the common header already communicates cadence.
        return

    def state_for_button(self, button_id: str) -> ServiceState | None:
        try:
            index = int(button_id.removeprefix("service-open-"))
        except ValueError:
            return None
        if 0 <= index < len(self._visible_states):
            return self._visible_states[index]
        return None


def _format_age(checked_at: datetime) -> str:
    elapsed = max(0, int((datetime.now(timezone.utc) - checked_at).total_seconds()))
    return f"{elapsed}s ago"
