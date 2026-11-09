from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from homelab_console.providers.disk_health import SmartctlDiskHealthProvider
from homelab_console.providers.remote_disk_health import (
    RemoteSmartctlDiskHealthProvider,
)
from homelab_console.screens.storage import disk_health_label
from homelab_console.transports import CommandResult

ROOT = Path(__file__).resolve().parents[1]


def standby_json(device: str) -> dict:
    return {
        "device": {"name": device, "type": "sat"},
        "smartctl": {
            "exit_status": 3,
            "messages": [
                {"string": "Device is in STANDBY mode, exit(3)"}
            ],
        },
    }


def fake_result(payload: dict, status: int = 0):
    return SimpleNamespace(
        stdout=json.dumps(payload),
        stderr="",
        returncode=status,
    )


@pytest.mark.asyncio
async def test_local_standby_is_visible_without_health_probe(
    monkeypatch,
) -> None:
    provider = SmartctlDiskHealthProvider(
        devices=("/dev/sda",), cache_seconds=0
    )
    calls = []

    def fake_run(*args):
        calls.append(args)
        if "--scan-open" in args or "--scan" in args:
            return fake_result({
                "devices": [
                    {"name": "/dev/sda", "type": "sat"}
                ]
            })
        return fake_result(standby_json("/dev/sda"), 3)

    monkeypatch.setattr(provider, "_run", fake_run)

    snapshot = await provider.collect()

    assert snapshot.available is True
    assert snapshot.error is None
    assert len(snapshot.disks) == 1

    disk = snapshot.disks[0]
    assert disk.device == "/dev/sda"
    assert disk.smart_passed is None
    assert disk.temperature_c is None
    assert disk_health_label(disk)[0] == "STANDBY"

    device_calls = [
        args for args in calls if "/dev/sda" in args
    ]
    assert len(device_calls) == 1
    assert "-n" in device_calls[0]
    assert "standby,3" in device_calls[0]


@pytest.mark.asyncio
async def test_remote_standby_is_visible_not_error() -> None:
    class FakeRunner:
        async def run(self, command, *, timeout=None):
            return CommandResult(
                command,
                json.dumps(standby_json("/dev/sdb")),
                "",
                3,
            )

    provider = RemoteSmartctlDiskHealthProvider(
        FakeRunner(),
        devices=("/dev/sdb",),
        helper="/usr/local/libexec/touchrack-smartctl",
        cache_seconds=0,
    )

    snapshot = await provider.collect()

    assert snapshot.available is True
    assert snapshot.error is None
    assert len(snapshot.disks) == 1
    assert disk_health_label(snapshot.disks[0])[0] == "STANDBY"


@pytest.mark.asyncio
async def test_exit_status_without_standby_message_is_not_sleep(
    monkeypatch,
) -> None:
    provider = SmartctlDiskHealthProvider(
        devices=("/dev/sdc",), cache_seconds=0
    )

    def fake_run(*args):
        if "--scan-open" in args or "--scan" in args:
            return fake_result({"devices": []})
        return fake_result({
            "smartctl": {
                "exit_status": 3,
                "messages": [
                    {"string": "Device open failed"}
                ],
            }
        }, 3)

    monkeypatch.setattr(provider, "_run", fake_run)

    snapshot = await provider.collect()

    assert not any(
        disk_health_label(disk)[0] == "STANDBY"
        for disk in snapshot.disks
    )


def test_privileged_helper_uses_standby_guard() -> None:
    script = (
        ROOT / "scripts/touchrack-smartctl"
    ).read_text()

    assert '"$SMARTCTL" -a -n standby,3 -j "$device"' in script


def test_standby_is_an_explicit_model_state() -> None:
    from homelab_console.models import DiskHealthInfo

    disk = DiskHealthInfo(
        device="/dev/sda",
        name="sda",
        model=None,
        serial=None,
        protocol=None,
        size=None,
        temperature_c=None,
        smart_passed=None,
        standby=True,
    )

    assert disk.standby is True
    assert disk_health_label(disk)[0] == "STANDBY"
