from __future__ import annotations

import socket
from datetime import datetime, timezone

import pytest

from homelab_console.models import ContainerInfo, ContainersSnapshot
from homelab_console.services import (
    ContainerSnapshotServiceProvider,
    ServiceDefinition,
    ServiceState,
    ServicesManager,
    TcpServiceProvider,
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



def test_services_manager_unlimited_selection_supports_pagination() -> None:
    now = datetime.now(timezone.utc)
    states = [
        ServiceState(
            f"svc-{index}",
            f"Service {index}",
            "container",
            f"svc-{index}",
            "OK",
            "RUN",
            "",
            now,
            priority=index,
            pinned=True,
        )
        for index in range(15)
    ]

    visible = _select_visible(states, None)

    assert len(visible) == 15
    assert visible[0].id == "svc-0"
    assert visible[-1].id == "svc-14"



@pytest.mark.asyncio
async def test_container_provider_exposes_rich_service_details() -> None:
    snapshot = ContainersSnapshot(
        containers=(
            ContainerInfo(
                "abc123",
                "svc-prometheus",
                "prom/prometheus:latest",
                "running",
                "Up 7 days",
                cpu_percent="0.04%",
                memory_usage="132MiB / 15.4GiB",
                memory_percent="0.86%",
            ),
        ),
        collected_at=datetime.now(timezone.utc),
        engine="Podman",
    )
    provider = ContainerSnapshotServiceProvider(lambda: snapshot)
    definition = ServiceDefinition(
        "prometheus",
        "Prometheus",
        "container",
        "svc-prometheus",
    )

    state = await provider.check(definition)
    details = dict(state.details)

    assert state.status == "OK"
    assert details["STATE"] == "RUNNING"
    assert details["CPU"] == "0.04%"
    assert details["MEMORY"] == "0.86%"
    assert details["USAGE"] == "132MiB / 15.4GiB"
    assert details["IMAGE"] == "prom/prometheus:latest"
    assert details["CONTAINER"] == "svc-prometheus"
    assert details["ENGINE"] == "Podman"


@pytest.mark.asyncio
async def test_tcp_provider_exposes_connection_details(monkeypatch) -> None:
    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: _Connection())
    provider = TcpServiceProvider()
    definition = ServiceDefinition(
        "postgres",
        "Postgres",
        "tcp",
        "127.0.0.1:5432",
        options={"timeout": 1.5},
    )

    state = await provider.check(definition)

    assert dict(state.details) == {
        "STATE": "OPEN",
        "HOST": "127.0.0.1",
        "PORT": "5432",
        "TIMEOUT": "1.5s",
    }
