import asyncio
import threading

import pytest

from homelab_console.providers import LocalHostProvider


@pytest.mark.asyncio
async def test_local_provider_returns_snapshot() -> None:
    snapshot = await LocalHostProvider().collect()
    assert snapshot.hostname
    assert snapshot.memory_total >= 0
    assert snapshot.disk_total >= 0
    assert snapshot.collected_at.tzinfo is not None


@pytest.mark.asyncio
async def test_local_provider_reuses_inflight_collection_after_caller_cancel(
    monkeypatch,
) -> None:
    provider = LocalHostProvider()
    started = threading.Event()
    release = threading.Event()
    calls = 0

    original_collect_sync = provider._collect_sync

    def slow_collect_sync():
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=2.0)
        return original_collect_sync()

    monkeypatch.setattr(provider, "_collect_sync", slow_collect_sync)

    first = asyncio.create_task(provider.collect())
    assert await asyncio.to_thread(started.wait, 1.0)

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(provider.collect())
    await asyncio.sleep(0.05)
    assert calls == 1

    release.set()
    snapshot = await second

    assert snapshot.hostname
    assert calls == 1
