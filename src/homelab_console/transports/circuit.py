from __future__ import annotations

import asyncio
from time import monotonic
from typing import Callable

from homelab_console.transports.base import CommandResult, CommandRunner
from homelab_console.transports.errors import TransportError


class CircuitOpenError(TransportError):
    """A recent transport failure is suppressing redundant attempts."""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0.0, retry_after)
        super().__init__(
            f"transport temporarily unavailable; retry in {self.retry_after:.1f}s"
        )


class CircuitBreakerCommandRunner:
    """Decorate a runner with shared failure backoff and one recovery probe."""

    def __init__(
        self,
        runner: CommandRunner,
        *,
        cooldown_seconds: float = 5.0,
        failure_types: tuple[type[BaseException], ...] = (TransportError,),
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be greater than zero")
        if not failure_types:
            raise ValueError("failure_types must not be empty")
        self.runner = runner
        self.cooldown_seconds = float(cooldown_seconds)
        self.failure_types = failure_types
        self.clock = clock
        self._open_until: float | None = None
        self._failure_generation = 0
        self._recovery_generation = 0
        self._probe_lock = asyncio.Lock()

    async def run(
        self,
        command: tuple[str, ...],
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        open_until = self._open_until
        now = self.clock()
        if open_until is not None and now < open_until:
            raise CircuitOpenError(open_until - now)

        if open_until is not None:
            if self._probe_lock.locked():
                raise CircuitOpenError(0.0)
            async with self._probe_lock:
                # Another recovery probe may have completed while this caller
                # was scheduled. A closed circuit can proceed normally.
                if self._open_until is None:
                    return await self.runner.run(command, timeout=timeout)
                return await self._run_and_record(command, timeout=timeout)

        return await self._run_and_record(command, timeout=timeout)

    async def _run_and_record(
        self,
        command: tuple[str, ...],
        *,
        timeout: float | None,
    ) -> CommandResult:
        failure_generation = self._failure_generation
        recovery_generation = self._recovery_generation
        try:
            result = await self.runner.run(command, timeout=timeout)
        except self.failure_types:
            # Failures from before a completed recovery must still
            # reach the caller, but cannot reopen the recovered circuit.
            if recovery_generation == self._recovery_generation:
                self._failure_generation += 1
                self._open_until = self.clock() + self.cooldown_seconds
            raise

        # A success preceding a newer failure is not proof of recovery.
        if failure_generation == self._failure_generation:
            if self._open_until is not None:
                self._recovery_generation += 1
            self._open_until = None
        return result
