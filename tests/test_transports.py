from __future__ import annotations

import asyncio
import sys

import pytest

from homelab_console.nodes import SshTransportConfig
from homelab_console.transports import (
    CircuitBreakerCommandRunner,
    CircuitOpenError,
    CommandResult,
    CommandTimeout,
    LocalCommandRunner,
    SshAuthenticationError,
    SshCommandRunner,
    SshConnectionError,
    SshHostKeyError,
    SshTransportError,
    TransportUnavailable,
)
from homelab_console.transports import process as process_module
from homelab_console.transports import ssh as ssh_module


def ssh_config() -> SshTransportConfig:
    return SshTransportConfig(
        host="192.168.1.40",
        user="touchrack-monitor",
        port=2222,
        identity_file="/etc/touchrack/keys/rpi3",
        known_hosts_file="/etc/touchrack/ssh_known_hosts",
        connect_timeout=3.2,
        command_timeout=8.0,
    )


@pytest.mark.asyncio
async def test_circuit_breaker_suppresses_attempts_during_cooldown() -> None:
    now = [100.0]

    class Runner:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, command, *, timeout=None):
            self.calls += 1
            raise SshConnectionError("connection refused")

    inner = Runner()
    runner = CircuitBreakerCommandRunner(
        inner,
        cooldown_seconds=5,
        failure_types=(SshTransportError,),
        clock=lambda: now[0],
    )

    with pytest.raises(SshConnectionError):
        await runner.run(("hostname",))
    with pytest.raises(CircuitOpenError) as caught:
        await runner.run(("df",))

    assert inner.calls == 1
    assert caught.value.retry_after == 5.0
    assert str(caught.value) == "transport temporarily unavailable; retry in 5.0s"


@pytest.mark.asyncio
async def test_circuit_breaker_recovers_after_successful_probe() -> None:
    now = [100.0]

    class Runner:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, command, *, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise SshConnectionError("connection refused")
            return CommandResult(command, "ok\n", "", 0)

    inner = Runner()
    runner = CircuitBreakerCommandRunner(
        inner,
        cooldown_seconds=5,
        failure_types=(SshTransportError,),
        clock=lambda: now[0],
    )

    with pytest.raises(SshConnectionError):
        await runner.run(("first",))
    now[0] += 5
    recovered = await runner.run(("probe",))
    normal = await runner.run(("next",))

    assert recovered.stdout == "ok\n"
    assert normal.command == ("next",)
    assert inner.calls == 3


@pytest.mark.asyncio
async def test_circuit_breaker_allows_only_one_concurrent_recovery_probe() -> None:
    now = [100.0]
    probe_started = asyncio.Event()
    release_probe = asyncio.Event()

    class Runner:
        def __init__(self) -> None:
            self.calls = 0

        async def run(self, command, *, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise SshConnectionError("connection refused")
            probe_started.set()
            await release_probe.wait()
            return CommandResult(command, "ok\n", "", 0)

    inner = Runner()
    runner = CircuitBreakerCommandRunner(
        inner,
        cooldown_seconds=5,
        failure_types=(SshTransportError,),
        clock=lambda: now[0],
    )
    with pytest.raises(SshConnectionError):
        await runner.run(("first",))

    now[0] += 5
    probe = asyncio.create_task(runner.run(("probe",)))
    await probe_started.wait()
    with pytest.raises(CircuitOpenError):
        await runner.run(("parallel",))

    release_probe.set()
    assert (await probe).stdout == "ok\n"
    assert inner.calls == 2


@pytest.mark.asyncio
async def test_circuit_breaker_ignores_remote_command_exit_status() -> None:
    class Runner:
        async def run(self, command, *, timeout=None):
            return CommandResult(command, "inactive\n", "", 3)

    runner = CircuitBreakerCommandRunner(
        Runner(),
        failure_types=(SshTransportError,),
    )

    first = await runner.run(("systemctl", "is-active", "demo"))
    second = await runner.run(("systemctl", "is-active", "demo"))

    assert first.returncode == second.returncode == 3


@pytest.mark.asyncio
async def test_local_runner_returns_stdout_stderr_and_exit_status() -> None:
    runner = LocalCommandRunner()
    result = await runner.run(
        (
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
        )
    )

    assert result.stdout == "out\n"
    assert result.stderr == "err\n"
    assert result.returncode == 7
    assert result.command[0] == sys.executable


@pytest.mark.asyncio
async def test_local_runner_reports_missing_executable() -> None:
    runner = LocalCommandRunner()
    with pytest.raises(TransportUnavailable, match="executable not found"):
        await runner.run(("touchrack-command-that-does-not-exist",))


@pytest.mark.asyncio
async def test_subprocess_timeout_kills_and_reaps_child(monkeypatch) -> None:
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
            self.returncode = 0
            return b"late", b""

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

    with pytest.raises(CommandTimeout) as caught:
        await process_module.run_subprocess(("slow",), timeout=0.01)

    assert caught.value.command == ("slow",)
    assert caught.value.timeout == 0.01
    assert process.killed is True
    assert process.communicate_calls == 2


@pytest.mark.asyncio
async def test_subprocess_cancellation_kills_and_reaps_child(monkeypatch) -> None:
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
            return b"", b""

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

    task = asyncio.create_task(
        process_module.run_subprocess(("slow",), timeout=30)
    )
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert process.killed is True
    assert process.communicate_calls == 2


@pytest.mark.asyncio
async def test_ssh_runner_builds_hardened_openssh_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_run_subprocess(
        argv: tuple[str, ...],
        *,
        timeout: float,
        reported_command: tuple[str, ...] | None = None,
    ) -> CommandResult:
        captured.update(
            argv=argv,
            timeout=timeout,
            reported_command=reported_command,
        )
        return CommandResult(
            command=reported_command or argv,
            stdout="ok\n",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr(ssh_module, "run_subprocess", fake_run_subprocess)
    runner = SshCommandRunner(ssh_config())
    command = ("printf", "%s", "hello; not-a-second-command")

    result = await runner.run(command)

    argv = captured["argv"]
    assert isinstance(argv, tuple)
    assert argv[0:2] == ("ssh", "-T")
    for option in (
        "BatchMode=yes",
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=/etc/touchrack/ssh_known_hosts",
        "ConnectTimeout=4",
        "ConnectionAttempts=1",
        "NumberOfPasswordPrompts=0",
    ):
        assert option in argv
    assert argv[argv.index("-p") + 1] == "2222"
    assert argv[argv.index("-i") + 1] == "/etc/touchrack/keys/rpi3"
    separator = argv.index("--")
    assert argv[separator + 1] == "touchrack-monitor@192.168.1.40"
    assert argv[separator + 2] == "printf %s 'hello; not-a-second-command'"
    assert len(argv) == separator + 3
    assert captured["timeout"] == 11.2
    assert captured["reported_command"] == command
    assert result.command == command


@pytest.mark.asyncio
async def test_ssh_runner_honours_per_command_timeout(monkeypatch) -> None:
    captured_timeout = None

    async def fake_run_subprocess(
        argv: tuple[str, ...],
        *,
        timeout: float,
        reported_command: tuple[str, ...] | None = None,
    ) -> CommandResult:
        nonlocal captured_timeout
        captured_timeout = timeout
        return CommandResult(reported_command or argv, "", "", 0)

    monkeypatch.setattr(ssh_module, "run_subprocess", fake_run_subprocess)

    await SshCommandRunner(ssh_config()).run(("true",), timeout=1.5)

    assert captured_timeout == 4.7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stderr", "error_type"),
    [
        ("Permission denied (publickey).", SshAuthenticationError),
        ("Host key verification failed.", SshHostKeyError),
        ("connect to host 192.168.1.40: Connection refused", SshConnectionError),
        ("unexpected OpenSSH transport failure", SshTransportError),
    ],
)
async def test_ssh_runner_classifies_transport_failures(
    monkeypatch,
    stderr: str,
    error_type: type[SshTransportError],
) -> None:
    async def fake_run_subprocess(
        argv: tuple[str, ...],
        *,
        timeout: float,
        reported_command: tuple[str, ...] | None = None,
    ) -> CommandResult:
        return CommandResult(reported_command or argv, "", stderr, 255)

    monkeypatch.setattr(ssh_module, "run_subprocess", fake_run_subprocess)

    with pytest.raises(error_type) as caught:
        await SshCommandRunner(ssh_config()).run(("uname", "-a"))

    assert caught.value.stderr == stderr


@pytest.mark.asyncio
async def test_ssh_runner_preserves_remote_command_exit_status(monkeypatch) -> None:
    async def fake_run_subprocess(
        argv: tuple[str, ...],
        *,
        timeout: float,
        reported_command: tuple[str, ...] | None = None,
    ) -> CommandResult:
        return CommandResult(reported_command or argv, "", "not found\n", 127)

    monkeypatch.setattr(ssh_module, "run_subprocess", fake_run_subprocess)

    result = await SshCommandRunner(ssh_config()).run(("missing-tool",))

    assert result.returncode == 127
    assert result.stderr == "not found\n"


@pytest.mark.asyncio
async def test_ssh_runner_rejects_empty_and_nul_arguments() -> None:
    runner = SshCommandRunner(ssh_config())

    with pytest.raises(ValueError, match="at least one"):
        await runner.run(())
    with pytest.raises(ValueError, match="NUL"):
        await runner.run(("printf", "bad\x00argument"))
