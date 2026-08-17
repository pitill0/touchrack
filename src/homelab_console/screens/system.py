from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from homelab_console import __version__

if TYPE_CHECKING:
    from homelab_console.app import HomelabConsole


class DiagnosticsScreen(ModalScreen[None]):
    """Read-only runtime diagnostics for the physical console."""

    def compose(self) -> ComposeResult:
        app = cast("HomelabConsole", self.app)
        rows = app.diagnostics_rows()
        with Vertical(id="diagnostics-dialog"):
            with Horizontal(id="diagnostics-header"):
                yield Button("BACK", id="diagnostics-back", classes="detail-back")
                yield Label("SYSTEM DIAGNOSTICS", id="diagnostics-title")
            for label, value, state in rows:
                with Horizontal(classes=f"diagnostics-row {state}"):
                    yield Static(label, classes="diagnostics-label")
                    yield Static(value, classes="diagnostics-value")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "diagnostics-back":
            self.dismiss()


class ConsoleMenu(ModalScreen[None]):
    """Compact touch-friendly application menu."""

    def compose(self) -> ComposeResult:
        with Vertical(id="menu-dialog"):
            yield Label("CONSOLE MENU", id="menu-title")
            yield Static(
                f"Local-first homelab console\n"
                f"Host + services + containers · v{__version__}",
                id="menu-about",
            )
            yield Button("REFRESH HOST", id="menu-refresh", classes="menu-action")
            yield Button("DIAGNOSTICS", id="menu-diagnostics", classes="menu-action")
            yield Button("ABOUT", id="menu-about-toggle", classes="menu-action")
            yield Button("QUIT", id="menu-quit", classes="menu-action menu-danger")
            yield Button("CLOSE", id="menu-close", classes="menu-action")

    def on_mount(self) -> None:
        self.query_one("#menu-about", Static).display = False

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "menu-refresh":
            app = cast("HomelabConsole", self.app)
            app.refresh_host()
            self.dismiss()
        elif button_id == "menu-diagnostics":
            self.dismiss()
            self.app.push_screen(DiagnosticsScreen())
        elif button_id == "menu-about-toggle":
            about = self.query_one("#menu-about", Static)
            about.display = not about.display
        elif button_id == "menu-quit":
            self.app.exit()
        elif button_id == "menu-close":
            self.dismiss()
