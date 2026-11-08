"""Reaping must not wait forever for an inherited pipe."""

import asyncio

import pytest

from homelab_console.transports.process import _kill_and_reap


class DelayedPipe:
    def __init__(self, release):
        self.release = release
        self.closed = False

    def close(self):
        self.closed = True
        self.release.set()


class Reader:
    def __init__(self, transport):
        self._transport = transport


class StalledProcess:
    def __init__(self):
        self.pid = None
        self.returncode = None
        self.kills = 0
        self.release = asyncio.Event()
        self.pipe = DelayedPipe(self.release)
        self.stdout = Reader(self.pipe)
        self.stderr = None

    def kill(self):
        self.kills += 1

    async def communicate(self):
        await self.release.wait()
        self.returncode = -9
        return b"", b""


@pytest.mark.asyncio
async def test_reaper_forces_pipe_close_after_deadline(monkeypatch):
    import homelab_console.transports.process as process_module

    monkeypatch.setattr(
        process_module,
        "_REAP_GRACE_SECONDS",
        0.05,
        raising=False,
    )
    monkeypatch.setattr(
        process_module,
        "_REAP_FINAL_SECONDS",
        0.2,
        raising=False,
    )

    process = StalledProcess()
    task = asyncio.create_task(_kill_and_reap(process))

    try:
        await asyncio.sleep(0.25)
        finished_before_rescue = task.done()
        pipe_closed_before_rescue = process.pipe.closed
    finally:
        # The old implementation has no deadline.
        # Unblock it so the RED test never strands a task.
        process.release.set()
        await asyncio.wait_for(task, timeout=2)

    assert finished_before_rescue, "reaper exceeded its deadline"
    assert pipe_closed_before_rescue, "stalled pipe was not closed"
    assert process.kills == 1
