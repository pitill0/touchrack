"""Cancellation during spawn must not abandon an acquired subprocess."""

import asyncio

import pytest

from homelab_console.providers.containers import PodmanContainersProvider
from homelab_console.transports.process import run_subprocess


class FakeProcess:
    def __init__(self):
        self.returncode = None
        self.kills = 0
        self.communications = 0

    def kill(self):
        self.kills += 1
        self.returncode = -9

    async def communicate(self):
        self.communications += 1
        return b"[]", b""


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["containers", "transport"])
async def test_cancellation_during_spawn_reaps_acquired_process(monkeypatch, kind):
    started = asyncio.Event()
    release = asyncio.Event()
    process = FakeProcess()

    async def controlled_spawn(*args, **kwargs):
        started.set()

        # Simulate a subprocess creation that finishes despite
        # receiving cancellation. The caller must still own and
        # reap the process returned by the spawn operation.
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()

        return process

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        controlled_spawn,
    )

    if kind == "containers":
        operation = PodmanContainersProvider(timeout_seconds=5)._run("ps")
    else:
        operation = run_subprocess(
            ("fake-command",),
            timeout=5,
        )

    caller = asyncio.create_task(operation)

    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        caller.cancel()
        release.set()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(caller, timeout=3)
    finally:
        release.set()
        if not caller.done():
            caller.cancel()
            await asyncio.wait_for(
                asyncio.gather(caller, return_exceptions=True),
                timeout=3,
            )

    assert process.kills == 1
    assert process.communications >= 1
    assert process.returncode == -9
