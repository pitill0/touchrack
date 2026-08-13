from __future__ import annotations

from datetime import datetime, timezone

import pytest

from homelab_console.models import ContainerInfo, ContainersSnapshot
from homelab_console.services import (
    ContainerSnapshotServiceProvider,
    ServiceDefinition,
    ServiceState,
    ServicesManager,
    _select_visible,
)


@pytest.mark.asyncio
async def test_container_service_uses_existing_snapshot() -> None:
    snapshot = ContainersSnapshot(
        containers=(
            ContainerInfo(
                container_id="abc",
                name="svc-grafana",
                image="grafana/grafana",
                state="running",
                status="Up 2 hours",
                cpu_percent="1.20%",
                memory_percent="4.40%",
            ),
        ),
        collected_at=datetime.now(timezone.utc),
        engine="Podman",
    )
    provider = ContainerSnapshotServiceProvider(lambda: snapshot)
    definition = ServiceDefinition(
        id="grafana",
        title="Grafana",
        provider="container",
        target="svc-grafana",
        metric="memory",
    )
    state = await provider.check(definition)
    assert state.status == "OK"
    assert state.primary == "MEM 4.40%"


def test_unpinned_error_is_promoted_ahead_of_pinned_services() -> None:
    now = datetime.now(timezone.utc)
    states = [
        ServiceState("a", "A", "container", "a", "OK", "RUN", "", now, priority=10, pinned=True),
        ServiceState("b", "B", "container", "b", "OK", "RUN", "", now, priority=20, pinned=True),
        ServiceState("x", "X", "http", "x", "ERROR", "DOWN", "", now, priority=99, pinned=False),
    ]
    visible = _select_visible(states, 2)
    assert [state.id for state in visible] == ["x", "a"]
