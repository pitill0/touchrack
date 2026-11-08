"""Cooperative shutdown regressions for host, storage and SMART."""

from __future__ import annotations

import asyncio
import threading

import pytest

from homelab_console.providers.disk_health import (
    SmartctlDiskHealthProvider,
)
from homelab_console.providers.local import LocalHostProvider
from homelab_console.providers.remote_disk_health import (
    RemoteSmartctlDiskHealthProvider,
)
from homelab_console.providers.storage import LocalStorageProvider


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,provider_class,inflight_name",
    (
        ("host", LocalHostProvider, "_collect_task"),
        ("storage", LocalStorageProvider, "_collect_task"),
        ("smart_local", SmartctlDiskHealthProvider, "_inflight"),
    ),
)
async def test_sync_collection_is_drained_without_cancelling_thread(
    kind, provider_class, inflight_name, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    exited = threading.Event()
    calls = 0

    provider = provider_class()

    def controlled_collection():
        nonlocal calls
        calls += 1
        started.set()
        try:
            if not release.wait(timeout=8):
                raise TimeoutError("Controlled thread release expired")
            return object()
        finally:
            exited.set()

    monkeypatch.setattr(provider, "_collect_sync", controlled_collection)

    caller = asyncio.create_task(provider.collect())
    shutdown = None

    try:
        assert await asyncio.wait_for(
            asyncio.to_thread(started.wait, 3),
            timeout=4,
        )

        inflight = getattr(provider, inflight_name)
        assert inflight is not None
        assert not inflight.done()
        assert calls == 1

        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        assert caller.cancelled()

        assert getattr(provider, inflight_name) is inflight
        assert not exited.is_set()

        shutdown = asyncio.create_task(provider.shutdown())

        done, pending = await asyncio.wait((shutdown,), timeout=0.15)

        assert not done and len(pending) == 1
        assert not exited.is_set()
        assert not inflight.cancelled()

        print(
            f"{kind.upper()}_BEFORE_RELEASE:",
            "caller_cancelled=",
            caller.cancelled(),
            "shutdown_pending=",
            not shutdown.done(),
            "thread_exited=",
            exited.is_set(),
            flush=True,
        )

        release.set()
        await asyncio.wait_for(shutdown, timeout=5)

        assert exited.is_set()
        assert inflight.done()
        assert not inflight.cancelled()
        assert calls == 1

        # A second shutdown must be harmless.
        await asyncio.wait_for(provider.shutdown(), timeout=5)

        print(
            f"{kind.upper()}_AFTER_DRAIN:",
            "shutdown_done=",
            shutdown.done(),
            "thread_exited=",
            exited.is_set(),
            "calls=",
            calls,
            flush=True,
        )
    finally:
        release.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        if shutdown is not None:
            await asyncio.wait_for(
                asyncio.gather(shutdown, return_exceptions=True),
                timeout=5,
            )
        await asyncio.wait_for(provider.shutdown(), timeout=5)


@pytest.mark.asyncio
async def test_remote_smart_inflight_drains_after_caller_cancel(
    monkeypatch,
):
    started = asyncio.Event()
    release = asyncio.Event()
    exited = asyncio.Event()
    calls = 0

    provider = RemoteSmartctlDiskHealthProvider(
        runner=object(),
        devices=(),
        helper="/unused/helper",
        cache_seconds=0,
    )

    async def controlled_remote_collection():
        nonlocal calls
        calls += 1
        started.set()
        try:
            await release.wait()
            return object()
        finally:
            exited.set()

    monkeypatch.setattr(
        provider,
        "_collect_remote",
        controlled_remote_collection,
    )

    caller = asyncio.create_task(provider.collect())
    shutdown = None

    try:
        await asyncio.wait_for(started.wait(), timeout=4)

        inflight = provider._inflight
        assert inflight is not None
        assert not inflight.done()

        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)

        assert caller.cancelled()
        assert provider._inflight is inflight
        assert not exited.is_set()

        shutdown = asyncio.create_task(provider.shutdown())

        done, pending = await asyncio.wait((shutdown,), timeout=0.15)

        assert not done and len(pending) == 1
        assert not inflight.cancelled()

        print(
            "SMART_REMOTE_BEFORE_RELEASE:",
            "caller_cancelled=",
            caller.cancelled(),
            "shutdown_pending=",
            not shutdown.done(),
            "collection_exited=",
            exited.is_set(),
            flush=True,
        )

        release.set()
        await asyncio.wait_for(shutdown, timeout=5)

        assert exited.is_set()
        assert inflight.done()
        assert not inflight.cancelled()
        assert calls == 1

        await asyncio.wait_for(provider.shutdown(), timeout=5)

        print(
            "SMART_REMOTE_AFTER_DRAIN:",
            "shutdown_done=",
            shutdown.done(),
            "collection_exited=",
            exited.is_set(),
            "calls=",
            calls,
            flush=True,
        )
    finally:
        release.set()
        if not caller.done():
            caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
        if shutdown is not None:
            await asyncio.wait_for(
                asyncio.gather(shutdown, return_exceptions=True),
                timeout=5,
            )
        await asyncio.wait_for(provider.shutdown(), timeout=5)
