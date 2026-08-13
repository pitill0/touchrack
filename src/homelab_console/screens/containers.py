from __future__ import annotations

from datetime import datetime, timezone

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from homelab_console.models import ContainerInfo, ContainersSnapshot


def _format_age(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s ago"
    minutes, remaining = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {remaining:02d}s ago"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m ago"


def _percent_value(value: str | None) -> float:
    if not value:
        return 0.0
    try:
        return float(value.strip().rstrip("%"))
    except ValueError:
        return 0.0


def _resource_text(container: ContainerInfo) -> str:
    parts: list[str] = []
    if container.cpu_percent:
        parts.append(f"CPU {container.cpu_percent}")
    if container.memory_percent:
        parts.append(f"MEM {container.memory_percent}")
    elif container.memory_usage:
        parts.append(f"MEM {container.memory_usage.split(' / ', 1)[0]}")
    return " · ".join(parts) if parts else "No live stats"


class ContainerDetailScreen(ModalScreen[None]):
    """Read-only, touch-friendly detail view for a single container."""

    def __init__(self, container: ContainerInfo, engine: str) -> None:
        super().__init__()
        self.container = container
        self.engine = engine

    def compose(self) -> ComposeResult:
        state_class = "running" if self.container.is_running else "stopped"
        with Vertical(id="container-detail-dialog"):
            with Horizontal(id="container-detail-header"):
                yield Button("BACK", id="container-detail-back", classes="detail-back")
                with Vertical(id="container-detail-title-block"):
                    yield Label(self.container.name, id="container-detail-name")
                    yield Label(f"{self.engine} · read-only", id="container-detail-engine")
                yield Static(
                    self.container.state.upper(),
                    id="container-detail-state",
                    classes=state_class,
                )

            with Grid(id="container-detail-grid"):
                yield Static(
                    f"IMAGE\n{self.container.image}",
                    classes="container-detail-card wide",
                )
                yield Static(
                    f"STATUS\n{self.container.status}",
                    classes="container-detail-card wide",
                )
                yield Static(
                    f"CPU\n{self.container.cpu_percent or 'Unavailable'}",
                    classes="container-detail-card",
                )
                yield Static(
                    f"MEMORY\n{self.container.memory_percent or self.container.memory_usage or 'Unavailable'}",
                    classes="container-detail-card",
                )
                yield Static(
                    f"CONTAINER ID\n{self.container.container_id[:20] or 'Unavailable'}",
                    classes="container-detail-card wide",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "container-detail-back":
            self.dismiss()


class CompactContainerRow(Horizontal):
    """Aligned touch-friendly row for the compact container list.

    Each cell is a button so the full visible row remains tappable while the
    values line up exactly with the sortable header columns.
    """

    def __init__(self, container: ContainerInfo, index: int) -> None:
        classes = "compact-container-row running" if container.is_running else "compact-container-row stopped"
        super().__init__(classes=classes)
        self.container = container
        self.index = index

    def compose(self) -> ComposeResult:
        state = "RUN" if self.container.is_running else "STOP"
        cpu = self.container.cpu_percent or "--"
        mem = self.container.memory_percent or "--"
        base_id = f"container-open-{self.index}"
        yield Button(
            self.container.name,
            id=f"{base_id}-name",
            classes="compact-cell name",
        )
        yield Button(
            state,
            id=f"{base_id}-state",
            classes="compact-cell state",
        )
        yield Button(
            cpu,
            id=f"{base_id}-cpu",
            classes="compact-cell metric",
        )
        yield Button(
            mem,
            id=f"{base_id}-memory",
            classes="compact-cell metric",
        )


class ResourceRankingRow(Horizontal):
    """One-line btop-style ranking with an inline meter."""

    def __init__(self, container_name: str, value: str | None, *, maximum: float) -> None:
        super().__init__(classes="resource-ranking-row")
        # `Widget.name` is a Textual property, so application data must not use
        # that attribute name.
        self.container_name = container_name
        self.display_value = value or "--"
        self.maximum = max(1.0, maximum)

    def compose(self) -> ComposeResult:
        numeric = _percent_value(self.display_value)
        width = 11
        filled = round(min(1.0, numeric / self.maximum) * width)
        bar = "█" * filled + "░" * (width - filled)
        yield Static(self.container_name, classes="resource-ranking-name")
        yield Static(bar, classes="resource-ranking-bar")
        yield Static(self.display_value, classes="resource-ranking-value")


class BtopRanking(Vertical):
    def __init__(self, heading: str, containers: list[ContainerInfo], metric: str) -> None:
        super().__init__(classes="btop-ranking")
        self.heading = heading
        self.containers = containers
        self.metric = metric

    def compose(self) -> ComposeResult:
        yield Label(self.heading, classes="btop-ranking-title")
        if not self.containers:
            yield Static("No data", classes="summary-ranking-empty")
            return
        values = [
            _percent_value(item.cpu_percent if self.metric == "cpu" else item.memory_percent)
            for item in self.containers
        ]
        maximum = max(values, default=1.0)
        for item in self.containers:
            value = item.cpu_percent if self.metric == "cpu" else item.memory_percent
            yield ResourceRankingRow(item.name, value, maximum=maximum)


class StoppedPanel(Vertical):
    def __init__(self, containers: list[ContainerInfo]) -> None:
        super().__init__(classes="btop-stopped-panel")
        self.containers = containers

    def compose(self) -> ComposeResult:
        yield Label("STOPPED", classes="btop-ranking-title")
        if not self.containers:
            yield Static("All containers are running", classes="stopped-ok")
            return
        for item in self.containers[:5]:
            with Horizontal(classes="stopped-row"):
                yield Static(item.name, classes="stopped-name")
                yield Static(item.status, classes="stopped-status")


class ContainersSummary(Vertical):
    def __init__(self, snapshot: ContainersSnapshot) -> None:
        super().__init__(classes="containers-summary btop-containers-summary")
        self.snapshot = snapshot

    def compose(self) -> ComposeResult:
        stopped = [item for item in self.snapshot.containers if not item.is_running]
        top_cpu = sorted(
            self.snapshot.containers,
            key=lambda item: _percent_value(item.cpu_percent),
            reverse=True,
        )[:5]
        top_mem = sorted(
            self.snapshot.containers,
            key=lambda item: _percent_value(item.memory_percent),
            reverse=True,
        )[:5]

        with Horizontal(id="btop-container-totals"):
            yield Static(
                f"{self.snapshot.running}\nRUNNING",
                classes="btop-total running",
            )
            yield Static(
                f"{self.snapshot.stopped}\nSTOPPED",
                classes="btop-total stopped",
            )
            yield Static(
                f"{self.snapshot.total}\nTOTAL",
                classes="btop-total total",
            )

        with Grid(id="btop-container-rankings"):
            yield BtopRanking("CPU HOTSPOTS", top_cpu, "cpu")
            yield BtopRanking("MEMORY HOTSPOTS", top_mem, "memory")
            yield StoppedPanel(stopped)


class ContainersCompactList(VerticalScroll):
    """Touch-friendly compact list with sortable column headers."""

    def __init__(
        self,
        containers: tuple[ContainerInfo, ...],
        sort_key: str,
        sort_descending: bool,
    ) -> None:
        super().__init__(classes="containers-compact-list")
        self.containers = containers
        self.sort_key = sort_key
        self.sort_descending = sort_descending

    def _header_label(self, title: str, key: str) -> str:
        if self.sort_key != key:
            return title
        return f"{title} {'v' if self.sort_descending else '^'}"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="compact-list-header"):
            yield Button(
                self._header_label("NAME", "name"),
                id="container-sort-name",
                classes="compact-header-button name",
            )
            yield Button(
                self._header_label("STATE", "state"),
                id="container-sort-state",
                classes="compact-header-button state",
            )
            yield Button(
                self._header_label("CPU", "cpu"),
                id="container-sort-cpu",
                classes="compact-header-button metric",
            )
            yield Button(
                self._header_label("MEMORY", "memory"),
                id="container-sort-memory",
                classes="compact-header-button metric",
            )
        for index, container in enumerate(self.containers):
            yield CompactContainerRow(container, index)


class ContainersView(Vertical):
    """Containers section with summary and compact list modes."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.view_mode = "summary"
        self.snapshot: ContainersSnapshot | None = None
        self.sort_key = "state"
        self.sort_descending = False
        self.displayed_containers: tuple[ContainerInfo, ...] = ()

    def compose(self) -> ComposeResult:
        with Horizontal(id="containers-header"):
            yield Button("≡", id="menu-button-containers", classes="flat-control", tooltip="Open console menu")
            yield Label("CONTAINERS", id="containers-title")
            yield Static("● --", id="containers-health")
            yield Button("REF 5s", id="refresh-interval-containers", classes="refresh-interval-button flat-control")
            yield Button("SLEEP 1m", id="screen-blank-containers", classes="screen-blank-button flat-control")

        with Horizontal(id="containers-context"):
            yield Button("SUMMARY", id="containers-view-button", classes="flat-control")
            yield Static("Reading local containers…", id="containers-status")
        yield Label("Waiting", id="containers-last-update")
        yield Vertical(id="containers-body")

    def show_refreshing(self) -> None:
        if self.snapshot is None:
            self.query_one("#containers-status", Static).update("Reading local containers…")

    def toggle_view(self) -> None:
        self.view_mode = "list" if self.view_mode == "summary" else "summary"
        label = "LIST" if self.view_mode == "list" else "SUMMARY"
        self.query_one("#containers-view-button", Button).label = label
        self._render_body()

    def set_sort(self, key: str) -> None:
        if key not in {"name", "state", "cpu", "memory"}:
            return
        if key == self.sort_key:
            self.sort_descending = not self.sort_descending
        else:
            self.sort_key = key
            # Resource columns are most useful high-to-low on first touch.
            self.sort_descending = key in {"cpu", "memory"}
        self._render_body()

    def _sorted_containers(self) -> tuple[ContainerInfo, ...]:
        if self.snapshot is None:
            return ()

        containers = list(self.snapshot.containers)
        if self.sort_key == "name":
            key_fn = lambda item: item.name.casefold()
        elif self.sort_key == "state":
            # False (running) sorts before True (not running), then by name.
            key_fn = lambda item: (not item.is_running, item.name.casefold())
        elif self.sort_key == "cpu":
            key_fn = lambda item: (_percent_value(item.cpu_percent), item.name.casefold())
        else:
            key_fn = lambda item: (_percent_value(item.memory_percent), item.name.casefold())

        containers.sort(key=key_fn, reverse=self.sort_descending)
        return tuple(containers)

    def container_for_button(self, button_id: str) -> ContainerInfo | None:
        if not button_id.startswith("container-open-"):
            return None
        try:
            remainder = button_id.removeprefix("container-open-")
            index = int(remainder.split("-", 1)[0])
            return self.displayed_containers[index]
        except (ValueError, IndexError):
            return None

    def show_snapshot(self, snapshot: ContainersSnapshot) -> None:
        self.snapshot = snapshot
        self.update_snapshot_age()

        status = self.query_one("#containers-status", Static)
        health = self.query_one("#containers-health", Static)
        health.remove_class("healthy", "degraded", "unavailable")
        if snapshot.error:
            status.update(snapshot.error)
            status.set_class(True, "has-error")
            status.remove_class("has-warning")
            health.update("● DOWN")
            health.add_class("unavailable")
        elif snapshot.stats_error:
            status.update(snapshot.stats_error)
            status.set_class(True, "has-warning")
            status.remove_class("has-error")
            health.update("● WARN")
            health.add_class("degraded")
        else:
            status.update(f"{snapshot.total} containers · {snapshot.running} running · {snapshot.stopped} stopped")
            status.remove_class("has-error", "has-warning")
            health.update("● OK")
            health.add_class("healthy")

        self._render_body()


    def update_snapshot_age(self) -> None:
        if self.snapshot is None:
            self.query_one("#containers-last-update", Label).update("Waiting")
            return
        age = max(
            0,
            int((datetime.now(timezone.utc) - self.snapshot.collected_at).total_seconds()),
        )
        self.query_one("#containers-last-update", Label).update(_format_age(age))

    def _render_body(self) -> None:
        body = self.query_one("#containers-body", Vertical)
        body.remove_children()
        snapshot = self.snapshot
        if snapshot is None:
            body.mount(Static("Reading local containers…", classes="containers-empty"))
            return
        if snapshot.error:
            body.mount(
                Static(
                    f"{snapshot.engine} data is unavailable.\nThe host dashboard remains operational.",
                    classes="containers-empty",
                )
            )
            return
        if not snapshot.containers:
            body.mount(Static("No local containers found.", classes="containers-empty"))
            return
        if self.view_mode == "summary":
            body.mount(ContainersSummary(snapshot))
        else:
            self.displayed_containers = self._sorted_containers()
            body.mount(
                ContainersCompactList(
                    self.displayed_containers,
                    self.sort_key,
                    self.sort_descending,
                )
            )
