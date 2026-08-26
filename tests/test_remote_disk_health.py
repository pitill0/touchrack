from __future__ import annotations

import json

import pytest

from homelab_console.providers import RemoteSmartctlDiskHealthProvider
from homelab_console.transports import CommandResult, SshConnectionError


def smart_payload(device: str) -> str:
    return json.dumps(
        {
            "device": {"name": device, "protocol": "ATA"},
            "model_name": "Test SSD",
            "serial_number": "SER123",
            "user_capacity": {"bytes": 1000000},
            "smart_status": {"passed": True},
            "temperature": {"current": 31},
            "power_on_time": {"hours": 42},
        }
    )


@pytest.mark.asyncio
async def test_remote_smartctl_uses_fixed_helper_and_configured_devices() -> None:
    class Runner:
        def __init__(self) -> None:
            self.calls = []

        async def run(self, command, *, timeout=None):
            self.calls.append((command, timeout))
            return CommandResult(command, smart_payload(command[-1]), "", 0)

    runner = Runner()
    provider = RemoteSmartctlDiskHealthProvider(
        runner,
        devices=("/dev/sda",),
        helper="/usr/local/libexec/touchrack-smartctl",
        cache_seconds=0,
    )

    snapshot = await provider.collect()

    assert runner.calls == [
        (("sudo", "-n", "/usr/local/libexec/touchrack-smartctl", "/dev/sda"), 12.0)
    ]
    assert snapshot.available is True
    assert snapshot.error is None
    assert snapshot.disks[0].model == "Test SSD"
    assert snapshot.disks[0].smart_passed is True


@pytest.mark.asyncio
async def test_remote_smartctl_surfaces_transport_failure() -> None:
    class Runner:
        async def run(self, command, *, timeout=None):
            raise SshConnectionError("connection refused")

    snapshot = await RemoteSmartctlDiskHealthProvider(
        Runner(),
        devices=("/dev/sda",),
        helper="/usr/local/libexec/touchrack-smartctl",
        cache_seconds=0,
    ).collect()

    assert snapshot.available is False
    assert snapshot.disks == ()
    assert snapshot.error == "/dev/sda: connection refused"


@pytest.mark.asyncio
async def test_remote_smartctl_keeps_valid_json_on_smart_bitmask_exit() -> None:
    class Runner:
        async def run(self, command, *, timeout=None):
            return CommandResult(command, smart_payload("/dev/sda"), "", 8)

    snapshot = await RemoteSmartctlDiskHealthProvider(
        Runner(),
        devices=("/dev/sda",),
        helper="/usr/local/libexec/touchrack-smartctl",
        cache_seconds=0,
    ).collect()

    assert snapshot.available is True
    assert snapshot.disks[0].device == "/dev/sda"
