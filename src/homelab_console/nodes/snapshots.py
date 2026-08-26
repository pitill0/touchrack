from __future__ import annotations

from dataclasses import replace
from typing import Protocol, TypeVar, cast


class CacheableSnapshot(Protocol):
    error: str | None
    stale: bool
    stale_error: str | None


SnapshotT = TypeVar("SnapshotT", bound=CacheableSnapshot)


class LastSuccessfulSnapshotStore:
    """Retain successful capability snapshots without hiding fresh failures."""

    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], CacheableSnapshot] = {}

    def resolve(
        self,
        node_id: str,
        capability: str,
        snapshot: SnapshotT,
    ) -> SnapshotT:
        key = (node_id, capability)
        if snapshot.error is None:
            fresh = replace(snapshot, stale=False, stale_error=None)
            self._snapshots[key] = fresh
            return cast(SnapshotT, fresh)

        previous = self._snapshots.get(key)
        if previous is None:
            return snapshot
        return cast(
            SnapshotT,
            replace(previous, stale=True, stale_error=snapshot.error),
        )
