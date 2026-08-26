from __future__ import annotations

import asyncio
import re
import socket
from datetime import datetime, timezone

import pytest

from homelab_console.models import ContainerInfo, ContainersSnapshot
from homelab_console.services import (
    ContainerSnapshotServiceProvider,
    CruxServiceProvider,
    ServiceConfigError,
    ServiceDefinition,
    ServiceState,
    RemoteSystemdServiceProvider,
    ServicesManager,
    ServicesRegistry,
    SystemdServiceProvider,
    TcpServiceProvider,
    _select_visible,
)
from homelab_console.transports import CommandResult, SshConnectionError


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


@pytest.mark.asyncio
async def test_container_service_does_not_report_stale_inventory_as_healthy() -> None:
    snapshot = ContainersSnapshot(
        containers=(),
        collected_at=datetime.now(timezone.utc),
        stale=True,
        stale_error="SSH timed out",
    )
    provider = ContainerSnapshotServiceProvider(lambda: snapshot)

    state = await provider.check(
        ServiceDefinition("demo", "Demo", "container", "demo")
    )

    assert state.status == "UNKNOWN"
    assert state.primary == "Stale"
    assert state.error == "SSH timed out"


def test_unpinned_error_is_promoted_ahead_of_pinned_services() -> None:
    now = datetime.now(timezone.utc)
    states = [
        ServiceState("a", "A", "container", "a", "OK", "RUN", "", now, priority=10, pinned=True),
        ServiceState("b", "B", "container", "b", "OK", "RUN", "", now, priority=20, pinned=True),
        ServiceState("x", "X", "http", "x", "ERROR", "DOWN", "", now, priority=99, pinned=False),
    ]
    visible = _select_visible(states, 2)
    assert [state.id for state in visible] == ["x", "a"]


@pytest.mark.asyncio
async def test_remote_systemd_provider_uses_runner_without_privilege_escalation() -> None:
    class Runner:
        def __init__(self) -> None:
            self.commands = []

        async def run(self, command, *, timeout=None):
            self.commands.append((command, timeout))
            return CommandResult(command, "active\n", "", 0)

    runner = Runner()
    provider = RemoteSystemdServiceProvider(runner)
    definition = ServiceDefinition(
        "sshd",
        "SSH",
        "systemd",
        "sshd.service",
        node_id="rpi3",
    )

    state = await provider.check(definition)

    assert state.status == "OK"
    assert state.primary == "ACTIVE"
    assert runner.commands == [
        (("systemctl", "is-active", "sshd.service"), 3.0)
    ]
    assert "sudo" not in runner.commands[0][0]


@pytest.mark.asyncio
async def test_remote_systemd_provider_normalizes_inactive_and_transport_error() -> None:
    class Runner:
        def __init__(self, result=None, error=None) -> None:
            self.result = result
            self.error = error

        async def run(self, command, *, timeout=None):
            if self.error:
                raise self.error
            return self.result

    definition = ServiceDefinition("demo", "Demo", "systemd", "demo.service")
    inactive = await RemoteSystemdServiceProvider(
        Runner(CommandResult(("systemctl",), "inactive\n", "", 3))
    ).check(definition)
    unavailable = await RemoteSystemdServiceProvider(
        Runner(error=SshConnectionError("connection refused"))
    ).check(definition)

    assert inactive.status == "ERROR"
    assert inactive.error == "inactive"
    assert unavailable.status == "UNKNOWN"
    assert unavailable.error == "connection refused"


@pytest.mark.asyncio
async def test_crux_provider_checks_absolute_rc_script_without_shell() -> None:
    class Runner:
        def __init__(self) -> None:
            self.calls = []

        async def run(self, command, *, timeout=None):
            self.calls.append((command, timeout))
            return CommandResult(
                command,
                "/usr/sbin/sshd is running with pid 123\n",
                "",
                0,
            )

    runner = Runner()
    state = await CruxServiceProvider(runner).check(
        ServiceDefinition("ssh", "SSH", "crux", "sshd")
    )

    assert runner.calls == [(("/etc/rc.d/sshd", "status"), 3.0)]
    assert state.status == "OK"
    assert state.primary == "RUNNING"
    assert ("EXIT", "0") in state.details


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("returncode", "expected_status", "expected_primary"),
    [(1, "ERROR", "STOPPED"), (3, "ERROR", "STOPPED"), (4, "UNKNOWN", "UNKNOWN")],
)
async def test_crux_provider_normalizes_status_exit_codes(
    returncode,
    expected_status,
    expected_primary,
) -> None:
    class Runner:
        async def run(self, command, *, timeout=None):
            return CommandResult(command, "sshd status", "", returncode)

    state = await CruxServiceProvider(Runner()).check(
        ServiceDefinition("ssh", "SSH", "crux", "sshd")
    )

    assert state.status == expected_status
    assert state.primary == expected_primary



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


@pytest.mark.asyncio
async def test_systemd_provider_kills_and_reaps_timed_out_process(monkeypatch) -> None:
    class SlowProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.killed = False
            self.communicate_calls = 0

        async def communicate(self):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                await asyncio.sleep(60)
            return b"", b""

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

    process = SlowProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    provider = SystemdServiceProvider(timeout_seconds=0.1)
    definition = ServiceDefinition(
        "nginx",
        "Nginx",
        "systemd",
        "nginx.service",
    )

    state = await provider.check(definition)

    assert state.status == "UNKNOWN"
    assert state.error == "systemctl timed out after 0.1s"
    assert process.killed is True
    assert process.communicate_calls == 2



@pytest.mark.asyncio
async def test_systemd_provider_cancellation_kills_and_reaps_process(
    monkeypatch,
) -> None:
    started = asyncio.Event()
    released = asyncio.Event()

    class FakeProcess:
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.communicate_calls = 0

        async def communicate(self):
            self.communicate_calls += 1
            if self.killed:
                self.returncode = -9
                return b"", b""
            started.set()
            await released.wait()
            return b"active\n", b""

        def kill(self) -> None:
            self.killed = True
            released.set()

    process = FakeProcess()

    async def fake_create_subprocess_exec(*args, **kwargs):
        return process

    monkeypatch.setattr(
        asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    provider = SystemdServiceProvider(timeout_seconds=30)
    definition = ServiceDefinition(
        id="demo",
        title="Demo",
        provider="systemd",
        target="demo.service",
    )
    task = asyncio.create_task(provider.check(definition))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed is True
    assert process.communicate_calls >= 2


@pytest.mark.asyncio
async def test_services_manager_reuses_inflight_collection_after_caller_cancel() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class Registry:
        path = None

        def load(self):
            return (
                ServiceDefinition(
                    id="demo",
                    title="Demo",
                    provider="slow",
                    target="demo",
                ),
            )

    class SlowProvider:
        async def check(self, definition):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return ServiceState(
                id=definition.id,
                title=definition.title,
                provider=definition.provider,
                target=definition.target,
                status="OK",
                primary="UP",
                secondary="demo",
                checked_at=datetime.now(timezone.utc),
                priority=definition.priority,
                pinned=definition.pinned,
                group=definition.group,
            )

    manager = ServicesManager(Registry(), {"slow": SlowProvider()})

    first = asyncio.create_task(manager.collect())
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    second = asyncio.create_task(manager.collect())
    await asyncio.sleep(0)
    assert calls == 1

    release.set()
    snapshot = await second

    assert calls == 1
    assert len(snapshot.services) == 1
    assert snapshot.services[0].status == "OK"



def test_services_registry_loads_valid_typed_config(tmp_path) -> None:
    path = tmp_path / "services.yaml"
    path.write_text(
        """
services:
  - id: demo
    title: Demo
    provider: HTTP
    url: https://example.test/health
    pinned: false
    priority: 42
    group: edge
    node: rpi3
    metric: status
    timeout: 1.5
""".strip(),
        encoding="utf-8",
    )

    definitions = ServicesRegistry(path).load()

    assert len(definitions) == 1
    definition = definitions[0]
    assert definition.id == "demo"
    assert definition.title == "Demo"
    assert definition.provider == "http"
    assert definition.target == "https://example.test/health"
    assert definition.pinned is False
    assert definition.priority == 42
    assert definition.group == "edge"
    assert definition.node_id == "rpi3"
    assert definition.metric == "status"
    assert definition.options == {"timeout": 1.5}


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        (
            "services:\n  - id: demo\n    provider: http\n"
            "    target: http://demo\n    pinned: 'false'\n",
            "services[0].pinned must be a boolean",
        ),
        (
            "services:\n  - id: demo\n    provider: http\n"
            "    target: http://demo\n    priority: '10'\n",
            "services[0].priority must be an integer",
        ),
        (
            "services: {}\n",
            "'services' must be a list",
        ),
        (
            "services:\n  - demo\n",
            "services[0] must be a mapping",
        ),
        (
            "services:\n  - id: demo\n    provider: http\n",
            "services[0].target must be a non-empty string",
        ),
        (
            "services:\n  - id: demo\n    provider: systemd\n"
            "    target: sshd\n    node: RPi3\n",
            "services[0].node must match [a-z0-9][a-z0-9_-]*",
        ),
        (
            "services:\n  - id: demo\n    provider: systemd\n"
            "    target: -bad\n",
            "services[0].target is not a safe systemd unit",
        ),
    ],
)
def test_services_registry_rejects_invalid_types(
    tmp_path,
    yaml_text,
    message,
) -> None:
    path = tmp_path / "services.yaml"
    path.write_text(yaml_text, encoding="utf-8")

    with pytest.raises(ServiceConfigError, match=r"^" + re.escape(message) + r"$"):
        ServicesRegistry(path).load()


def test_services_registry_rejects_duplicate_ids(tmp_path) -> None:
    path = tmp_path / "services.yaml"
    path.write_text(
        """
services:
  - id: demo
    provider: systemd
    target: demo.service
  - id: demo
    provider: tcp
    target: 127.0.0.1:9000
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(
        ServiceConfigError,
        match=r"services\[1\]\.id duplicates service id 'demo'",
    ):
        ServicesRegistry(path).load()


@pytest.mark.asyncio
async def test_services_manager_surfaces_registry_validation_error(tmp_path) -> None:
    path = tmp_path / "services.yaml"
    path.write_text(
        """
services:
  - id: demo
    provider: http
    target: http://demo
    pinned: "false"
""".strip(),
        encoding="utf-8",
    )

    manager = ServicesManager(ServicesRegistry(path), {})
    snapshot = await manager.collect()

    assert snapshot.services == ()
    assert snapshot.config_path == str(path)
    assert snapshot.error == "services[0].pinned must be a boolean"


@pytest.mark.asyncio
async def test_services_manager_filters_unscoped_and_node_scoped_definitions() -> None:
    class Registry:
        path = None

        def load(self):
            return (
                ServiceDefinition("local", "Local", "fake", "local"),
                ServiceDefinition(
                    "remote",
                    "Remote",
                    "fake",
                    "remote",
                    node_id="rpi3",
                ),
                ServiceDefinition(
                    "other",
                    "Other",
                    "fake",
                    "other",
                    node_id="opi5",
                ),
            )

    class Provider:
        async def check(self, definition):
            return ServiceState(
                definition.id,
                definition.title,
                definition.provider,
                definition.target,
                "OK",
                "UP",
                definition.target,
                datetime.now(timezone.utc),
            )

    local = await ServicesManager(
        Registry(),
        {"fake": Provider()},
        node_id="lokalix",
        local_node_id="lokalix",
    ).collect()
    remote = await ServicesManager(
        Registry(),
        {"fake": Provider()},
        node_id="rpi3",
        local_node_id="lokalix",
    ).collect()

    assert [state.id for state in local.services] == ["local"]
    assert [state.id for state in remote.services] == ["remote"]



def test_services_registry_accepts_all_supported_provider_shapes(tmp_path) -> None:
    path = tmp_path / "services.yaml"
    path.write_text(
        """
services:
  - id: container-demo
    provider: container
    target: svc-demo
    metric: usage
  - id: systemd-demo
    provider: systemd
    target: ssh.service
  - id: crux-demo
    provider: crux
    target: sshd
  - id: http-demo
    provider: http
    target: https://example.test/health
    timeout: 1.25
    expect: 204
  - id: tcp-demo
    provider: tcp
    target: 127.0.0.1:5432
    timeout: 0.5
""".strip(),
        encoding="utf-8",
    )

    definitions = ServicesRegistry(path).load()

    assert [definition.provider for definition in definitions] == [
        "container",
        "systemd",
        "crux",
        "http",
        "tcp",
    ]
    assert definitions[0].metric == "usage"
    assert definitions[3].options == {"timeout": 1.25, "expect": 204}
    assert definitions[4].options == {"timeout": 0.5}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (
            "provider: mystery\n    target: demo",
            "services[0].provider must be one of: container, crux, http, systemd, tcp",
        ),
        (
            "provider: container\n    target: demo\n    metric: latency",
            "services[0].metric for container must be one of: cpu, mem, memory, status, usage",
        ),
        (
            "provider: systemd\n    target: ssh.service\n    metric: cpu",
            "services[0].metric for systemd must be 'status'",
        ),
        (
            "provider: crux\n    target: ../sshd",
            "services[0].target is not a safe CRUX service name",
        ),
        (
            "provider: http\n    target: ftp://example.test/health",
            "services[0].target for http must be an http:// or https:// URL",
        ),
        (
            "provider: http\n    target: https://example.test\n    timeout: '2'",
            "services[0].timeout must be a positive number",
        ),
        (
            "provider: http\n    target: https://example.test\n    timeout: 0",
            "services[0].timeout must be a positive number",
        ),
        (
            "provider: http\n    target: https://example.test\n    expect: '200'",
            "services[0].expect must be an integer HTTP status code",
        ),
        (
            "provider: http\n    target: https://example.test\n    expect: 700",
            "services[0].expect must be between 100 and 599",
        ),
        (
            "provider: tcp\n    target: localhost",
            "services[0].target for tcp must use host:port",
        ),
        (
            "provider: tcp\n    target: localhost:70000",
            "services[0].target for tcp must use host:port with port 1-65535",
        ),
        (
            "provider: tcp\n    target: localhost:5432\n    timeout: false",
            "services[0].timeout must be a positive number",
        ),
        (
            "provider: http\n    target: https://example.test\n    timout: 2",
            "services[0] has unsupported option(s) for http: timout",
        ),
        (
            "provider: container\n    target: demo\n    timeout: 2",
            "services[0] has unsupported option(s) for container: timeout",
        ),
    ],
)
def test_services_registry_rejects_invalid_provider_config(
    tmp_path,
    body,
    message,
) -> None:
    path = tmp_path / "services.yaml"
    path.write_text(
        "services:\n  - id: demo\n    " + body + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ServiceConfigError,
        match=r"^" + re.escape(message) + r"$",
    ):
        ServicesRegistry(path).load()
