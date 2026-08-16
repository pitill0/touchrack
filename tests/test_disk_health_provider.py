from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from homelab_console.providers.disk_health import SmartctlDiskHealthProvider


def _result(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(stdout=json.dumps(payload), stderr="", returncode=0)


@pytest.mark.asyncio
async def test_smartctl_provider_collects_nvme_health(monkeypatch) -> None:
    provider = SmartctlDiskHealthProvider(cache_seconds=0)

    scan = {
        "devices": [
            {"name": "/dev/nvme0", "type": "nvme"},
        ]
    }
    device = {
        "device": {"name": "/dev/nvme0", "protocol": "NVMe"},
        "model_name": "Example NVMe",
        "serial_number": "ABC123",
        "user_capacity": {"bytes": 1_000_000_000_000},
        "smart_status": {"passed": True},
        "temperature": {"current": 41},
        "power_on_time": {"hours": 8214},
        "power_cycle_count": 120,
        "nvme_smart_health_information_log": {
            "percentage_used": 7,
            "unsafe_shutdowns": 3,
            "media_errors": 0,
        },
    }

    calls: list[tuple[str, ...]] = []

    def fake_run(*args: str):
        calls.append(args)
        return _result(scan if "--scan-open" in args else device)

    monkeypatch.setattr(provider, "_run", fake_run)

    snapshot = await provider.collect()

    assert snapshot.available
    assert snapshot.error is None
    assert snapshot.total == 1

    disk = snapshot.disks[0]
    assert disk.device == "/dev/nvme0"
    assert disk.name == "nvme0"
    assert disk.model == "Example NVMe"
    assert disk.serial == "ABC123"
    assert disk.protocol == "NVMe"
    assert disk.size == 1_000_000_000_000
    assert disk.temperature_c == 41
    assert disk.smart_passed is True
    assert disk.power_on_hours == 8214
    assert disk.percentage_used == 7
    assert dict(disk.details)["WEAR USED"] == "7%"
    assert calls[0] == ("--scan-open", "-j")


@pytest.mark.asyncio
async def test_smartctl_provider_is_optional_when_binary_is_missing(monkeypatch) -> None:
    provider = SmartctlDiskHealthProvider(cache_seconds=0)

    def missing(*args: str):
        raise FileNotFoundError("smartctl")

    monkeypatch.setattr(provider, "_run", missing)

    snapshot = await provider.collect()

    assert snapshot.disks == ()
    assert snapshot.available is False
    assert snapshot.error == "smartctl not installed"


@pytest.mark.asyncio
async def test_smartctl_provider_accepts_useful_json_from_nonzero_exit(monkeypatch) -> None:
    provider = SmartctlDiskHealthProvider(cache_seconds=0)
    scan = {"devices": [{"name": "/dev/sda", "type": "sat"}]}
    failed = {
        "device": {"name": "/dev/sda", "protocol": "ATA"},
        "model_name": "Example HDD",
        "smart_status": {"passed": False},
        "temperature": {"current": 38},
    }

    monkeypatch.setattr(
        provider,
        "_run",
        lambda *args: _result(scan if "--scan-open" in args else failed),
    )

    snapshot = await provider.collect()

    assert snapshot.total == 1
    assert snapshot.disks[0].smart_passed is False



@pytest.mark.asyncio
async def test_smartctl_provider_filters_and_preserves_configured_device_order(
    monkeypatch,
) -> None:
    provider = SmartctlDiskHealthProvider(
        cache_seconds=0,
        devices=("/dev/sda", "/dev/nvme0"),
    )
    scan = {
        "devices": [
            {"name": "/dev/nvme0", "type": "nvme"},
            {"name": "/dev/sda", "type": "sat"},
            {"name": "/dev/sdb", "type": "sat"},
        ]
    }

    payloads = {
        "/dev/sda": {
            "device": {"name": "/dev/sda", "protocol": "ATA"},
            "model_name": "Disk A",
            "smart_status": {"passed": True},
        },
        "/dev/nvme0": {
            "device": {"name": "/dev/nvme0", "protocol": "NVMe"},
            "model_name": "Disk N",
            "smart_status": {"passed": True},
        },
    }
    probed: list[str] = []

    def fake_run(*args: str):
        if "--scan-open" in args:
            return _result(scan)
        name = next(arg for arg in args if arg.startswith("/dev/"))
        probed.append(name)
        return _result(payloads[name])

    monkeypatch.setattr(provider, "_run", fake_run)

    snapshot = await provider.collect()

    assert [disk.device for disk in snapshot.disks] == ["/dev/sda", "/dev/nvme0"]
    assert probed == ["/dev/sda", "/dev/nvme0"]


@pytest.mark.asyncio
async def test_smartctl_provider_probes_configured_device_missing_from_scan(
    monkeypatch,
) -> None:
    provider = SmartctlDiskHealthProvider(
        cache_seconds=0,
        devices=("/dev/sdc",),
    )
    scan = {"devices": [{"name": "/dev/sda", "type": "sat"}]}
    direct = {
        "device": {"name": "/dev/sdc", "protocol": "ATA"},
        "model_name": "Direct Disk",
        "smart_status": {"passed": True},
    }

    monkeypatch.setattr(
        provider,
        "_run",
        lambda *args: _result(scan if "--scan-open" in args else direct),
    )

    snapshot = await provider.collect()

    assert [disk.device for disk in snapshot.disks] == ["/dev/sdc"]


@pytest.mark.asyncio
async def test_smartctl_provider_explicit_empty_selection_returns_no_disks(
    monkeypatch,
) -> None:
    provider = SmartctlDiskHealthProvider(cache_seconds=0, devices=())
    scan = {"devices": [{"name": "/dev/sda", "type": "sat"}]}

    monkeypatch.setattr(provider, "_run", lambda *args: _result(scan))

    snapshot = await provider.collect()

    assert snapshot.available is True
    assert snapshot.disks == ()
