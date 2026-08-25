from __future__ import annotations

from homelab_console.providers import RemoteHostProvider
from homelab_console.transports import CommandResult, SshConnectionError


VALID_PAYLOAD = """\
touchrack_protocol=1
hostname=rpi3
uptime_seconds=123.5
cpu_percent=17.25
load_average=0.10 0.20 0.30
memory=1048576 2097152
disk=3145728 4194304
temperature_c=48.50
ip_address=192.168.1.33
"""


class FakeRunner:
    def __init__(
        self,
        *,
        stdout: str = VALID_PAYLOAD,
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


async def test_remote_provider_collects_universal_host_snapshot() -> None:
    runner = FakeRunner()

    snapshot = await RemoteHostProvider(
        runner,
        fallback_hostname="Raspberry Pi 3",
    ).collect()

    assert snapshot.hostname == "rpi3"
    assert snapshot.uptime_seconds == 123.5
    assert snapshot.cpu_percent == 17.25
    assert snapshot.load_average == (0.1, 0.2, 0.3)
    assert (snapshot.memory_used, snapshot.memory_total) == (1048576, 2097152)
    assert (snapshot.disk_used, snapshot.disk_total) == (3145728, 4194304)
    assert snapshot.temperature_c == 48.5
    assert snapshot.ip_address == "192.168.1.33"
    assert snapshot.error is None
    assert runner.commands[0][:2] == ("sh", "-c")
    assert "/proc/stat" in runner.commands[0][2]


async def test_remote_provider_supports_missing_optional_readings() -> None:
    payload = VALID_PAYLOAD.replace("temperature_c=48.50", "temperature_c=-").replace(
        "ip_address=192.168.1.33", "ip_address=-"
    )

    snapshot = await RemoteHostProvider(
        FakeRunner(stdout=payload),
        fallback_hostname="rpi3",
    ).collect()

    assert snapshot.temperature_c is None
    assert snapshot.ip_address is None
    assert snapshot.error is None
    assert snapshot.health == "Degraded"


async def test_remote_provider_converts_transport_failure_to_snapshot() -> None:
    snapshot = await RemoteHostProvider(
        FakeRunner(error=SshConnectionError("connection refused")),
        fallback_hostname="Raspberry Pi 3",
    ).collect()

    assert snapshot.hostname == "Raspberry Pi 3"
    assert snapshot.health == "Unavailable"
    assert snapshot.error == "SshConnectionError: connection refused"


async def test_remote_provider_rejects_failed_or_malformed_collection() -> None:
    failed = await RemoteHostProvider(
        FakeRunner(stderr="sh: awk: not found", returncode=127),
        fallback_hostname="rpi3",
    ).collect()
    malformed = await RemoteHostProvider(
        FakeRunner(stdout=VALID_PAYLOAD.replace("cpu_percent=17.25\n", "")),
        fallback_hostname="rpi3",
    ).collect()

    assert failed.error == "RuntimeError: remote collector failed: sh: awk: not found"
    assert malformed.error == "ValueError: unsupported remote collector output"
