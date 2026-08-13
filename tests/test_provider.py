import pytest

from homelab_console.providers import LocalHostProvider


@pytest.mark.asyncio
async def test_local_provider_returns_snapshot() -> None:
    snapshot = await LocalHostProvider().collect()
    assert snapshot.hostname
    assert snapshot.memory_total >= 0
    assert snapshot.disk_total >= 0
    assert snapshot.collected_at.tzinfo is not None
