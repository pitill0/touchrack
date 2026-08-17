from __future__ import annotations

from rich.text import Text

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from homelab_console.models import (
    DiskHealthInfo,
    DiskHealthSnapshot,
    FilesystemInfo,
    StorageSnapshot,
)


WARNING_AT = 75.0
CRITICAL_AT = 90.0
BAR_WIDTH = 34
VISIBLE_FILESYSTEMS = 4
VISIBLE_DISKS = 4


def storage_severity(usage_percent: float) -> str:
    if usage_percent >= CRITICAL_AT:
        return "critical"
    if usage_percent >= WARNING_AT:
        return "warning"
    return "normal"


class FilesystemCard(Button):
    """Persistent two-row touch target for one filesystem."""

    def __init__(self, index: int) -> None:
        super().__init__(
            "",
            id=f"storage-open-{index}",
            classes="storage-filesystem storage-filesystem-empty",
            disabled=True,
        )
        self.filesystem: FilesystemInfo | None = None

    def set_filesystem(self, filesystem: FilesystemInfo | None) -> None:
        self.remove_class(
            "storage-filesystem-empty",
            "normal",
            "warning",
            "critical",
        )
        self.filesystem = filesystem

        if filesystem is None:
            self.label = ""
            self.disabled = True
            self.add_class("storage-filesystem-empty")
            return

        severity = storage_severity(filesystem.usage_percent)
        self.add_class(severity)
        self.disabled = False

        mount = filesystem.mountpoint
        if len(mount) > 12:
            mount = f"…{mount[-11:]}"

        percent = max(0.0, min(100.0, filesystem.usage_percent))
        filled = round((percent / 100.0) * BAR_WIDTH)
        bar = "█" * filled + "░" * (BAR_WIDTH - filled)

        bar_style = {
            "normal": "bold cyan",
            "warning": "bold yellow",
            "critical": "bold red",
        }[severity]

        self.label = Text.assemble(
            (f"{mount:<12}", "bold"),
            (f"{percent:>4.0f}% ", "bold"),
            (bar, bar_style),
        )


class StorageDetailScreen(ModalScreen[None]):
    """Read-only filesystem detail for a touched STORAGE row."""

    def __init__(self, filesystem: FilesystemInfo) -> None:
        super().__init__()
        self.filesystem = filesystem

    def compose(self) -> ComposeResult:
        severity = storage_severity(self.filesystem.usage_percent)
        state_label = {
            "normal": "● OK",
            "warning": "● WARN",
            "critical": "● FULL",
        }[severity]

        with Vertical(id="storage-detail-dialog"):
            with Horizontal(id="storage-detail-header"):
                yield Button("BACK", id="storage-detail-back", classes="detail-back")
                yield Label(self.filesystem.mountpoint, id="storage-detail-title")
                yield Static(
                    state_label,
                    id="storage-detail-state",
                    classes=severity,
                )

            rows = (
                ("DEVICE", self.filesystem.device),
                ("FILESYSTEM", self.filesystem.filesystem or "unknown"),
                ("MOUNT", self.filesystem.mountpoint),
                ("USED", _format_bytes(self.filesystem.used)),
                ("FREE", _format_bytes(self.filesystem.free)),
                ("TOTAL", _format_bytes(self.filesystem.total)),
                ("USAGE", f"{self.filesystem.usage_percent:.1f}%"),
                ("OPTIONS", self.filesystem.mount_options or "--"),
            )
            for label, value in rows:
                yield Static(
                    f"{label:<10}  {value}",
                    classes="storage-detail-line",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "storage-detail-back":
            self.dismiss()


def disk_health_label(disk: DiskHealthInfo) -> tuple[str, str]:
    if disk.smart_passed is False:
        return "● FAIL", "critical"
    if disk.smart_passed is True:
        return "● OK", "normal"
    return "? UNK", "unknown"


class DiskHealthCard(Button):
    """Persistent two-row touch target for one physical disk."""

    def __init__(self, index: int) -> None:
        super().__init__(
            "",
            id=f"storage-disk-open-{index}",
            classes="storage-disk storage-disk-empty",
            disabled=True,
        )
        self.disk: DiskHealthInfo | None = None

    def set_disk(self, disk: DiskHealthInfo | None) -> None:
        self.remove_class(
            "storage-disk-empty",
            "normal",
            "critical",
            "unknown",
        )
        self.disk = disk
        if disk is None:
            self.label = ""
            self.disabled = True
            self.add_class("storage-disk-empty")
            return

        health, severity = disk_health_label(disk)
        self.add_class(severity)
        self.disabled = False

        temperature = (
            f"{disk.temperature_c:.0f}°C"
            if disk.temperature_c is not None
            else "--°C"
        )
        protocol = (disk.protocol or "--").upper()[:6]
        if disk.percentage_used is not None:
            extra = f"wear {disk.percentage_used}%"
        elif disk.size is not None:
            extra = _format_bytes_compact(disk.size)
        else:
            extra = ""

        style = {
            "normal": "bold bright_green",
            "critical": "bold red",
            "unknown": "dim",
        }[severity]
        self.label = Text.assemble(
            (f"{disk.name[:12]:<12}", "bold"),
            (f"{temperature:>5}  ", "bold"),
            (f"{health:<7}", style),
            (f"{protocol:<7}", "dim"),
            (extra, "dim"),
        )


class DiskDetailScreen(ModalScreen[None]):
    """Read-only SMART/NVMe detail for one physical disk."""

    def __init__(self, disk: DiskHealthInfo) -> None:
        super().__init__()
        self.disk = disk

    def compose(self) -> ComposeResult:
        health, severity = disk_health_label(self.disk)
        with Vertical(id="disk-detail-dialog"):
            with Horizontal(id="disk-detail-header"):
                yield Button("BACK", id="disk-detail-back", classes="detail-back")
                yield Label(self.disk.name.upper(), id="disk-detail-title")
                yield Static(
                    health,
                    id="disk-detail-state",
                    classes=severity,
                )

            rows: list[tuple[str, str]] = [
                ("DEVICE", self.disk.device),
                ("MODEL", self.disk.model or "--"),
                ("SERIAL", self.disk.serial or "--"),
                ("PROTOCOL", self.disk.protocol or "--"),
                ("SIZE", _format_bytes(self.disk.size) if self.disk.size is not None else "--"),
                (
                    "TEMP",
                    f"{self.disk.temperature_c:.0f}°C"
                    if self.disk.temperature_c is not None
                    else "--",
                ),
                (
                    "POWER ON",
                    f"{self.disk.power_on_hours} h"
                    if self.disk.power_on_hours is not None
                    else "--",
                ),
                (
                    "CYCLES",
                    str(self.disk.power_cycles)
                    if self.disk.power_cycles is not None
                    else "--",
                ),
            ]
            rows.extend(self.disk.details)
            for label, value in rows:
                yield Static(
                    f"{label[:10]:<10}  {value}",
                    classes="disk-detail-line",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "disk-detail-back":
            self.dismiss()


class StorageView(Vertical):
    """Compact 64×18 local-filesystem health view."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.snapshot: StorageSnapshot | None = None
        self.disk_snapshot: DiskHealthSnapshot | None = None
        self.mode = "files"
        self._all_filesystems: tuple[FilesystemInfo, ...] = ()
        self._visible_filesystems: tuple[FilesystemInfo, ...] = ()
        self._all_disks: tuple[DiskHealthInfo, ...] = ()
        self._visible_disks: tuple[DiskHealthInfo, ...] = ()
        self._page = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="storage-header"):
            yield Button("≡", id="menu-button-storage", classes="flat-control")
            yield Label("STORAGE", id="storage-title")
            yield Button(
                Text("FILES", no_wrap=True),
                id="storage-mode-files",
                classes="storage-mode-button selected flat-control",
            )
            yield Button(
                Text("DISKS", no_wrap=True),
                id="storage-mode-disks",
                classes="storage-mode-button flat-control",
            )
            yield Static("● --", id="storage-health")
            yield Button(
                "REF 5s",
                id="refresh-interval-storage",
                classes="refresh-interval-button flat-control",
            )
            yield Button(
                "SLEEP 1m",
                id="screen-blank-storage",
                classes="screen-blank-button flat-control",
            )

        with Grid(id="storage-grid"):
            for index in range(VISIBLE_FILESYSTEMS):
                yield FilesystemCard(index)

        with Grid(id="storage-disks-grid", classes="hidden"):
            for index in range(VISIBLE_DISKS):
                yield DiskHealthCard(index)

        # Preserve detailed provider/configuration feedback without spending a
        # dedicated sub-navigation row below the header.
        yield Static("", id="storage-status")

        with Horizontal(id="storage-pager", classes="pager-hidden"):
            yield Button(
                "◀",
                id="storage-page-prev",
                classes="storage-page-button flat-control",
            )
            yield Static("1 / 1", id="storage-page-indicator")
            yield Button(
                "▶",
                id="storage-page-next",
                classes="storage-page-button flat-control",
            )

    def show_refreshing(self) -> None:
        if self.snapshot is None:
            status = self.query_one("#storage-status", Static)
            status.update("Reading local filesystems…")
            status.remove_class("has-warning", "has-error")

    def _render_dom_ready(self) -> bool:
        """Return whether the mounted child DOM is still safe to update."""
        if not self.is_mounted:
            return False

        required = [
            "#storage-status",
            "#storage-health",
            "#storage-pager",
            "#storage-page-indicator",
            "#storage-page-prev",
            "#storage-page-next",
        ]
        if self.mode == "files":
            required.extend(
                f"#storage-open-{index}"
                for index in range(VISIBLE_FILESYSTEMS)
            )
        else:
            required.extend(
                f"#storage-disk-open-{index}"
                for index in range(VISIBLE_DISKS)
            )

        return all(len(self.query(selector)) == 1 for selector in required)

    def show_snapshot(self, snapshot: StorageSnapshot) -> None:
        self.snapshot = snapshot
        self._all_filesystems = snapshot.filesystems

        # A provider worker may complete while Textual is tearing down the
        # screen. Preserve the fresh snapshot, but don't query children that
        # are already disappearing from the DOM.
        if not self._render_dom_ready():
            return

        if self.mode == "files":
            if self._page >= self._page_count:
                self._page = max(0, self._page_count - 1)
            self._render_page()

        status = self.query_one("#storage-status", Static)
        health = self.query_one("#storage-health", Static)
        status.remove_class("has-warning", "has-error")
        health.remove_class("healthy", "degraded", "unavailable")

        if snapshot.error:
            status.update(snapshot.error)
            status.add_class("has-error")
            health.update("● DOWN")
            health.add_class("unavailable")
            return

        if not snapshot.filesystems:
            status.update("No local filesystems detected")
            status.add_class("has-warning")
            health.update("● WARN")
            health.add_class("degraded")
            return

        critical = any(
            filesystem.usage_percent >= CRITICAL_AT
            for filesystem in snapshot.filesystems
        )
        warnings = any(
            WARNING_AT <= filesystem.usage_percent < CRITICAL_AT
            for filesystem in snapshot.filesystems
        )

        # Capacity severity is already encoded in the global health indicator
        # and in each filesystem bar. Keep text for exceptional provider states.
        status.update("")
        self._update_combined_health(
            filesystem_critical=critical,
            filesystem_warning=warnings,
        )

    def show_disk_snapshot(self, snapshot: DiskHealthSnapshot) -> None:
        self.disk_snapshot = snapshot
        self._all_disks = snapshot.disks

        if not self._render_dom_ready():
            return

        if self.mode == "disks":
            if self._page >= self._page_count:
                self._page = max(0, self._page_count - 1)
            self._render_page()
        else:
            self._update_mode_buttons()

        self._update_combined_health()

    def _update_combined_health(
        self,
        *,
        filesystem_critical: bool | None = None,
        filesystem_warning: bool | None = None,
    ) -> None:
        health = self.query_one("#storage-health", Static)
        health.remove_class("healthy", "degraded", "unavailable")

        if filesystem_critical is None or filesystem_warning is None:
            filesystems = self.snapshot.filesystems if self.snapshot else ()
            filesystem_critical = any(
                item.usage_percent >= CRITICAL_AT for item in filesystems
            )
            filesystem_warning = any(
                WARNING_AT <= item.usage_percent < CRITICAL_AT
                for item in filesystems
            )

        disk_failed = any(
            disk.smart_passed is False for disk in self._all_disks
        )

        if disk_failed or filesystem_critical:
            health.update("● DOWN")
            health.add_class("unavailable")
        elif filesystem_warning:
            health.update("● WARN")
            health.add_class("degraded")
        else:
            health.update("● OK")
            health.add_class("healthy")

    def _update_mode_buttons(self) -> None:
        files_button = self.query_one("#storage-mode-files", Button)
        disks_button = self.query_one("#storage-mode-disks", Button)
        files_selected = self.mode == "files"
        disks_selected = self.mode == "disks"
        files_button.set_class(files_selected, "selected")
        disks_button.set_class(disks_selected, "selected")
        files_button.label = (
            Text("FILES", no_wrap=True)
            if files_selected
            else Text("FILES", no_wrap=True)
        )
        disks_button.label = (
            Text("DISKS", no_wrap=True)
            if disks_selected
            else Text("DISKS", no_wrap=True)
        )

        status = self.query_one("#storage-status", Static)
        if self.mode == "disks" and self.disk_snapshot is not None:
            if not self.disk_snapshot.available:
                status.update(self.disk_snapshot.error or "smartctl unavailable")
            elif self.disk_snapshot.error and not self.disk_snapshot.disks:
                status.update(self.disk_snapshot.error)
            elif not self.disk_snapshot.disks:
                status.update("No SMART/NVMe disks detected")
            else:
                status.update("")
        elif self.snapshot is not None and self.snapshot.error:
            status.update(self.snapshot.error)
        elif self.snapshot is not None and not self.snapshot.filesystems:
            status.update("No local filesystems detected")
        else:
            status.update("")

    def set_mode(self, mode: str) -> None:
        if mode not in {"files", "disks"} or mode == self.mode:
            return
        self.mode = mode
        self._page = 0
        self.query_one("#storage-grid").set_class(mode != "files", "hidden")
        self.query_one("#storage-disks-grid").set_class(mode != "disks", "hidden")
        self._update_mode_buttons()
        self._render_page()

    @property
    def _page_count(self) -> int:
        total = (
            len(self._all_filesystems)
            if self.mode == "files"
            else len(self._all_disks)
        )
        page_size = VISIBLE_FILESYSTEMS if self.mode == "files" else VISIBLE_DISKS
        if total == 0:
            return 1
        return max(1, (total + page_size - 1) // page_size)

    def _render_page(self) -> None:
        if self.mode == "files":
            start = self._page * VISIBLE_FILESYSTEMS
            end = start + VISIBLE_FILESYSTEMS
            self._visible_filesystems = self._all_filesystems[start:end]
            for index in range(VISIBLE_FILESYSTEMS):
                card = self.query_one(f"#storage-open-{index}", FilesystemCard)
                card.set_filesystem(
                    self._visible_filesystems[index]
                    if index < len(self._visible_filesystems)
                    else None
                )
        else:
            start = self._page * VISIBLE_DISKS
            end = start + VISIBLE_DISKS
            self._visible_disks = self._all_disks[start:end]
            for index in range(VISIBLE_DISKS):
                card = self.query_one(f"#storage-disk-open-{index}", DiskHealthCard)
                card.set_disk(
                    self._visible_disks[index]
                    if index < len(self._visible_disks)
                    else None
                )

        pager = self.query_one("#storage-pager", Horizontal)
        indicator = self.query_one("#storage-page-indicator", Static)
        prev_button = self.query_one("#storage-page-prev", Button)
        next_button = self.query_one("#storage-page-next", Button)

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
        if event.button.id == "storage-mode-files":
            self.set_mode("files")
            event.stop()
        elif event.button.id == "storage-mode-disks":
            self.set_mode("disks")
            event.stop()
        elif event.button.id == "storage-page-prev":
            self.page_previous()
            event.stop()
        elif event.button.id == "storage-page-next":
            self.page_next()
            event.stop()

    def filesystem_for_button(self, button_id: str) -> FilesystemInfo | None:
        try:
            index = int(button_id.removeprefix("storage-open-"))
        except ValueError:
            return None
        if 0 <= index < len(self._visible_filesystems):
            return self._visible_filesystems[index]
        return None

    def disk_for_button(self, button_id: str) -> DiskHealthInfo | None:
        try:
            index = int(button_id.removeprefix("storage-disk-open-"))
        except ValueError:
            return None
        if 0 <= index < len(self._visible_disks):
            return self._visible_disks[index]
        return None


def _format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB", "PiB"):
        if amount < 1024 or suffix == "PiB":
            return f"{amount:.1f} {suffix}" if suffix != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} PiB"



def _format_bytes_compact(value: int) -> str:
    amount = float(max(0, value))
    for suffix in ("B", "K", "M", "G", "T", "P"):
        if amount < 1024 or suffix == "P":
            if suffix == "B":
                return f"{int(amount)}B"
            return f"{amount:.1f}{suffix}"
        amount /= 1024
    return f"{amount:.1f}P"
