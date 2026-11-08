from __future__ import annotations

import asyncio
import logging
import os
import signal

from homelab_console.transports.base import CommandResult
from homelab_console.transports.errors import CommandTimeout, TransportUnavailable


async def _spawn_owned(
    *argv: str,
    **kwargs: object,
) -> asyncio.subprocess.Process:
    """Finish subprocess creation even if the caller is cancelled.

    If cancellation arrives before asyncio returns the Process,
    acquire ownership and reap it before propagating cancellation.
    """
    spawn_task = asyncio.create_task(asyncio.create_subprocess_exec(*argv, **kwargs))
    cancelled = False

    while not spawn_task.done():
        try:
            await asyncio.shield(spawn_task)
        except asyncio.CancelledError:
            cancelled = True

    try:
        process = spawn_task.result()
    except BaseException:
        if cancelled:
            raise asyncio.CancelledError
        raise

    if cancelled:
        await _kill_and_reap(process)
        raise asyncio.CancelledError

    return process


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
        process = await _spawn_owned(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
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


_PENDING_REAP_TASKS: set[asyncio.Task] = set()


def _observe_terminal_reap(task: asyncio.Task) -> None:
    _PENDING_REAP_TASKS.discard(task)

    try:
        failure = task.exception()
    except asyncio.CancelledError:
        return

    if failure is not None:
        logging.getLogger(__name__).error(
            "Detached subprocess cleanup failed",
            exc_info=(type(failure), failure, failure.__traceback__),
        )


def _retain_terminal_reap(task: asyncio.Task) -> None:
    _PENDING_REAP_TASKS.add(task)
    task.add_done_callback(_observe_terminal_reap)


_REAP_GRACE_SECONDS = 3.0
_REAP_FINAL_SECONDS = 1.0


async def _kill_and_reap(process: asyncio.subprocess.Process) -> None:
    pid = getattr(process, "pid", None)
    isolated = (
        isinstance(process, asyncio.subprocess.Process)
        and isinstance(pid, int)
        and pid > 0
        and pid != os.getpgrp()
    )

    try:
        if isolated:
            os.killpg(pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass

    reap_task = asyncio.create_task(process.communicate())
    cancelled_during_cleanup = False

    async def wait_bounded(seconds: float) -> bool:
        nonlocal cancelled_during_cleanup

        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds

        while not reap_task.done():
            remaining = deadline - loop.time()
            if remaining <= 0:
                return False

            try:
                await asyncio.wait_for(
                    asyncio.shield(reap_task),
                    timeout=remaining,
                )
            except TimeoutError:
                return reap_task.done()
            except asyncio.CancelledError:
                cancelled_during_cleanup = True

        return True

    completed = await wait_bounded(_REAP_GRACE_SECONDS)

    if not completed:
        # Emergency closure of inherited pipe transports.
        # Used only when normal subprocess reaping stalls.
        for stream in (
            getattr(process, "stdout", None),
            getattr(process, "stderr", None),
        ):
            transport = getattr(stream, "_transport", None)
            if transport is not None:
                transport.close()

        completed = await wait_bounded(_REAP_FINAL_SECONDS)

    if not completed:
        reap_task.cancel()

        # The final grace period is also bounded. An uncooperative
        # communicate() cannot be forcibly stopped by asyncio.
        # Retain ownership if it remains pending.
        completed = await wait_bounded(_REAP_FINAL_SECONDS)

        if not completed:
            _retain_terminal_reap(reap_task)
        else:
            # Observe a completed cancellation or cleanup failure.
            try:
                await reap_task
            except asyncio.CancelledError:
                pass

        raise TimeoutError("subprocess reaping exceeded cleanup deadline")

    await reap_task

    if cancelled_during_cleanup:
        raise asyncio.CancelledError
