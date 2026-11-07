from __future__ import annotations

import asyncio

import pytest

from homelab_console.transports import process as process_module


@pytest.mark.asyncio
async def test_repeated_cancellation_still_completes_process_reaping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second cancellation must not abandon child reaping."""

    command_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_reap = asyncio.Event()
    reaped = asyncio.Event()

    class FakeProcess:
        returncode: int | None = None

        def __init__(self) -> None:
            self.killed = False
            self.communicate_calls = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            self.communicate_calls += 1

            if not self.killed:
                command_started.set()
                await asyncio.Event().wait()
                return b"", b""

            cleanup_started.set()
            await allow_reap.wait()
            self.returncode = -9
            reaped.set()
            return b"", b""

        def kill(self) -> None:
            self.killed = True

    process = FakeProcess()

    async def fake_spawn(*args: object, **kwargs: object) -> FakeProcess:
        return process

    monkeypatch.setattr(
        asyncio, "create_subprocess_exec", fake_spawn
    )

    task = asyncio.create_task(
        process_module.run_subprocess(("simulated-child",), timeout=10)
    )

    try:
        await asyncio.wait_for(command_started.wait(), 1)
        task.cancel()

        await asyncio.wait_for(cleanup_started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        allow_reap.set()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)

        await asyncio.wait_for(reaped.wait(), 0.5)

        assert process.killed
        assert process.returncode == -9
        assert process.communicate_calls >= 2
    finally:
        allow_reap.set()
        if not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, 1)
            except (asyncio.CancelledError, TimeoutError):
                pass
