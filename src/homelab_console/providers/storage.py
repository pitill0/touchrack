from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import psutil

from homelab_console.models import FilesystemInfo, StorageSnapshot


_PSEUDO_FILESYSTEMS = frozenset(
    {
        "autofs",
        "binfmt_misc",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "nsfs",
        "overlay",
        "proc",
        "pstore",
        "ramfs",
        "rpc_pipefs",
        "securityfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)


class LocalStorageProvider:
    """Collect useful local filesystem usage without coupling the UI to psutil.

    With no explicit mount list, psutil supplies mounted physical filesystems and
    pseudo-filesystems are filtered out. An explicit mount list is supported now
    so a later STORAGE config layer can select exactly which filesystems appear.
    """

    def __init__(self, mountpoints: tuple[str, ...] | None = None) -> None:
        self.mountpoints = (
            tuple(mountpoints) if mountpoints is not None else None
        )
        self._collect_task: asyncio.Task[StorageSnapshot] | None = None

    async def collect(self) -> StorageSnapshot:
        # Cancellation of asyncio.to_thread() does not stop the worker thread.
        # Keep one filesystem collection in flight so a replacement Textual
        # refresh cannot overlap psutil mount / disk-usage probes.
        task = self._collect_task
        if task is None or task.done():
            task = asyncio.create_task(self._collect_once())
            self._collect_task = task
            task.add_done_callback(self._clear_collect_task)
        return await asyncio.shield(task)

    async def _collect_once(self) -> StorageSnapshot:
        return await asyncio.to_thread(self._collect_sync)

    def _clear_collect_task(self, task: asyncio.Task[StorageSnapshot]) -> None:
        if self._collect_task is task:
            self._collect_task = None

    def _collect_sync(self) -> StorageSnapshot:
        collected_at = datetime.now(timezone.utc)
        try:
            partitions = tuple(psutil.disk_partitions(all=False))
            selected = self._select_partitions(partitions)
            filesystems: list[FilesystemInfo] = []

            for partition in selected:
                try:
                    usage = psutil.disk_usage(partition.mountpoint)
                except (OSError, PermissionError):
                    # One inaccessible mount must not make STORAGE unavailable.
                    continue

                filesystems.append(
                    FilesystemInfo(
                        device=str(partition.device),
                        mountpoint=str(partition.mountpoint),
                        filesystem=str(partition.fstype),
                        mount_options=str(partition.opts),
                        total=int(usage.total),
                        used=int(usage.used),
                        free=int(usage.free),
                        usage_percent=float(usage.percent),
                    )
                )

            if self.mountpoints is None:
                filesystems.sort(key=_filesystem_sort_key)
            return StorageSnapshot(
                filesystems=tuple(filesystems),
                collected_at=collected_at,
            )
        except Exception as exc:  # provider boundary: return state, don't crash UI
            return StorageSnapshot(
                filesystems=(),
                collected_at=collected_at,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _select_partitions(self, partitions: tuple[object, ...]) -> tuple[object, ...]:
        by_mountpoint: dict[str, object] = {}

        for partition in partitions:
            mountpoint = str(getattr(partition, "mountpoint", ""))
            filesystem = str(getattr(partition, "fstype", "")).casefold()

            if not mountpoint:
                continue
            if filesystem in _PSEUDO_FILESYSTEMS:
                continue
            by_mountpoint.setdefault(mountpoint, partition)

        if self.mountpoints is None:
            return tuple(by_mountpoint.values())

        return tuple(
            by_mountpoint[mountpoint]
            for mountpoint in self.mountpoints
            if mountpoint in by_mountpoint
        )


def _filesystem_sort_key(filesystem: FilesystemInfo) -> tuple[int, str]:
    """Keep root first, then stable alphabetical mount ordering."""
    return (0 if filesystem.mountpoint == "/" else 1, filesystem.mountpoint.casefold())
