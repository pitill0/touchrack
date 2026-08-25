from __future__ import annotations

from datetime import datetime, timezone

from homelab_console.models import (
    ContainersSnapshot,
    DiskHealthSnapshot,
    StorageSnapshot,
)


class UnavailableContainersProvider:
    """Represent a capability that is not implemented for this node."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def collect(self) -> ContainersSnapshot:
        return ContainersSnapshot(
            containers=(),
            collected_at=datetime.now(timezone.utc),
            error=self.reason,
        )


class UnavailableStorageProvider:
    """Represent a capability that is not implemented for this node."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def collect(self) -> StorageSnapshot:
        return StorageSnapshot(
            filesystems=(),
            collected_at=datetime.now(timezone.utc),
            error=self.reason,
        )


class UnavailableDiskHealthProvider:
    """Represent a capability that is not implemented for this node."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def collect(self) -> DiskHealthSnapshot:
        return DiskHealthSnapshot(
            disks=(),
            collected_at=datetime.now(timezone.utc),
            available=False,
            error=self.reason,
        )
