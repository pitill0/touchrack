"""Double cancellation must not abandon subprocess cleanup."""

from __future__ import annotations

import asyncio

import pytest

from homelab_console.providers.containers import CliContainersProvider
from homelab_console.services import (
    ServiceDefinition,
    SystemdServiceProvider,
)


class ControlledProcess:
    """Fake asyncio subprocess with independently gated reaping."""

    def __init__(self) -> None:
        self.first_started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.cleanup_finished = asyncio.Event()
        self.communicate_calls = 0
        self.kill_calls = 0
        self.returncode = None

    def kill(self) -> None:
        self.kill_calls += 1

    async def communicate(self):
        self.communicate_calls += 1

        if self.communicate_calls == 1:
            self.first_started.set()
            # The initial communicate() must be cancelled by the caller.
            await asyncio.Event().wait()
            raise AssertionError("Initial communicate unexpectedly ended")

        if self.communicate_calls == 2:
            self.cleanup_started.set()
            await self.release_cleanup.wait()
            self.returncode = -9
            self.cleanup_finished.set()
            return b"", b""

        raise AssertionError(f"Unexpected communicate call: {self.communicate_calls}")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ("systemd", "containers"))
async def test_second_cancel_waits_for_subprocess_reaping(kind, monkeypatch):
    process = ControlledProcess()
    spawn_calls = []

    async def fake_spawn(*args, **kwargs):
        spawn_calls.append((args, kwargs))
        return process

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_spawn,
    )

    if kind == "systemd":
        provider = SystemdServiceProvider(timeout_seconds=5)
        definition = ServiceDefinition(
            id="controlled",
            title="Controlled systemd",
            provider="systemd",
            target="controlled.service",
        )
        caller = asyncio.create_task(provider.check(definition))
    else:
        provider = CliContainersProvider(
            command="controlled-container-cli",
            timeout_seconds=5,
        )
        caller = asyncio.create_task(provider._run("ps"))

    try:
        await asyncio.wait_for(process.first_started.wait(), timeout=3)

        assert len(spawn_calls) == 1
        assert process.communicate_calls == 1

        # First cancellation interrupts the active operation and
        # initiates kill/reap through the shared transport helper.
        caller.cancel()

        await asyncio.wait_for(process.cleanup_started.wait(), timeout=3)

        assert process.kill_calls == 1
        assert process.communicate_calls == 2
        assert not process.cleanup_finished.is_set()

        # A second cancellation must NOT cancel the reaping task.
        caller.cancel()

        done, pending = await asyncio.wait((caller,), timeout=0.15)

        print(
            f"{kind.upper()}_DURING_CLEANUP:",
            "caller_pending=",
            len(pending),
            "kill_calls=",
            process.kill_calls,
            "cleanup_finished=",
            process.cleanup_finished.is_set(),
            flush=True,
        )

        assert not done
        assert len(pending) == 1
        assert not process.cleanup_finished.is_set()

        process.release_cleanup.set()

        result = await asyncio.wait_for(
            asyncio.gather(caller, return_exceptions=True),
            timeout=4,
        )

        assert isinstance(result[0], asyncio.CancelledError)
        assert process.cleanup_finished.is_set()
        assert process.communicate_calls == 2
        assert process.kill_calls == 1
        assert caller.done()

        print(
            f"{kind.upper()}_AFTER_REAP:",
            "caller_cancelled=",
            caller.cancelled(),
            "cleanup_finished=",
            process.cleanup_finished.is_set(),
            "communicate_calls=",
            process.communicate_calls,
            "kill_calls=",
            process.kill_calls,
            flush=True,
        )
    finally:
        process.release_cleanup.set()
        if not caller.done():
            caller.cancel()
        await asyncio.wait_for(
            asyncio.gather(caller, return_exceptions=True),
            timeout=5,
        )
