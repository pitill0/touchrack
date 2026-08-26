from __future__ import annotations

from typing import TYPE_CHECKING, cast

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from homelab_console.nodes.runtime import NodeRuntime
from homelab_console.nodes.connectivity import NodeConnectivity

if TYPE_CHECKING:
    from homelab_console.app import HomelabConsole


class NodeSelectorScreen(ModalScreen[None]):
    """Touch-friendly selector for the configured monitoring contexts."""

    PAGE_SIZE = 3

    def __init__(
        self,
        runtimes: tuple[NodeRuntime, ...],
        *,
        active_node_id: str,
        connectivity: dict[str, NodeConnectivity] | None = None,
    ) -> None:
        super().__init__()
        self.runtimes = runtimes
        self.active_node_id = active_node_id
        self.connectivity = connectivity or {}
        active_index = next(
            (
                index
                for index, runtime in enumerate(runtimes)
                if runtime.node.id == active_node_id
            ),
            0,
        )
        self.page = active_index // self.PAGE_SIZE

    @property
    def page_count(self) -> int:
        return max(1, (len(self.runtimes) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)

    def compose(self) -> ComposeResult:
        with Vertical(id="node-selector-dialog"):
            with Horizontal(id="node-selector-header"):
                yield Label("SELECT NODE", id="node-selector-title")
                yield Static(
                    f"{len(self.runtimes)} configured",
                    id="node-selector-count",
                )
            with Vertical(id="node-selector-list"):
                for runtime in self.runtimes:
                    node = runtime.node
                    active = node.id == self.active_node_id
                    state = self.connectivity.get(node.id, NodeConnectivity())
                    suffix = {
                        "online": " · UP",
                        "offline": " · DOWN",
                        "unknown": " · --",
                    }[state.status]
                    label = f"{'● ' if active else ''}{node.title}{suffix}"
                    yield Button(
                        label,
                        id=f"node-option-{node.id}",
                        classes="node-option active" if active else "node-option",
                        disabled=active,
                    )
            with Horizontal(id="node-selector-pagination"):
                yield Button("PREV", id="node-selector-prev")
                yield Static("1 / 1", id="node-selector-page")
                yield Button("NEXT", id="node-selector-next")
            yield Button("CANCEL", id="node-selector-cancel")

    def on_mount(self) -> None:
        self._render_page()

    def _render_page(self) -> None:
        self.page = min(max(0, self.page), self.page_count - 1)
        start = self.page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE
        for index, runtime in enumerate(self.runtimes):
            self.query_one(f"#node-option-{runtime.node.id}", Button).display = (
                start <= index < end
            )
        self.query_one("#node-selector-page", Static).update(
            f"{self.page + 1} / {self.page_count}"
        )
        self.query_one("#node-selector-prev", Button).disabled = self.page == 0
        self.query_one("#node-selector-next", Button).disabled = (
            self.page >= self.page_count - 1
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "node-selector-cancel":
            self.dismiss()
            return
        if button_id == "node-selector-prev":
            self.page -= 1
            self._render_page()
            return
        if button_id == "node-selector-next":
            self.page += 1
            self._render_page()
            return
        if not button_id.startswith("node-option-"):
            return

        node_id = button_id.removeprefix("node-option-")
        app = cast("HomelabConsole", self.app)
        app.select_node(node_id)
        self.dismiss()
