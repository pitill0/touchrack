from __future__ import annotations

from collections import deque

from textual.app import ComposeResult
from textual.containers import Grid, Horizontal, Vertical
from textual.widgets import Button, Label, Static

from homelab_console.models import HostSnapshot


class InlineBar(Static):
    """Single-line Unicode meter with per-resource warning thresholds."""

    def __init__(
        self,
        *,
        width: int = 34,
        warning_at: float = 60.0,
        critical_at: float = 85.0,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="inline-bar")
        self.bar_width = width
        self.warning_at = warning_at
        self.critical_at = critical_at

    def set_value(self, value: float | None) -> None:
        self.remove_class("low", "medium", "high", "unknown")
        if value is None:
            self.add_class("unknown")
            self.update("░" * self.bar_width)
            return
        clamped = max(0.0, min(100.0, value))
        filled = round((clamped / 100.0) * self.bar_width)
        self.update("█" * filled + "░" * (self.bar_width - filled))
        if clamped >= self.critical_at:
            self.add_class("high")
        elif clamped >= self.warning_at:
            self.add_class("medium")
        else:
            self.add_class("low")


class ResourceMetricRow(Vertical):
    """Metric label and value on the left, long aligned bar on the right."""

    def __init__(
        self,
        title: str,
        *,
        warning_at: float,
        critical_at: float,
        id: str,
    ) -> None:
        super().__init__(id=id, classes="resource-metric-row")
        self.title = title
        self.warning_at = warning_at
        self.critical_at = critical_at

    def compose(self) -> ComposeResult:
        with Horizontal(classes="resource-metric-main"):
            yield Label(self.title, classes="resource-metric-title")
            yield Label("—", classes="resource-metric-value")
            yield InlineBar(
                width=48,
                warning_at=self.warning_at,
                critical_at=self.critical_at,
            )
        yield Label("Waiting for data", classes="resource-metric-detail")

    def set_metric(self, value: str, *, percentage: float | None, detail: str) -> None:
        self.query_one(".resource-metric-value", Label).update(value)
        self.query_one(".resource-metric-detail", Label).update(detail)
        self.query_one(InlineBar).set_value(percentage)


class TemperatureRow(Horizontal):
    """Compact host footer: temperature on the left, uptime on the right."""

    def compose(self) -> ComposeResult:
        yield Label("TEMP", classes="temperature-title")
        yield Label("—", id="temperature-value")
        yield Static("● UNKNOWN", id="temperature-state")
        yield Label("UP —", id="host-uptime")

    def set_temperature(self, temperature: float | None) -> None:
        value = self.query_one("#temperature-value", Label)
        state = self.query_one("#temperature-state", Static)
        state.remove_class("normal", "warm", "hot", "unknown")
        if temperature is None:
            value.update("—")
            state.update("● UNKNOWN")
            state.add_class("unknown")
            return
        value.update(f"{temperature:.0f}°C")
        if temperature >= 65:
            state.update("● HOT")
            state.add_class("hot")
        elif temperature >= 50:
            state.update("● WARM")
            state.add_class("warm")
        else:
            state.update("● NORMAL")
            state.add_class("normal")

    def set_uptime(self, uptime_seconds: float, *, error: str | None = None) -> None:
        uptime = self.query_one("#host-uptime", Label)
        uptime.set_class(error is not None, "has-error")
        if error:
            uptime.update("DATA ERROR")
        else:
            uptime.update(f"UP {_format_uptime(uptime_seconds)}")


class HostView(Vertical):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.snapshot: HostSnapshot | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="host-command-bar"):
            yield Button("≡", id="menu-button", classes="flat-control")
            yield Label("LOCAL HOST", id="host-name")
            yield Static("● --", id="host-health")
            yield Button("REF 5s", id="refresh-interval-host", classes="refresh-interval-button flat-control")
            yield Button("SLEEP 1m", id="screen-blank-host", classes="screen-blank-button flat-control")

        with Vertical(id="resource-metrics"):
            # Thresholds are resource-specific: brief CPU bursts are normal,
            # while sustained memory/disk pressure deserves earlier warning.
            yield ResourceMetricRow("CPU", warning_at=50, critical_at=80, id="metric-cpu")
            yield ResourceMetricRow("MEM", warning_at=60, critical_at=85, id="metric-memory")
            yield ResourceMetricRow("DISK", warning_at=75, critical_at=90, id="metric-disk")
            yield TemperatureRow(id="metric-temperature")


    def show_snapshot(self, snapshot: HostSnapshot) -> None:
        self.snapshot = snapshot
        memory_percent = _percentage(snapshot.memory_used, snapshot.memory_total)
        disk_percent = _percentage(snapshot.disk_used, snapshot.disk_total)

        self.query_one("#host-name", Label).update(snapshot.hostname.upper())
        health = self.query_one("#host-health", Static)
        health.remove_class("healthy", "degraded", "unavailable")
        health_class = snapshot.health.lower()
        health.add_class(health_class)
        health.update("● OK" if health_class == "healthy" else "● WARN" if health_class == "degraded" else "● DOWN")

        self.query_one("#metric-cpu", ResourceMetricRow).set_metric(
            f"{snapshot.cpu_percent:.0f}%",
            percentage=snapshot.cpu_percent,
            detail=f"Load {_format_load_short(snapshot.load_average)}",
        )
        self.query_one("#metric-memory", ResourceMetricRow).set_metric(
            f"{memory_percent:.0f}%" if memory_percent is not None else "—",
            percentage=memory_percent,
            detail=f"{_format_bytes(snapshot.memory_used)} / {_format_bytes(snapshot.memory_total)}",
        )
        self.query_one("#metric-disk", ResourceMetricRow).set_metric(
            f"{disk_percent:.0f}%" if disk_percent is not None else "—",
            percentage=disk_percent,
            detail=f"{_format_bytes(snapshot.disk_used)} / {_format_bytes(snapshot.disk_total)}",
        )
        temperature_row = self.query_one("#metric-temperature", TemperatureRow)
        temperature_row.set_temperature(snapshot.temperature_c)
        temperature_row.set_uptime(snapshot.uptime_seconds, error=snapshot.error)

    def show_refreshing(self) -> None:
        """No animated host footer: refresh cadence is already shown in the header."""


def _percentage(used: int, total: int) -> float | None:
    if total <= 0:
        return None
    return max(0.0, min(100.0, (used / total) * 100.0))


def _format_uptime(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    days, remainder = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for suffix in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or suffix == "TiB":
            return f"{amount:.1f} {suffix}" if suffix != "B" else f"{int(amount)} B"
        amount /= 1024
    return f"{amount:.1f} TiB"


def _format_load(load: tuple[float, float, float] | None) -> str:
    if load is None:
        return "Unavailable"
    return f"{load[0]:.2f} · {load[1]:.2f} · {load[2]:.2f}"


def _format_load_short(load: tuple[float, float, float] | None) -> str:
    if load is None:
        return "—"
    return f"{load[0]:.2f}"
