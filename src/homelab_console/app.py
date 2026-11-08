from __future__ import annotations

import asyncio

from datetime import UTC, datetime
from time import monotonic
from pathlib import Path
import subprocess
import threading

from textual import events, work
from textual.worker import Worker
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Static

from homelab_console.button_actions import (
    ButtonActionKind,
    classify_button_action,
)
from homelab_console.config import load_config
from homelab_console.refresh_policy import (
    next_refresh_interval,
    normalize_refresh_interval,
    refresh_interval_label,
)
from homelab_console.runtime_diagnostics import (
    RuntimeDiagnostics,
    TouchDiagnostic,
    build_diagnostics_rows,
)
from homelab_console.providers import (
    AutoContainersProvider,
    ContainersProvider,
    DiskHealthProvider,
    HostProvider,
    LocalHostProvider,
    LocalStorageProvider,
    SmartctlDiskHealthProvider,
    RemoteSmartctlDiskHealthProvider,
    StorageProvider,
)
from homelab_console.models import DiskHealthSnapshot
from homelab_console.nodes import (
    Node,
    NodeConnectivityRegistry,
    LastSuccessfulSnapshotStore,
    NodeRegistry,
    NodeRuntime,
    build_node_registry,
)
from homelab_console.screens import (
    ContainersView,
    HostView,
    NodeSelectorScreen,
    ServicesView,
    StorageView,
)
from homelab_console.screens.system import ConsoleMenu
from homelab_console.screens.containers import ContainerDetailScreen
from homelab_console.screens.services import ServiceDetailScreen
from homelab_console.screens.storage import DiskDetailScreen, StorageDetailScreen
from homelab_console.services import (
    ContainerSnapshotServiceProvider,
    CruxServiceProvider,
    HttpServiceProvider,
    RemoteSystemdServiceProvider,
    ServicesManager,
    ServicesRegistry,
    SystemdServiceProvider,
    TcpServiceProvider,
)
from homelab_console.screen_blank import (
    SCREEN_BLANK_OPTIONS,
    apply_screen_blank,
    force_screen_blank,
    normalize_screen_blank_minutes,
    screen_blank_label,
    wake_screen,
)
from homelab_console.touch import TouchReader, TouchTap, map_axis, touch_enabled_from_environment
from homelab_console import __version__
from homelab_console.single_instance import AlreadyRunningError, SingleInstanceLock


class HomelabConsole(App[None]):
    TITLE = "Homelab Console"
    SUB_TITLE = "Local host spike"
    CSS_PATH = "console.tcss"

    def __init__(
        self,
        provider: HostProvider | None = None,
        containers_provider: ContainersProvider | None = None,
        storage_provider: StorageProvider | None = None,
        disk_health_provider: DiskHealthProvider | None = None,
        refresh_seconds: float = 5.0,
        containers_refresh_seconds: float | None = None,
        screen_blank_minutes: int = 1,
        screen_tty: str = "/dev/tty1",
        touch_enabled: bool = True,
        touch_device: str = "auto",
        services_config_path: str | None = None,
        config_source_path: str | None = None,
        nodes: NodeRegistry | None = None,
    ) -> None:
        super().__init__()
        injected_providers = (
            provider,
            containers_provider,
            storage_provider,
            disk_health_provider,
        )
        if nodes is not None and any(item is not None for item in injected_providers):
            raise ValueError("nodes and individual providers cannot be combined")
        if nodes is None:
            local_runtime = NodeRuntime(
                node=Node(id="local", title="Local"),
                host=provider or LocalHostProvider(),
                containers=containers_provider or AutoContainersProvider(),
                storage=storage_provider or LocalStorageProvider(),
                disk_health=disk_health_provider or SmartctlDiskHealthProvider(),
            )
            nodes = NodeRegistry((local_runtime,), default_node_id="local")
        self.nodes = nodes
        self.node_connectivity = NodeConnectivityRegistry(
            tuple(runtime.node.id for runtime in nodes)
        )
        self.node_snapshots = LastSuccessfulSnapshotStore()
        # Only the latest refresh may publish per capability.
        self._refresh_tokens: dict[str, object] = {}
        self.active_node_id = nodes.default.node.id
        self.refresh_seconds = normalize_refresh_interval(refresh_seconds)
        # Kept for compatibility with the earlier spike API; refresh is now global.
        _ = containers_refresh_seconds
        self._last_scheduled_refresh = monotonic()
        self.active_section = "host"
        self.screen_blank_minutes = normalize_screen_blank_minutes(screen_blank_minutes)
        self.screen_tty = screen_tty
        self.touch_enabled = touch_enabled
        self.touch_device = touch_device
        self.screen_blank_error: str | None = None
        self.config_source_path = config_source_path
        self.services_registry = ServicesRegistry(services_config_path)
        self._container_snapshots = {}
        self._local_node_id = next(
            (
                runtime.node.id
                for runtime in self.nodes
                if runtime.node.transport == "local"
            ),
            self.nodes.default.node.id,
        )
        self._services_managers: dict[str, ServicesManager] = {}
        # Compatibility attribute for code that inspected the former single
        # manager. It always follows the active node context.
        self.services_manager = self._services_manager(self.active_node)
        self.touch_reader: TouchReader | None = None
        self._touch_state_lock = threading.Lock()
        self._last_touch_activity = monotonic()
        self._discard_next_tap = False
        self._display_blanked = False

    @property
    def active_node(self) -> NodeRuntime:
        """Return the runtime selected as the application's monitoring context."""
        return self.nodes.get(self.active_node_id)

    # Compatibility accessors preserve the v4.7 constructor/injection surface
    # while keeping the active node runtime as the single source of truth.
    @property
    def provider(self) -> HostProvider:
        return self.active_node.host

    @property
    def containers_provider(self) -> ContainersProvider:
        return self.active_node.containers

    @property
    def storage_provider(self) -> StorageProvider:
        return self.active_node.storage

    @property
    def disk_health_provider(self) -> DiskHealthProvider:
        return self.active_node.disk_health

    def compose(self) -> ComposeResult:
        yield Static("TOUCH: waiting", id="touch-diagnostic")
        with Container(id="content"):
            yield HostView(id="view-host", classes="section-view")
            yield ServicesView(
                id="view-services",
                classes="section-view hidden",
            )
            yield StorageView(
                id="view-storage",
                classes="section-view hidden",
            )
            yield ContainersView(
                id="view-containers",
                classes="section-view hidden",
            )
        with Horizontal(id="navigation"):
            yield Button("HOST", id="nav-host", classes="nav active", variant="primary")
            yield Button("SERVICES", id="nav-services", classes="nav")
            yield Button("STORAGE", id="nav-storage", classes="nav")
            yield Button("CONTAINERS", id="nav-containers", classes="nav")
            if len(self.nodes) > 1:
                yield Button(
                    self.active_node.node.id.upper(),
                    id="select-node",
                    classes="nav",
                )

    def on_mount(self) -> None:
        # Defer the initial workers until Textual has completed the first
        # compose/mount/refresh cycle. StorageView now has nested composed
        # controls, and starting a worker directly from App.on_mount can race
        # those children being attached to the DOM.
        self.call_after_refresh(self._initial_refresh)
        self._last_scheduled_refresh = monotonic()
        # Keep display sleep independent from data refresh / modal screens.
        self.set_interval(1.0, self._screen_blank_tick)
        self.set_interval(1.0, self._refresh_scheduler_tick)
        self.set_interval(1.0, self._update_snapshot_ages)
        self._update_snapshot_ages()
        self._update_node_selector()
        self._apply_layout_profile()
        self._update_refresh_controls()
        self._update_screen_blank_controls()
        self._apply_screen_blank_setting()
        self._start_touch_reader()

    def _initial_refresh(self) -> None:
        self._refresh_active_capabilities()

    def _refresh_active_capabilities(self) -> None:
        """Start one refresh batch for the active node."""
        self.refresh_host()
        self.refresh_containers()
        self.refresh_storage()

    def on_resize(self, event: events.Resize) -> None:
        self._apply_layout_profile()

    def _apply_layout_profile(self) -> None:
        """Apply a dedicated profile for the 64x18 physical console."""
        compact = self.size.width <= 70 or self.size.height <= 20
        self.screen.set_class(compact, "compact-touch")
        self._update_refresh_controls()
        self._update_screen_blank_controls()

    async def on_unmount(self) -> None:
        if self.touch_reader is not None:
            self.touch_reader.stop()

        # Shutdown covers all nodes, including the inactive ones.
        managers = tuple(self._services_managers.values())
        hosts = {
            id(runtime.host): runtime.host
            for runtime in self.nodes
            if isinstance(runtime.host, LocalHostProvider)
        }
        containers = {
            id(runtime.containers): runtime.containers
            for runtime in self.nodes
            if callable(getattr(runtime.containers, "shutdown", None))
        }
        storages = {
            id(runtime.storage): runtime.storage
            for runtime in self.nodes
            if isinstance(runtime.storage, LocalStorageProvider)
        }
        smart_providers = {
            id(runtime.disk_health): runtime.disk_health
            for runtime in self.nodes
            if isinstance(runtime.disk_health, SmartctlDiskHealthProvider)
        }
        remote_smart_providers = {
            id(runtime.disk_health): runtime.disk_health
            for runtime in self.nodes
            if isinstance(
                runtime.disk_health, RemoteSmartctlDiskHealthProvider
            )
        }

        if (
            managers or hosts or containers or storages
            or smart_providers or remote_smart_providers
        ):
            results = await asyncio.gather(
                *(manager.shutdown() for manager in managers),
                *(host.shutdown() for host in hosts.values()),
                *(provider.shutdown() for provider in containers.values()),
                *(storage.shutdown() for storage in storages.values()),
                *(smart.shutdown() for smart in smart_providers.values()),
                *(smart.shutdown() for smart in remote_smart_providers.values()),
                return_exceptions=True,
            )
            # Finish every shutdown before propagating an isolated failure.
            for result in results:
                if isinstance(result, BaseException):
                    raise result

    def _start_touch_reader(self) -> None:
        diagnostic = self.query_one("#touch-diagnostic", Static)
        if not self.touch_enabled or not touch_enabled_from_environment():
            diagnostic.update("TOUCH: disabled")
            return
        device_path = None if self.touch_device.casefold() == "auto" else self.touch_device
        self.touch_reader = TouchReader(
            self._receive_touch_from_thread,
            touch_down_callback=self._wake_display_from_thread,
            device_path=device_path,
        )
        if self.touch_reader.start():
            diagnostic.update("TOUCH: listening")
        else:
            diagnostic.update(f"TOUCH: {self.touch_reader.error}")

    def _wake_display_from_thread(self) -> None:
        """Wake tty1 and consume the first touch when the app blanked it.

        The kernel's virtual-console blank timer does not expose a reliable
        blanked/not-blanked state to us. The app therefore owns the idle timer:
        when it forces the screen blank it records that state, and the next
        BTN_TOUCH wakes the display and is discarded before hit-testing.
        """
        with self._touch_state_lock:
            was_blanked = self._display_blanked
            self._last_touch_activity = monotonic()
            if was_blanked:
                self._discard_next_tap = True
                self._display_blanked = False
        if not was_blanked:
            return
        try:
            wake_screen(tty_path=self.screen_tty)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # The wake gesture must still be consumed if setterm fails.
            pass
        self.call_from_thread(self._resume_refresh_after_wake)

    def _resume_refresh_after_wake(self) -> None:
        """Refresh once after sleep without replaying missed intervals."""
        self._last_scheduled_refresh = monotonic()
        self._refresh_active_capabilities()

    def _receive_touch_from_thread(self, tap: TouchTap) -> None:
        with self._touch_state_lock:
            discard = self._discard_next_tap
            self._discard_next_tap = False
            self._last_touch_activity = monotonic()
        if discard:
            return
        # Marshal all Textual access back to the application thread.
        self.call_from_thread(self._show_touch_diagnostic, tap)

    def _show_touch_diagnostic(self, tap: TouchTap) -> None:
        col = map_axis(tap.raw_x, 0, 4096, max(1, self.size.width))
        row = map_axis(tap.raw_y, 0, 4096, max(1, self.size.height))
        widget = self._widget_at(col, row)
        identity = self._widget_identity(widget)
        diagnostic = self.query_one("#touch-diagnostic", Static)
        diagnostic.update(f"TOUCH ({col},{row}) -> {identity}")

        # A tap on the dimmed area around the modal menu should behave like
        # clicking outside a popover: close the menu without triggering any
        # action underneath it.  This is intentionally handled here because
        # the physical touchscreen is read directly from evdev rather than
        # delivered to Textual as mouse-click events.
        if isinstance(self.screen, ConsoleMenu):
            dialog = self.screen.query_one("#menu-dialog", Vertical)
            region = dialog.region
            outside_dialog = not (
                region.x <= col < region.x + region.width
                and region.y <= row < region.y + region.height
            )
            if outside_dialog:
                self.screen.dismiss()
                return

        if isinstance(widget, Button) and not widget.disabled:
            widget.press()

    def _widget_at(self, x: int, y: int) -> Widget | None:
        """Return the actionable widget Textual actually renders at a cell.

        Physical touch events arrive from evdev rather than Textual's mouse
        input path. Delegate hit-testing to Screen's compositor-aware lookup so
        scrolled, clipped, hidden, or covered widgets cannot steal a tap.
        """
        return self.screen.get_focusable_widget_at(x, y)

    @staticmethod
    def _widget_identity(widget: Widget | None) -> str:
        if widget is None:
            return "none"
        name = widget.__class__.__name__
        return f"{name}#{widget.id}" if widget.id else name

    def _update_snapshot_ages(self) -> None:
        """Update mounted freshness labels without triggering data collection.

        Timers may tick while Textual is tearing down the screen. Treat these
        presentation-only updates as best-effort so an already-unmounted view
        cannot turn normal shutdown into an application exception.
        """
        for view in self.query(ContainersView):
            view.update_snapshot_age()
        for view in self.query(ServicesView):
            view.update_snapshot_age()

    def diagnostics_rows(self) -> tuple[tuple[str, str, str], ...]:
        """Return cheap, read-only runtime facts without probing hardware again."""
        touch = TouchDiagnostic("DISABLED", "disabled", "muted")
        if self.touch_enabled and touch_enabled_from_environment():
            reader_alive = (
                self.touch_reader is not None
                and self.touch_reader._thread is not None
                and self.touch_reader._thread.is_alive()
            )
            if reader_alive:
                device = Path(self.touch_reader.device_path or "?").name
                touch = TouchDiagnostic(
                    "OK",
                    f"{device} · {self.touch_reader.device_name or 'touch'}",
                    "ok",
                )
            else:
                detail = (
                    self.touch_reader.error
                    if self.touch_reader is not None
                    else "reader unavailable"
                )
                touch = TouchDiagnostic("WARN", detail, "warn")

        containers = self.query_one(ContainersView).snapshot
        services = self.query_one(ServicesView).snapshot

        return build_diagnostics_rows(
            RuntimeDiagnostics(
                version=__version__,
                node_id=self.active_node.node.id,
                node_title=self.active_node.node.title,
                node_transport=self.active_node.node.transport,
                node_connectivity=self.node_connectivity.get(
                    self.active_node.node.id
                ).status,
                screen_tty=self.screen_tty,
                width=self.size.width,
                height=self.size.height,
                touch=touch,
                container_engine=(
                    containers.engine if containers is not None else None
                ),
                container_total=(
                    containers.total if containers is not None else None
                ),
                containers_error=(
                    containers.error if containers is not None else None
                ),
                container_access=self.active_node.container_access,
                services_count=(
                    len(services.services) if services is not None else None
                ),
                services_error=(
                    services.error if services is not None else None
                ),
                config_source_path=self.config_source_path,
                sleep_label=screen_blank_label(
                    self.screen_blank_minutes,
                    compact=True,
                ),
                screen_blank_error=self.screen_blank_error,
            )
        )

    def _refresh_scheduler_tick(self) -> None:
        now = monotonic()
        with self._touch_state_lock:
            if self._display_blanked:
                return
        if now - self._last_scheduled_refresh < self.refresh_seconds:
            return
        self._last_scheduled_refresh = now
        self._refresh_active_capabilities()

    def _screen_blank_tick(self) -> None:
        """Drive display sleep from a dedicated app-level timer."""
        self._update_display_blank_state(monotonic())

    def cycle_refresh_interval(self) -> None:
        self.refresh_seconds = next_refresh_interval(self.refresh_seconds)
        self._last_scheduled_refresh = monotonic()
        self._update_refresh_controls()
        # Apply the new selection immediately, then start counting the new interval.
        self._refresh_active_capabilities()

    def _update_refresh_controls(self) -> None:
        label = refresh_interval_label(
            self.refresh_seconds,
            compact=self.screen.has_class("compact-touch"),
        )
        for button in self.query(".refresh-interval-button"):
            if isinstance(button, Button):
                button.label = label


    def cycle_screen_blank(self) -> None:
        values = tuple(option.minutes for option in SCREEN_BLANK_OPTIONS)
        current_index = values.index(self.screen_blank_minutes)
        self.screen_blank_minutes = values[(current_index + 1) % len(values)]
        with self._touch_state_lock:
            self._last_touch_activity = monotonic()
            self._discard_next_tap = False
            self._display_blanked = False
        self._apply_screen_blank_setting()
        self._update_screen_blank_controls()

    def _apply_screen_blank_setting(self) -> None:
        """Disable autonomous VC blanking; the app owns the idle/wake state."""
        try:
            apply_screen_blank(0, tty_path=self.screen_tty)
            wake_screen(tty_path=self.screen_tty)
            with self._touch_state_lock:
                self._display_blanked = False
                self._last_touch_activity = monotonic()
                self._discard_next_tap = False
            self.screen_blank_error = None
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            self.screen_blank_error = str(exc)

    def _update_display_blank_state(self, now: float | None = None) -> None:
        """Blank once after the configured period of touch inactivity."""
        if self.screen_blank_minutes <= 0:
            return
        current = monotonic() if now is None else now
        timeout_seconds = self.screen_blank_minutes * 60
        with self._touch_state_lock:
            should_blank = (
                not self._display_blanked
                and current - self._last_touch_activity >= timeout_seconds
            )
            if should_blank:
                self._display_blanked = True
        if not should_blank:
            return
        try:
            force_screen_blank(tty_path=self.screen_tty)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            with self._touch_state_lock:
                self._display_blanked = False
            self.screen_blank_error = str(exc)
            self._update_screen_blank_controls()

    def _update_screen_blank_controls(self) -> None:
        compact = self.screen.has_class("compact-touch")
        label = screen_blank_label(self.screen_blank_minutes, compact=compact)
        if compact:
            label = f"SLEEP {label}"
        if self.screen_blank_error:
            label = "ERR" if compact else "SCREEN ERR"
        for button in self.query(".screen-blank-button"):
            if isinstance(button, Button):
                button.label = label
                button.tooltip = (
                    self.screen_blank_error
                    or "Cycle display blanking: 1m, 5m, 15m, always on"
                )

    def _update_node_selector(self) -> None:
        buttons = self.query("#select-node")
        for button in buttons:
            if isinstance(button, Button):
                button.label = self.active_node.node.id.upper()
                button.tooltip = f"Active node: {self.active_node.node.title}"

    def select_node(self, node_id: str) -> None:
        """Activate a configured runtime and refresh its capability views."""
        runtime = self.nodes.get(node_id)
        if node_id == self.active_node_id:
            return

        self.active_node_id = node_id
        self.services_manager = self._services_manager(runtime)
        self._last_scheduled_refresh = monotonic()
        self._update_node_selector()
        self.show_section("host")
        self.query_one(HostView).show_node_switching(runtime.node.title)
        self.query_one(ContainersView).show_node_switching()
        self.query_one(StorageView).show_node_switching()
        self.query_one(ServicesView).show_node_switching()
        self._refresh_active_capabilities()

    def refresh_host(self) -> Worker[None]:
        """Invalidate older results before scheduling a worker."""
        refresh_token = object()
        self._refresh_tokens["host"] = refresh_token
        return self._refresh_host_worker(refresh_token)

    @work(
        exclusive=True, group="host-refresh", name="refresh_host", exit_on_error=False
    )
    async def _refresh_host_worker(self, refresh_token: object) -> None:
        host_view = self.query_one(HostView)
        host_view.show_refreshing()
        runtime = self.active_node
        try:
            snapshot = await runtime.host.collect()
        except Exception as exc:
            # An unexpected host collection error must not leave the node online.
            if (
                self.active_node_id == runtime.node.id
                and self._refresh_tokens["host"] is refresh_token
            ):
                self.node_connectivity.record(
                    runtime.node.id, error=str(exc)
                )
                host_view.show_refresh_error()
            raise
        if (
            self.active_node_id != runtime.node.id
            or self._refresh_tokens["host"] is not refresh_token
        ):
            return
        self.node_connectivity.record(runtime.node.id, error=snapshot.error)
        snapshot = self.node_snapshots.resolve(
            runtime.node.id, "host", snapshot
        )
        host_view.show_snapshot(snapshot)

    def refresh_containers(self) -> Worker[None]:
        """Invalidate older results before scheduling a worker."""
        refresh_token = object()
        self._refresh_tokens["containers"] = refresh_token
        return self._refresh_containers_worker(refresh_token)

    @work(
        exclusive=True,
        group="containers-refresh",
        name="refresh_containers",
        exit_on_error=False,
    )
    async def _refresh_containers_worker(self, refresh_token: object) -> None:
        containers_view = self.query_one(ContainersView)
        containers_view.show_refreshing()
        runtime = self.active_node
        try:
            snapshot = await runtime.containers.collect()
        except Exception as exc:
            if (
                self.active_node_id == runtime.node.id
                and self._refresh_tokens["containers"] is refresh_token
            ):
                containers_view.show_refresh_error(str(exc))
            raise
        if (
            self.active_node_id != runtime.node.id
            or self._refresh_tokens["containers"] is not refresh_token
        ):
            return
        snapshot = self.node_snapshots.resolve(
            runtime.node.id, "containers", snapshot
        )
        self._container_snapshots[runtime.node.id] = snapshot
        containers_view.show_snapshot(snapshot)
        self.refresh_services()

    def refresh_storage(self) -> Worker[None]:
        """Invalidate older results before scheduling a worker."""
        refresh_token = object()
        self._refresh_tokens["storage"] = refresh_token
        return self._refresh_storage_worker(refresh_token)

    @work(
        exclusive=True,
        group="storage-refresh",
        name="refresh_storage",
        exit_on_error=False,
    )
    async def _refresh_storage_worker(self, refresh_token: object) -> None:
        storage_view = self.query_one(StorageView)
        storage_view.show_refreshing()
        runtime = self.active_node
        try:
            snapshot = await runtime.storage.collect()
        except Exception as exc:
            if (
                self.active_node_id == runtime.node.id
                and self._refresh_tokens["storage"] is refresh_token
            ):
                storage_view.show_refresh_error(str(exc))
            raise
        try:
            disk_snapshot = await runtime.disk_health.collect()
        except Exception as exc:  # noqa: BLE001
            # Provider failures must not block filesystem collection.
            disk_snapshot = DiskHealthSnapshot(
                disks=(),
                collected_at=datetime.now(UTC),
                available=False,
                error=str(exc),
            )
        if (
            self.active_node_id != runtime.node.id
            or self._refresh_tokens["storage"] is not refresh_token
        ):
            return
        snapshot = self.node_snapshots.resolve(
            runtime.node.id, "storage", snapshot
        )
        storage_view.show_snapshot(snapshot)
        storage_view.show_disk_snapshot(disk_snapshot)

    def refresh_services(self) -> Worker[None]:
        """Invalidate older results before scheduling a worker."""
        refresh_token = object()
        self._refresh_tokens["services"] = refresh_token
        return self._refresh_services_worker(refresh_token)

    @work(
        exclusive=True,
        group="services-refresh",
        name="refresh_services",
        exit_on_error=False,
    )
    async def _refresh_services_worker(self, refresh_token: object) -> None:
        services_view = self.query_one(ServicesView)
        services_view.show_refreshing()
        runtime = self.active_node
        manager = self._services_manager(runtime)
        try:
            snapshot = await manager.collect()
        except Exception as exc:
            if (
                self.active_node_id == runtime.node.id
                and self._refresh_tokens["services"] is refresh_token
            ):
                services_view.show_refresh_error(str(exc))
            raise
        if (
            self.active_node_id != runtime.node.id
            or self._refresh_tokens["services"] is not refresh_token
        ):
            return
        services_view.show_snapshot(snapshot)

    def _services_manager(self, runtime: NodeRuntime) -> ServicesManager:
        existing = self._services_managers.get(runtime.node.id)
        if existing is not None:
            return existing

        providers = {
            "container": ContainerSnapshotServiceProvider(
                lambda node_id=runtime.node.id: self._container_snapshots.get(
                    node_id
                )
            ),
            "http": HttpServiceProvider(),
            "tcp": TcpServiceProvider(),
        }
        if runtime.node.transport == "local":
            providers["systemd"] = SystemdServiceProvider()
        elif runtime.command_runner is not None:
            providers["systemd"] = RemoteSystemdServiceProvider(
                runtime.command_runner
            )
            providers["crux"] = CruxServiceProvider(runtime.command_runner)

        manager = ServicesManager(
            self.services_registry,
            providers,
            node_id=runtime.node.id,
            local_node_id=self._local_node_id,
        )
        self._services_managers[runtime.node.id] = manager
        return manager

    def _containers_snapshot(self):
        try:
            return self._container_snapshots.get(self.active_node_id)
        except Exception:
            return None

    def on_button_pressed(self, event: Button.Pressed) -> None:
        action = classify_button_action(event.button.id)
        if action is None:
            return

        if action.kind is ButtonActionKind.MENU:
            self.push_screen(ConsoleMenu())
            return

        if action.kind is ButtonActionKind.REFRESH_INTERVAL:
            self.cycle_refresh_interval()
            return

        if action.kind is ButtonActionKind.SCREEN_BLANK:
            self.cycle_screen_blank()
            return

        if action.kind is ButtonActionKind.CONTAINERS_VIEW:
            self.query_one(ContainersView).toggle_view()
            return

        if action.kind is ButtonActionKind.CONTAINERS_PAGE:
            self.query_one(ContainersView).page_list(int(action.value))
            return

        if action.kind is ButtonActionKind.CONTAINER_SORT:
            self.query_one(ContainersView).set_sort(str(action.value))
            return

        if action.kind is ButtonActionKind.CONTAINER_OPEN:
            containers_view = self.query_one(ContainersView)
            container = containers_view.container_for_button(str(action.value))
            if container is not None:
                engine = (
                    containers_view.snapshot.engine
                    if containers_view.snapshot
                    else "Containers"
                )
                self.push_screen(ContainerDetailScreen(container, engine))
            return

        if action.kind is ButtonActionKind.SERVICE_OPEN:
            state = self.query_one(ServicesView).state_for_button(
                str(action.value)
            )
            if state is not None:
                self.push_screen(ServiceDetailScreen(state))
            return

        if action.kind is ButtonActionKind.STORAGE_OPEN:
            filesystem = self.query_one(StorageView).filesystem_for_button(
                str(action.value)
            )
            if filesystem is not None:
                self.push_screen(StorageDetailScreen(filesystem))
            return

        if action.kind is ButtonActionKind.STORAGE_DISK_OPEN:
            disk = self.query_one(StorageView).disk_for_button(str(action.value))
            if disk is not None:
                self.push_screen(DiskDetailScreen(disk))
            return

        if action.kind is ButtonActionKind.NODE_SELECTOR:
            self.push_screen(
                NodeSelectorScreen(
                    self.nodes.all(),
                    active_node_id=self.active_node_id,
                    connectivity=self.node_connectivity.all(),
                )
            )
            return

        if action.kind is ButtonActionKind.NAVIGATION:
            self.show_section(str(action.value))
    def show_section(self, section: str) -> None:
        if section not in {"host", "services", "storage", "containers"}:
            raise ValueError(f"Unknown section: {section}")

        self.active_section = section
        for name in ("host", "services", "storage", "containers"):
            self.query_one(f"#view-{name}").set_class(name != section, "hidden")
            button = self.query_one(f"#nav-{name}", Button)
            button.set_class(name == section, "active")
            button.variant = "primary" if name == section else "default"


def main() -> None:
    import sys

    try:
        with SingleInstanceLock():
            try:
                config = load_config()
                HomelabConsole(
                    nodes=build_node_registry(config),
                    refresh_seconds=config.refresh.seconds,
                    screen_blank_minutes=config.display.sleep_minutes,
                    screen_tty=config.display.tty,
                    touch_enabled=config.touch.enabled,
                    touch_device=config.touch.device,
                    services_config_path=config.services_file,
                    config_source_path=config.source_path,
                ).run()
            finally:
                # Leave a clean, usable terminal after quitting, including when
                # the application is interrupted. Only emit control codes to a TTY.
                if sys.stdout.isatty():
                    sys.stdout.write("\x1b[?25h\x1b[0m\x1b[2J\x1b[H")
                    sys.stdout.flush()
    except AlreadyRunningError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
