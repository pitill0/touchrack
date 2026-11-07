from __future__ import annotations

import asyncio

import pytest

from homelab_console.transports import (
    CircuitBreakerCommandRunner,
    CircuitOpenError,
    CommandResult,
    SshConnectionError,
    SshTransportError,
)


@pytest.mark.asyncio
async def test_late_success_cannot_clear_newer_transport_failure() -> None:
    """An older success must not invalidate a newer failure cooldown."""

    clock = [100.0]
    slow_started = asyncio.Event()
    failure_started = asyncio.Event()
    release_slow = asyncio.Event()
    release_failure = asyncio.Event()

    class Runner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def run(
            self,
            command: tuple[str, ...],
            *,
            timeout: float | None = None,
        ) -> CommandResult:
            name = command[0]
            self.calls.append(name)

            if name == "slow-success":
                slow_started.set()
                await release_slow.wait()
                return CommandResult(command, "ok", "", 0)

            if name == "fast-failure":
                failure_started.set()
                await release_failure.wait()
                raise SshConnectionError("simulated connection failure")

            return CommandResult(command, "unexpected", "", 0)

    inner = Runner()
    circuit = CircuitBreakerCommandRunner(
        inner,
        cooldown_seconds=5.0,
        failure_types=(SshTransportError,),
        clock=lambda: clock[0],
    )

    slow = asyncio.create_task(circuit.run(("slow-success",)))
    failing = asyncio.create_task(circuit.run(("fast-failure",)))

    try:
        await asyncio.wait_for(
            asyncio.gather(slow_started.wait(), failure_started.wait()),
            2,
        )

        release_failure.set()
        with pytest.raises(SshConnectionError):
            await asyncio.wait_for(failing, 2)

        with pytest.raises(CircuitOpenError) as opened:
            await circuit.run(("during-cooldown",))
        assert opened.value.retry_after == 5.0

        release_slow.set()
        await asyncio.wait_for(slow, 2)

        # The older successful request started before the transport failure.
        # It is not a recovery probe and must not reset the cooldown.
        with pytest.raises(CircuitOpenError) as still_open:
            await circuit.run(("after-late-success",))

        assert still_open.value.retry_after == 5.0
        assert inner.calls == ["slow-success", "fast-failure"]

    finally:
        release_slow.set()
        release_failure.set()
        for task in (slow, failing):
            if not task.done():
                task.cancel()
        await asyncio.gather(slow, failing, return_exceptions=True)


@pytest.mark.asyncio
async def test_late_failure_cannot_reopen_recovered_circuit() -> None:
    """An old failure must not undo a successful recovery probe."""

    clock = [100.0]
    old_started = asyncio.Event()
    release_old = asyncio.Event()

    class Runner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def run(
            self,
            command: tuple[str, ...],
            *,
            timeout: float | None = None,
        ) -> CommandResult:
            name = command[0]
            self.calls.append(name)

            if name == "old-failure":
                old_started.set()
                await release_old.wait()
                raise SshConnectionError("late failure")

            if name == "initial-failure":
                raise SshConnectionError("initial failure")

            return CommandResult(command, "ok\n", "", 0)

    inner = Runner()
    circuit = CircuitBreakerCommandRunner(
        inner,
        cooldown_seconds=5.0,
        failure_types=(SshTransportError,),
        clock=lambda: clock[0],
    )

    old = asyncio.create_task(circuit.run(("old-failure",)))

    try:
        await asyncio.wait_for(old_started.wait(), 2)

        with pytest.raises(SshConnectionError):
            await circuit.run(("initial-failure",))

        clock[0] += 5

        recovered = await circuit.run(("recovery-probe",))
        assert recovered.returncode == 0

        normal = await circuit.run(("after-recovery",))
        assert normal.returncode == 0

        release_old.set()
        with pytest.raises(SshConnectionError):
            await asyncio.wait_for(old, 2)

        # The late failure belongs to the previous circuit state.
        # It must still reach its caller, without reopening the circuit.
        result = await circuit.run(("after-late-failure",))
        assert result.returncode == 0

        assert inner.calls == [
            "old-failure",
            "initial-failure",
            "recovery-probe",
            "after-recovery",
            "after-late-failure",
        ]
    finally:
        release_old.set()
        if not old.done():
            old.cancel()
        await asyncio.gather(old, return_exceptions=True)


@pytest.mark.asyncio
async def test_concurrent_failures_extend_cooldown() -> None:
    """Without a recovery, the later transport failure extends cooldown."""

    clock = [100.0]
    started = [asyncio.Event(), asyncio.Event()]
    release = [asyncio.Event(), asyncio.Event()]

    class Runner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def run(
            self,
            command: tuple[str, ...],
            *,
            timeout: float | None = None,
        ) -> CommandResult:
            name = command[0]
            self.calls.append(name)
            index = {"failure-a": 0, "failure-b": 1}[name]
            started[index].set()
            await release[index].wait()
            raise SshConnectionError(f"failure {index}")

    inner = Runner()
    circuit = CircuitBreakerCommandRunner(
        inner,
        cooldown_seconds=5.0,
        failure_types=(SshTransportError,),
        clock=lambda: clock[0],
    )

    tasks = [
        asyncio.create_task(circuit.run(("failure-a",))),
        asyncio.create_task(circuit.run(("failure-b",))),
    ]

    try:
        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in started)),
            2,
        )

        release[0].set()
        with pytest.raises(SshConnectionError):
            await asyncio.wait_for(tasks[0], 2)

        clock[0] = 102.0
        release[1].set()
        with pytest.raises(SshConnectionError):
            await asyncio.wait_for(tasks[1], 2)

        with pytest.raises(CircuitOpenError) as opened:
            await circuit.run(("blocked",))

        assert opened.value.retry_after == 5.0
        assert inner.calls == ["failure-a", "failure-b"]
    finally:
        for event in release:
            event.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_recovery_probe_preserves_open_circuit() -> None:
    """A cancelled probe must release its lock without closing the circuit."""

    clock = [100.0]
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    class Runner:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def run(
            self,
            command: tuple[str, ...],
            *,
            timeout: float | None = None,
        ) -> CommandResult:
            name = command[0]
            self.calls.append(name)

            if name == "initial-failure":
                raise SshConnectionError("offline")

            if name == "cancelled-probe":
                probe_started.set()
                await release_probe.wait()

            return CommandResult(command, "ok\n", "", 0)

    inner = Runner()
    circuit = CircuitBreakerCommandRunner(
        inner,
        cooldown_seconds=5.0,
        failure_types=(SshTransportError,),
        clock=lambda: clock[0],
    )

    with pytest.raises(SshConnectionError):
        await circuit.run(("initial-failure",))

    clock[0] = 105.0
    probe = asyncio.create_task(circuit.run(("cancelled-probe",)))

    try:
        await asyncio.wait_for(probe_started.wait(), 2)

        with pytest.raises(CircuitOpenError):
            await circuit.run(("parallel-probe",))

        probe.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(probe, 2)

        assert not circuit._probe_lock.locked()
        assert circuit._open_until is not None

        recovered = await asyncio.wait_for(
            circuit.run(("retry-probe",)),
            2,
        )
        assert recovered.returncode == 0
        assert circuit._open_until is None
        assert inner.calls == [
            "initial-failure",
            "cancelled-probe",
            "retry-probe",
        ]
    finally:
        release_probe.set()
        if not probe.done():
            probe.cancel()
        await asyncio.gather(probe, return_exceptions=True)
