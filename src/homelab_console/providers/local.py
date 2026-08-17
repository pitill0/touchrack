from __future__ import annotations

import asyncio
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import psutil

from homelab_console.models import HostSnapshot


class LocalHostProvider:
    """Collect local host data without coupling the UI to psutil or /proc."""

    def __init__(self, disk_path: str = "/") -> None:
        self.disk_path = disk_path
        self._collect_task: asyncio.Task[HostSnapshot] | None = None

    async def collect(self) -> HostSnapshot:
        # Textual may cancel an exclusive refresh worker while _collect_sync()
        # is already running in a thread. Keep one provider-level collection
        # alive so the next refresh reuses it instead of starting another
        # psutil / filesystem probe in parallel.
        task = self._collect_task
        if task is None or task.done():
            task = asyncio.create_task(self._collect_once())
            self._collect_task = task
            task.add_done_callback(self._clear_collect_task)
        return await asyncio.shield(task)

    async def _collect_once(self) -> HostSnapshot:
        return await asyncio.to_thread(self._collect_sync)

    def _clear_collect_task(self, task: asyncio.Task[HostSnapshot]) -> None:
        if self._collect_task is task:
            self._collect_task = None

    def _collect_sync(self) -> HostSnapshot:
        collected_at = datetime.now(timezone.utc)
        try:
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage(self.disk_path)
            boot_time = psutil.boot_time()
            return HostSnapshot(
                hostname=socket.gethostname(),
                uptime_seconds=max(0.0, collected_at.timestamp() - boot_time),
                cpu_percent=psutil.cpu_percent(interval=0.15),
                load_average=self._load_average(),
                memory_used=memory.used,
                memory_total=memory.total,
                disk_used=disk.used,
                disk_total=disk.total,
                temperature_c=self._temperature(),
                ip_address=self._primary_ip(),
                collected_at=collected_at,
            )
        except Exception as exc:  # provider boundary: return state, don't crash UI
            return HostSnapshot(
                hostname=socket.gethostname(),
                uptime_seconds=0,
                cpu_percent=0,
                load_average=None,
                memory_used=0,
                memory_total=0,
                disk_used=0,
                disk_total=0,
                temperature_c=None,
                ip_address=None,
                collected_at=collected_at,
                error=f"{type(exc).__name__}: {exc}",
            )

    @staticmethod
    def _load_average() -> tuple[float, float, float] | None:
        try:
            return tuple(float(value) for value in os.getloadavg())
        except (AttributeError, OSError):
            return None

    @staticmethod
    def _primary_ip() -> str | None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 80))
            return str(sock.getsockname()[0])
        except OSError:
            return None
        finally:
            sock.close()

    @staticmethod
    def _temperature() -> float | None:
        try:
            readings = psutil.sensors_temperatures(fahrenheit=False)
        except (AttributeError, OSError):
            readings = {}

        preferred = ("coretemp", "k10temp", "cpu_thermal", "soc_thermal")
        for group in preferred:
            for entry in readings.get(group, []):
                if entry.current is not None:
                    return float(entry.current)

        for entries in readings.values():
            for entry in entries:
                if entry.current is not None:
                    return float(entry.current)

        # Conservative Linux fallback for common thermal zones.
        for path in sorted(Path("/sys/class/thermal").glob("thermal_zone*/temp")):
            try:
                raw = float(path.read_text(encoding="utf-8").strip())
                return raw / 1000 if raw > 1000 else raw
            except (OSError, ValueError):
                continue
        return None
