from __future__ import annotations

import multiprocessing
import os
from pathlib import Path

import pytest

from homelab_console.single_instance import AlreadyRunningError, SingleInstanceLock


def _hold_lock(path: str, ready, release) -> None:
    with SingleInstanceLock(path):
        ready.set()
        release.wait(5)


def test_lock_can_be_reacquired_after_release(tmp_path: Path) -> None:
    path = tmp_path / "console.lock"
    with SingleInstanceLock(path):
        assert path.read_text().strip() == str(os.getpid())

    with SingleInstanceLock(path):
        assert path.read_text().strip() == str(os.getpid())


def test_second_process_is_rejected_with_owner_pid(tmp_path: Path) -> None:
    path = tmp_path / "console.lock"
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    process = ctx.Process(target=_hold_lock, args=(str(path), ready, release))
    process.start()
    try:
        assert ready.wait(3)
        with pytest.raises(AlreadyRunningError) as caught:
            SingleInstanceLock(path).acquire()
        assert caught.value.owner_pid == process.pid
    finally:
        release.set()
        process.join(3)
        if process.is_alive():
            process.terminate()
            process.join(3)
