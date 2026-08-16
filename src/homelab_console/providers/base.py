from __future__ import annotations

from typing import Protocol

from homelab_console.models import (
    ContainersSnapshot,
    DiskHealthSnapshot,
    HostSnapshot,
    StorageSnapshot,
)


class HostProvider(Protocol):
    async def collect(self) -> HostSnapshot:
        """Return one immutable snapshot of the host."""


class ContainersProvider(Protocol):
    async def collect(self) -> ContainersSnapshot:
        """Return one immutable snapshot of local containers."""



class StorageProvider(Protocol):
    async def collect(self) -> StorageSnapshot:
        """Return one immutable snapshot of local filesystems."""



class DiskHealthProvider(Protocol):
    async def collect(self) -> DiskHealthSnapshot:
        """Return optional SMART/NVMe health information for physical disks."""
