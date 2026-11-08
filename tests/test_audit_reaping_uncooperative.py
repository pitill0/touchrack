"""Expose a pending communicate task after terminal reap timeout."""

import asyncio

import pytest

from homelab_console.transports.process import _kill_and_reap


class UncooperativeProcess:
    """Controlled fake: cleanup cancellation does not stop communicate."""

    pid = None
    stdout = None
    stderr = None
    returncode = None

    def __init__(self):
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.cancel_received = asyncio.Event()
        self.finished = asyncio.Event()
        self.communicate_task = None
        self.kill_count = 0

    def kill(self):
        self.kill_count += 1

    async def communicate(self):
        self.communicate_task = asyncio.current_task()
        self.started.set()

        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancel_received.set()
            # Simulate a cleanup operation that does not terminate
            # merely because it received cancellation.
            await self.release.wait()

        self.finished.set()
        self.returncode = -9
        return b"", b""


@pytest.mark.asyncio
async def test_terminal_timeout_does_not_abandon_communicate(monkeypatch):
    import homelab_console.transports.process as module

    monkeypatch.setattr(module, "_REAP_GRACE_SECONDS", 0.02)
    monkeypatch.setattr(module, "_REAP_FINAL_SECONDS", 0.03)

    process = UncooperativeProcess()
    caller = asyncio.create_task(_kill_and_reap(process))

    pending_at_return = False
    owned_at_return = False
    cancellation_observed = False

    try:
        with pytest.raises(
            TimeoutError,
            match="reaping exceeded cleanup deadline",
        ):
            await asyncio.wait_for(caller, timeout=2)

        assert process.communicate_task is not None

        pending_at_return = not process.communicate_task.done()
        owned_at_return = process.communicate_task in module._PENDING_REAP_TASKS
        cancellation_observed = process.cancel_received.is_set()

        print(
            "TERMINAL_RESULT",
            f"caller_done={caller.done()}",
            f"communicate_pending={pending_at_return}",
            f"cancel_observed={cancellation_observed}",
            f"kill_count={process.kill_count}",
            flush=True,
        )

    finally:
        # Always release the deliberately stubborn coroutine.
        # No pending-task leaks from this regression itself.
        process.release.set()

        if process.communicate_task is not None:
            await asyncio.wait_for(
                asyncio.shield(process.communicate_task),
                timeout=2,
            )

        # Run completion callbacks before asserting registry release.
        await asyncio.sleep(0)

        if not caller.done():
            caller.cancel()

        await asyncio.wait_for(
            asyncio.gather(caller, return_exceptions=True),
            timeout=2,
        )

    assert process.kill_count == 1
    assert cancellation_observed
    assert pending_at_return, "test did not exercise terminal ownership"
    assert owned_at_return, "reaper returned without retaining ownership of communicate"
    assert process.communicate_task.done()
    assert process.communicate_task not in module._PENDING_REAP_TASKS
