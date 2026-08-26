from __future__ import annotations

from homelab_console.providers import RemoteContainersProvider
from homelab_console.transports import CommandResult, SshAuthenticationError


PODMAN_PAYLOAD = """\
touchrack_containers_protocol=1
engine=Podman
ps_begin
[{"Id":"aaa111","Names":["web"],"Image":"nginx:latest","State":"running","Status":"Up 2 hours"},{"Id":"bbb222","Names":["db"],"Image":"postgres:17","State":"exited","Status":"Exited (0)"}]
ps_end
stats_status=0
stats_begin
[{"id":"aaa111","cpu_percent":"1.25%","mem_usage":"20MiB / 1GiB","mem_percent":"1.95%"}]
stats_end
"""


DOCKER_PAYLOAD = """\
touchrack_containers_protocol=1
engine=Docker
ps_begin
{"ID":"ddd444","Names":"worker","Image":"worker:v1","State":"running","Status":"Up 5 minutes"}
ps_end
stats_status=0
stats_begin
{"ID":"ddd444","CPUPerc":"2.50%","MemUsage":"40MiB / 2GiB","MemPerc":"1.95%"}
stats_end
"""


class FakeRunner:
    def __init__(
        self,
        *,
        stdout: str = PODMAN_PAYLOAD,
        stderr: str = "",
        returncode: int = 0,
        error: Exception | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.error = error
        self.commands: list[tuple[str, ...]] = []

    async def run(self, command: tuple[str, ...], *, timeout=None):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return CommandResult(
            command=command,
            stdout=self.stdout,
            stderr=self.stderr,
            returncode=self.returncode,
        )


async def test_remote_podman_collection_reuses_universal_normalization() -> None:
    runner = FakeRunner()

    snapshot = await RemoteContainersProvider(runner).collect()

    assert snapshot.error is None
    assert snapshot.stats_error is None
    assert snapshot.engine == "Podman"
    assert [item.name for item in snapshot.containers] == ["web", "db"]
    assert snapshot.containers[0].cpu_percent == "1.25%"
    assert snapshot.containers[0].memory_usage == "20MiB / 1GiB"
    assert snapshot.containers[1].cpu_percent is None
    command = runner.commands[0]
    assert command[:2] == ("sh", "-c")
    assert "command -v podman" in command[2]
    assert "command -v docker" in command[2]
    assert "sudo" not in command[2]


async def test_remote_docker_collection_reuses_universal_normalization() -> None:
    snapshot = await RemoteContainersProvider(
        FakeRunner(stdout=DOCKER_PAYLOAD)
    ).collect()

    assert snapshot.error is None
    assert snapshot.engine == "Docker"
    assert snapshot.total == 1
    container = snapshot.containers[0]
    assert container.name == "worker"
    assert container.cpu_percent == "2.50%"
    assert container.memory_percent == "1.95%"


async def test_remote_privileged_collection_calls_only_fixed_helper() -> None:
    runner = FakeRunner()

    snapshot = await RemoteContainersProvider(
        runner,
        helper="/usr/local/libexec/touchrack-container-inventory",
    ).collect()

    assert snapshot.error is None
    assert runner.commands == [
        ("sudo", "-n", "/usr/local/libexec/touchrack-container-inventory")
    ]


async def test_remote_stats_failure_preserves_container_inventory() -> None:
    payload = PODMAN_PAYLOAD.replace("stats_status=0", "stats_status=125").replace(
        '[{"id":"aaa111","cpu_percent":"1.25%","mem_usage":"20MiB / 1GiB","mem_percent":"1.95%"}]',
        "permission denied",
    )

    snapshot = await RemoteContainersProvider(FakeRunner(stdout=payload)).collect()

    assert snapshot.error is None
    assert snapshot.total == 2
    assert snapshot.stats_error == (
        "Resource statistics unavailable: permission denied"
    )
    assert all(item.cpu_percent is None for item in snapshot.containers)


async def test_remote_containers_reports_missing_engine_without_fallback() -> None:
    snapshot = await RemoteContainersProvider(
        FakeRunner(
            stderr="Neither Podman nor Docker is installed or available in PATH",
            returncode=127,
        )
    ).collect()

    assert snapshot.engine == "Remote"
    assert snapshot.containers == ()
    assert snapshot.error == (
        "RuntimeError: remote containers collector failed: "
        "Neither Podman nor Docker is installed or available in PATH"
    )


async def test_remote_containers_converts_transport_and_protocol_failures() -> None:
    transport = await RemoteContainersProvider(
        FakeRunner(error=SshAuthenticationError("permission denied"))
    ).collect()
    malformed = await RemoteContainersProvider(
        FakeRunner(stdout=PODMAN_PAYLOAD.replace("ps_end", "missing"))
    ).collect()

    assert transport.error == "SshAuthenticationError: permission denied"
    assert malformed.error == "ValueError: invalid remote containers collector output"


async def test_malformed_stats_are_degraded_without_losing_inventory() -> None:
    payload = PODMAN_PAYLOAD.replace(
        '[{"id":"aaa111","cpu_percent":"1.25%","mem_usage":"20MiB / 1GiB","mem_percent":"1.95%"}]',
        "not-json",
    )

    snapshot = await RemoteContainersProvider(FakeRunner(stdout=payload)).collect()

    assert snapshot.error is None
    assert snapshot.total == 2
    assert snapshot.stats_error is not None
    assert snapshot.stats_error.startswith("Resource statistics unavailable:")
