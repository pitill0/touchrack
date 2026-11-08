"""Terminal reaping must observe cancellation before returning."""

import asyncio

import pytest

from homelab_console.transports.process import _kill_and_reap


class StubbornProcess:
    pid = None
    stdout = None
    stderr = None
    returncode = None

    def __init__(self):
        self.finished = asyncio.Event()
        self.kills = 0

    def kill(self):
        self.kills += 1

    async def communicate(self):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Cleanup is cooperative, but not instantaneous.
            await asyncio.sleep(0.08)
            self.finished.set()
            raise


@pytest.mark.asyncio
async def test_terminal_timeout_drains_cancelled_communicate(monkeypatch):
    import homelab_console.transports.process as module

    monkeypatch.setattr(module, "_REAP_GRACE_SECONDS", 0.02)
    monkeypatch.setattr(module, "_REAP_FINAL_SECONDS", 0.2)

    process = StubbornProcess()
    task = asyncio.create_task(_kill_and_reap(process))

    try:
        with pytest.raises(TimeoutError, match="reaping exceeded"):
            await asyncio.wait_for(task, timeout=2)

        assert process.finished.is_set(), (
            "reaper returned while communicate was still active"
        )
        assert process.kills == 1
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
