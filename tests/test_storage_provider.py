from __future__ import annotations

from types import SimpleNamespace

import pytest

from homelab_console.providers.storage import LocalStorageProvider


def _partition(
    device: str,
    mountpoint: str,
    fstype: str,
    opts: str = "rw",
) -> SimpleNamespace:
    return SimpleNamespace(
        device=device,
        mountpoint=mountpoint,
        fstype=fstype,
        opts=opts,
    )


def _usage(total: int, used: int, free: int, percent: float) -> SimpleNamespace:
    return SimpleNamespace(
        total=total,
        used=used,
        free=free,
        percent=percent,
    )


@pytest.mark.asyncio
async def test_storage_provider_collects_real_filesystems_and_filters_pseudo(
    monkeypatch,
) -> None:
    partitions = [
        _partition("/dev/nvme0n1p2", "/", "ext4", "rw,noatime"),
        _partition("tmpfs", "/run", "tmpfs", "rw,nosuid,nodev"),
        _partition("/dev/sda1", "/data", "xfs", "rw"),
        _partition("overlay", "/var/lib/containers/storage/overlay", "overlay", "rw"),
    ]
    usage = {
        "/": _usage(1000, 400, 600, 40.0),
        "/data": _usage(2000, 1500, 500, 75.0),
    }

    monkeypatch.setattr(
        "homelab_console.providers.storage.psutil.disk_partitions",
        lambda all=False: partitions,
    )
    monkeypatch.setattr(
        "homelab_console.providers.storage.psutil.disk_usage",
        lambda mountpoint: usage[mountpoint],
    )

    snapshot = await LocalStorageProvider().collect()

    assert snapshot.error is None
    assert snapshot.total == 2
    assert [item.mountpoint for item in snapshot.filesystems] == ["/", "/data"]

    root = snapshot.filesystems[0]
    assert root.device == "/dev/nvme0n1p2"
    assert root.filesystem == "ext4"
    assert root.mount_options == "rw,noatime"
    assert root.total == 1000
    assert root.used == 400
    assert root.free == 600
    assert root.usage_percent == 40.0


@pytest.mark.asyncio
async def test_storage_provider_honours_explicit_mount_selection(monkeypatch) -> None:
    partitions = [
        _partition("/dev/nvme0n1p2", "/", "ext4"),
        _partition("/dev/nvme0n1p3", "/home", "ext4"),
        _partition("/dev/sda1", "/backup", "xfs"),
    ]

    monkeypatch.setattr(
        "homelab_console.providers.storage.psutil.disk_partitions",
        lambda all=False: partitions,
    )
    monkeypatch.setattr(
        "homelab_console.providers.storage.psutil.disk_usage",
        lambda mountpoint: _usage(100, 25, 75, 25.0),
    )

    snapshot = await LocalStorageProvider(("/backup", "/")).collect()

    assert snapshot.error is None
    assert [item.mountpoint for item in snapshot.filesystems] == ["/backup", "/"]


@pytest.mark.asyncio
async def test_storage_provider_skips_one_inaccessible_mount(monkeypatch) -> None:
    partitions = [
        _partition("/dev/nvme0n1p2", "/", "ext4"),
        _partition("/dev/sdb1", "/offline", "ext4"),
    ]

    monkeypatch.setattr(
        "homelab_console.providers.storage.psutil.disk_partitions",
        lambda all=False: partitions,
    )

    def disk_usage(mountpoint: str) -> SimpleNamespace:
        if mountpoint == "/offline":
            raise PermissionError("denied")
        return _usage(100, 50, 50, 50.0)

    monkeypatch.setattr(
        "homelab_console.providers.storage.psutil.disk_usage",
        disk_usage,
    )

    snapshot = await LocalStorageProvider().collect()

    assert snapshot.error is None
    assert [item.mountpoint for item in snapshot.filesystems] == ["/"]


@pytest.mark.asyncio
async def test_storage_provider_returns_error_snapshot_on_collection_failure(
    monkeypatch,
) -> None:
    def fail(*, all: bool = False):
        raise OSError("boom")

    monkeypatch.setattr(
        "homelab_console.providers.storage.psutil.disk_partitions",
        fail,
    )

    snapshot = await LocalStorageProvider().collect()

    assert snapshot.filesystems == ()
    assert snapshot.error == "OSError: boom"
    assert snapshot.collected_at.tzinfo is not None



@pytest.mark.asyncio
async def test_storage_provider_explicit_empty_selection_returns_no_filesystems(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "homelab_console.providers.storage.psutil.disk_partitions",
        lambda all=False: [
            _partition("/dev/nvme0n1p2", "/", "ext4"),
        ],
    )

    snapshot = await LocalStorageProvider(()).collect()

    assert snapshot.error is None
    assert snapshot.filesystems == ()
