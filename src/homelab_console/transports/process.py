from __future__ import annotations

import asyncio

from homelab_console.transports.base import CommandResult
from homelab_console.transports.errors import CommandTimeout, TransportUnavailable


async def run_subprocess(
    argv: tuple[str, ...],
    *,
    timeout: float,
    reported_command: tuple[str, ...] | None = None,
) -> CommandResult:
    """Run and reap one subprocess, including timeout and cancellation paths."""
    if not argv:
        raise ValueError("argv must contain at least one element")
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise TransportUnavailable(f"executable not found: {argv[0]}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except asyncio.TimeoutError as exc:
        await _kill_and_reap(process)
        raise CommandTimeout(reported_command or argv, timeout) from exc
    except asyncio.CancelledError:
        await _kill_and_reap(process)
        raise

    return CommandResult(
        command=reported_command or argv,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace"),
        returncode=int(process.returncode or 0),
    )


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    try:
        process.kill()
    except ProcessLookupError:
        pass

    # A second cancellation must not abandon subprocess reaping.
    # Shielding alone is insufficient: the caller still receives
    # CancelledError and must continue waiting for cleanup.
    reap_task = asyncio.create_task(process.communicate())
    cancelled_during_cleanup = False

    while not reap_task.done():
        try:
            await asyncio.shield(reap_task)
        except asyncio.CancelledError:
            cancelled_during_cleanup = True

    # Surface any cleanup failure rather than silently discarding it.
    await reap_task

    if cancelled_during_cleanup:
        raise asyncio.CancelledError
