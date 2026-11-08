"""Application shutdown must drain running container CLI operations."""

import asyncio

import pytest

from homelab_console.app import HomelabConsole
from homelab_console.providers.containers import (
    AutoContainersProvider,
    CliContainersProvider,
)


class ControlledProcess:
    pid = None
    stdout = None
    stderr = None
    returncode = None

    def __init__(self):
        self.started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.finished = asyncio.Event()
        self.calls = 0
        self.kills = 0

    def kill(self):
        self.kills += 1

    async def communicate(self):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("original communicate did not cancel")

        if self.calls == 2:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            self.returncode = -9
            self.finished.set()
            return b"", b""

        raise AssertionError("unexpected communicate invocation")


@pytest.mark.asyncio
async def test_unmount_drains_active_containers_command(monkeypatch):
    process = ControlledProcess()

    async def fake_spawn(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_spawn)

    provider = CliContainersProvider(
        command="controlled-containers",
        timeout_seconds=30,
    )

    app = HomelabConsole(
        containers_provider=provider,
        touch_enabled=False,
        screen_blank_minutes=0,
    )

    operation = asyncio.create_task(provider._run("ps"))
    closing = None

    try:
        await asyncio.wait_for(process.started.wait(), timeout=3)
        closing = asyncio.create_task(app.on_unmount())

        # The application must not report shutdown complete
        # while Containers still owns an unfinished subprocess.
        await asyncio.sleep(0.10)
        assert not closing.done(), (
            "on_unmount completed before Containers subprocess cleanup"
        )

        await asyncio.wait_for(process.cleanup_started.wait(), timeout=3)

        assert process.kills == 1
        assert not process.finished.is_set()

        process.cleanup_release.set()

        await asyncio.wait_for(closing, timeout=4)
        result = await asyncio.wait_for(
            asyncio.gather(operation, return_exceptions=True),
            timeout=4,
        )

        assert isinstance(result[0], asyncio.CancelledError)
        assert process.finished.is_set()
        assert not provider._active_runs

    finally:
        process.cleanup_release.set()

        if not operation.done():
            operation.cancel()

        await asyncio.wait_for(
            asyncio.gather(operation, return_exceptions=True),
            timeout=5,
        )

        if closing is not None:
            await asyncio.wait_for(
                asyncio.gather(closing, return_exceptions=True),
                timeout=5,
            )


@pytest.mark.asyncio
async def test_auto_provider_delegates_shutdown():
    class Stub:
        calls = 0

        async def shutdown(self):
            self.calls += 1

    provider = AutoContainersProvider()
    stub = Stub()
    provider.provider = stub

    await provider.shutdown()

    assert stub.calls == 1
