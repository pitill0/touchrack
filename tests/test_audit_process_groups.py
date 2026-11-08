"""Subprocess descendants must not retain inherited pipes."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

from homelab_console.transports.errors import CommandTimeout
from homelab_console.transports.process import run_subprocess

PARENT_CODE = """
import subprocess
import sys
import time
from pathlib import Path

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(25)"],
    stdout=sys.stdout,
    stderr=sys.stderr,
)
Path(sys.argv[1]).write_text(str(child.pid))
time.sleep(25)
"""


async def wait_for_descendant(path: Path, task: asyncio.Task):
    for _ in range(150):
        if path.exists():
            return
        if task.done():
            break
        await asyncio.sleep(0.01)
    raise AssertionError("descendant was not created")


async def rescue(task: asyncio.Task, path: Path):
    # RED-run safety: kill only the known descendant PID, never
    # the pytest process group.
    if path.exists():
        pid = int(path.read_text())
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(task, timeout=5)
    except (CommandTimeout, asyncio.CancelledError):
        pass


@pytest.mark.asyncio
async def test_timeout_closes_descendant_pipes(tmp_path: Path):
    marker = tmp_path / "descendant.pid"

    task = asyncio.create_task(
        run_subprocess(
            (sys.executable, "-c", PARENT_CODE, str(marker)),
            timeout=0.4,
        )
    )

    try:
        await wait_for_descendant(marker, task)
        await asyncio.sleep(1.0)
        completed = task.done()
    finally:
        await rescue(task, marker)

    assert completed, "descendant retained inherited pipes after timeout"

    with pytest.raises(CommandTimeout):
        await task


@pytest.mark.asyncio
async def test_cancel_closes_descendant_pipes(tmp_path: Path):
    marker = tmp_path / "descendant.pid"

    task = asyncio.create_task(
        run_subprocess(
            (sys.executable, "-c", PARENT_CODE, str(marker)),
            timeout=15,
        )
    )

    try:
        await wait_for_descendant(marker, task)
        task.cancel()
        await asyncio.sleep(0.8)
        completed = task.done()
    finally:
        await rescue(task, marker)

    assert completed, "descendant retained inherited pipes after cancellation"

    assert task.cancelled()
