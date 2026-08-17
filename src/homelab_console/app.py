from __future__ import annotations

from time import monotonic
from pathlib import Path
import subprocess
import threading

from textual import events, work
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
    StorageProvider,
)
from homelab_console.screens import ContainersView, HostView, ServicesView, StorageView
from homelab_console.screens.system import ConsoleMenu
from homelab_console.screens.containers import ContainerDetailScreen
from homelab_console.screens.services import ServiceDetailScreen
from homelab_console.screens.storage import DiskDetailScreen, StorageDetailScreen
from homelab_console.services import (
    ContainerSnapshotServiceProvider,
    HttpServiceProvider,
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
    ) -> None:
        super().__init__()
        self.provider = provider or LocalHostProvider()
        self.containers_provider = containers_provider or AutoContainersProvider()
        self.storage_provider = storage_provider or LocalStorageProvider()
        self.disk_health_provider = (
            disk_health_provider or SmartctlDiskHealthProvider()
        )
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
        self.services_manager = ServicesManager(
            self.services_registry,
            {
                "container": ContainerSnapshotServiceProvider(self._containers_snapshot),
                "systemd": SystemdServiceProvider(),
                "http": HttpServiceProvider(),
                "tcp": TcpServiceProvider(),
            },
        )
        self.touch_reader: TouchReader | None = None
        self._touch_state_lock = threading.Lock()
        self._last_touch_activity = monotonic()
        self._discard_next_tap = False
        self._display_blanked = False

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

    def on_mount(self) -> None:
        # Defer the initial workers until Textual has completed the first
        # compose/mount/refresh cycle. StorageView now has nested composed
        # controls, and starting a worker directly from App.on_mount can race
        # those children being attached to the DOM.
        self.call_after_refresh(self._initial_refresh)
        self._last_scheduled_refresh = monotonic()
        self.set_interval(1.0, self._refresh_scheduler_tick)
        # Keep display sleep independent from data refresh / modal screens.
        self.set_interval(1.0, self._screen_blank_tick)
        self.set_interval(1.0, self._update_snapshot_ages)
        self._update_snapshot_ages()
        self._apply_layout_profile()
        self._update_refresh_controls()
        self._update_screen_blank_controls()
        self._apply_screen_blank_setting()
        self._start_touch_reader()

    def _initial_refresh(self) -> None:
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

    def on_unmount(self) -> None:
        if self.touch_reader is not None:
            self.touch_reader.stop()

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
        if now - self._last_scheduled_refresh < self.refresh_seconds:
            return
        self._last_scheduled_refresh = now
        self.refresh_host()
        self.refresh_containers()
        self.refresh_storage()

    def _screen_blank_tick(self) -> None:
        """Drive display sleep from a dedicated app-level timer."""
        self._update_display_blank_state(monotonic())

    def cycle_refresh_interval(self) -> None:
        self.refresh_seconds = next_refresh_interval(self.refresh_seconds)
        self._last_scheduled_refresh = monotonic()
        self._update_refresh_controls()
        # Apply the new selection immediately, then start counting the new interval.
        self.refresh_host()
        self.refresh_containers()
        self.refresh_storage()

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

    @work(exclusive=True, group="host-refresh")
    async def refresh_host(self) -> None:
        host_view = self.query_one(HostView)
        host_view.show_refreshing()
        snapshot = await self.provider.collect()
        host_view.show_snapshot(snapshot)

    @work(exclusive=True, group="containers-refresh")
    async def refresh_containers(self) -> None:
        containers_view = self.query_one(ContainersView)
        containers_view.show_refreshing()
        snapshot = await self.containers_provider.collect()
        containers_view.show_snapshot(snapshot)
        self.refresh_services()

    @work(exclusive=True, group="storage-refresh")
    async def refresh_storage(self) -> None:
        storage_view = self.query_one(StorageView)
        storage_view.show_refreshing()
        snapshot = await self.storage_provider.collect()
        disk_snapshot = await self.disk_health_provider.collect()
        storage_view.show_snapshot(snapshot)
        storage_view.show_disk_snapshot(disk_snapshot)

    @work(exclusive=True, group="services-refresh")
    async def refresh_services(self) -> None:
        services_view = self.query_one(ServicesView)
        services_view.show_refreshing()
        snapshot = await self.services_manager.collect()
        services_view.show_snapshot(snapshot)

    def _containers_snapshot(self):
        try:
            return self.query_one(ContainersView).snapshot
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
                    storage_provider=LocalStorageProvider(config.storage.mounts),
                    disk_health_provider=SmartctlDiskHealthProvider(
                        devices=config.storage.disks
                    ),
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
