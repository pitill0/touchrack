from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from time import monotonic

from homelab_console.models import DiskHealthSnapshot
from homelab_console.providers.disk_health import _disk_from_json
from homelab_console.transports import CommandRunner


class RemoteSmartctlDiskHealthProvider:
    """Collect allowlisted SMART JSON through a privileged remote helper."""

    def __init__(
        self,
        runner: CommandRunner,
        *,
        devices: tuple[str, ...],
        helper: str,
        cache_seconds: float = 60.0,
    ) -> None:
        self.runner = runner
        self.devices = devices
        self.helper = helper
        self.cache_seconds = max(0.0, cache_seconds)
        self._cached: DiskHealthSnapshot | None = None
        self._cached_at = 0.0
        self._inflight: asyncio.Task[DiskHealthSnapshot] | None = None

    async def collect(self) -> DiskHealthSnapshot:
        now = monotonic()
        if self._cached is not None and now - self._cached_at < self.cache_seconds:
            return self._cached
        task = self._inflight
        if task is None or task.done():
            task = asyncio.create_task(self._collect_remote())
            self._inflight = task
        try:
            snapshot = await asyncio.shield(task)
        finally:
            if task.done() and self._inflight is task:
                self._inflight = None
        # Successful SMART probes are intentionally slow-cadence. Transport,
        # helper and permission failures are not cached here; the shared SSH
        # circuit breaker already provides short backoff and quick recovery.
        if snapshot.available:
            self._cached = snapshot
            self._cached_at = monotonic()
        return snapshot

    async def shutdown(self) -> None:
        """Wait for cooperative collection without abandoning its work."""
        task = self._inflight
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _collect_remote(self) -> DiskHealthSnapshot:
        collected_at = datetime.now(timezone.utc)
        disks = []
        errors: list[str] = []
        for device in self.devices:
            try:
                result = await self.runner.run(
                    ("sudo", "-n", self.helper, device),
                    timeout=12.0,
                )
                payload = json.loads(result.stdout or "{}")
                if not isinstance(payload, dict) or not payload:
                    detail = result.stderr.strip() or f"exit status {result.returncode}"
                    raise ValueError(detail)
                disks.append(_disk_from_json(payload, fallback_device=device))
            except Exception as exc:
                errors.append(f"{device}: {exc}")
        return DiskHealthSnapshot(
            disks=tuple(disks),
            collected_at=collected_at,
            available=bool(disks),
            error="; ".join(errors) if errors else None,
        )
