from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from time import monotonic
from typing import Any

from homelab_console.models import DiskHealthInfo, DiskHealthSnapshot


class SmartctlDiskHealthProvider:
    """Optional disk-health provider backed by smartctl JSON output.

    smartctl is deliberately optional: missing tooling never prevents TouchRack
    from starting. Results are cached because full SMART/NVMe probes do not need
    to run on the application's fast UI refresh cadence.
    """

    def __init__(
        self,
        binary: str = "smartctl",
        cache_seconds: float = 60.0,
        devices: tuple[str, ...] | None = None,
    ) -> None:
        self.binary = binary
        self.cache_seconds = max(0.0, cache_seconds)
        self.devices = tuple(devices) if devices is not None else None
        self._cached: DiskHealthSnapshot | None = None
        self._cached_at = 0.0
        self._inflight: asyncio.Task[DiskHealthSnapshot] | None = None

    async def collect(self) -> DiskHealthSnapshot:
        now = monotonic()
        if (
            self._cached is not None
            and self.cache_seconds > 0
            and now - self._cached_at < self.cache_seconds
        ):
            return self._cached

        # Textual's exclusive refresh workers are cancelled when a newer refresh
        # starts. asyncio.to_thread() cannot stop work already running in its
        # thread, so a cancelled caller must not start a second SMART probe.
        # Keep one provider-owned task alive and let later callers join it.
        task = self._inflight
        if task is None or task.done():
            task = asyncio.create_task(self._refresh_cache())
            self._inflight = task

        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._inflight is task:
                self._inflight = None

    async def _refresh_cache(self) -> DiskHealthSnapshot:
        snapshot = await asyncio.to_thread(self._collect_sync)
        self._cached = snapshot
        self._cached_at = monotonic()
        return snapshot

    def _collect_sync(self) -> DiskHealthSnapshot:
        collected_at = datetime.now(timezone.utc)

        try:
            scan = self._run("--scan-open", "-j")
        except FileNotFoundError:
            return DiskHealthSnapshot(
                disks=(),
                collected_at=collected_at,
                available=False,
                error="smartctl not installed",
            )
        except OSError as exc:
            return DiskHealthSnapshot(
                disks=(),
                collected_at=collected_at,
                available=False,
                error=f"smartctl unavailable: {exc}",
            )

        try:
            scan_data = json.loads(scan.stdout or "{}")
        except json.JSONDecodeError as exc:
            return DiskHealthSnapshot(
                disks=(),
                collected_at=collected_at,
                available=True,
                error=f"smartctl scan JSON error: {exc.msg}",
            )

        scanned_devices = scan_data.get("devices")
        if not isinstance(scanned_devices, list):
            scanned_devices = []

        scan_by_name: dict[str, dict[str, Any]] = {}
        for device in scanned_devices:
            if not isinstance(device, dict):
                continue
            name = str(device.get("name") or "").strip()
            if name:
                scan_by_name.setdefault(name, device)

        if self.devices is None:
            selected_devices = tuple(scan_by_name)
        else:
            # Preserve explicit config order. A configured path that wasn't
            # returned by --scan-open is still probed directly.
            selected_devices = self.devices

        disks: list[DiskHealthInfo] = []
        errors: list[str] = []

        for name in selected_devices:
            scan_entry = scan_by_name.get(name, {})
            args = ["-a", "-j", name]
            device_type = str(scan_entry.get("type") or "").strip()
            if device_type:
                args.extend(["-d", device_type])

            try:
                result = self._run(*args)
            except OSError as exc:
                errors.append(f"{name}: {exc}")
                continue

            try:
                payload = json.loads(result.stdout or "{}")
            except json.JSONDecodeError:
                errors.append(f"{name}: invalid JSON")
                continue

            disks.append(_disk_from_json(payload, fallback_device=name))

        if self.devices is None:
            disks.sort(key=lambda disk: disk.name.casefold())
        error = "; ".join(errors) if errors and not disks else None
        return DiskHealthSnapshot(
            disks=tuple(disks),
            collected_at=collected_at,
            available=True,
            error=error,
        )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        # smartctl uses exit-status bit flags for SMART conditions. Useful JSON
        # may still be emitted on non-zero exit, so never use check=True here.
        return subprocess.run(
            (self.binary, *args),
            capture_output=True,
            text=True,
            check=False,
            timeout=12,
        )


def _disk_from_json(payload: dict[str, Any], fallback_device: str) -> DiskHealthInfo:
    device_obj = payload.get("device")
    if not isinstance(device_obj, dict):
        device_obj = {}

    device = str(device_obj.get("name") or fallback_device)
    name = Path(device).name or device
    protocol = _text(payload.get("device", {}).get("protocol") if isinstance(payload.get("device"), dict) else None)

    size_obj = payload.get("user_capacity")
    size = _int_value(size_obj.get("bytes")) if isinstance(size_obj, dict) else None

    smart_obj = payload.get("smart_status")
    smart_passed = smart_obj.get("passed") if isinstance(smart_obj, dict) else None
    if not isinstance(smart_passed, bool):
        smart_passed = None

    temp_obj = payload.get("temperature")
    temperature_c = _float_value(temp_obj.get("current")) if isinstance(temp_obj, dict) else None

    power_obj = payload.get("power_on_time")
    power_on_hours = _int_value(power_obj.get("hours")) if isinstance(power_obj, dict) else None
    power_cycles = _int_value(payload.get("power_cycle_count"))

    nvme = payload.get("nvme_smart_health_information_log")
    if not isinstance(nvme, dict):
        nvme = {}

    percentage_used = _int_value(nvme.get("percentage_used"))
    unsafe_shutdowns = _int_value(nvme.get("unsafe_shutdowns"))
    media_errors = _int_value(nvme.get("media_errors"))

    model = _text(payload.get("model_name")) or _text(payload.get("product"))
    serial = _text(payload.get("serial_number"))

    details: list[tuple[str, str]] = []
    if percentage_used is not None:
        details.append(("WEAR USED", f"{percentage_used}%"))
    if unsafe_shutdowns is not None:
        details.append(("UNSAFE OFF", str(unsafe_shutdowns)))
    if media_errors is not None:
        details.append(("MEDIA ERR", str(media_errors)))

    return DiskHealthInfo(
        device=device,
        name=name,
        model=model,
        serial=serial,
        protocol=protocol,
        size=size,
        temperature_c=temperature_c,
        smart_passed=smart_passed,
        power_on_hours=power_on_hours,
        power_cycles=power_cycles,
        percentage_used=percentage_used,
        unsafe_shutdowns=unsafe_shutdowns,
        media_errors=media_errors,
        details=tuple(details),
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_value(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, dict):
        for key in ("value", "blocks"):
            if key in value:
                return _int_value(value[key])
        return None
    try:
        return int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _float_value(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
