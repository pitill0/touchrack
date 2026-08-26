from __future__ import annotations

import math
import shlex

from homelab_console.nodes.config import SshTransportConfig
from homelab_console.transports.base import CommandResult
from homelab_console.transports.errors import (
    SshAuthenticationError,
    SshConnectionError,
    SshHostKeyError,
    SshTransportError,
)
from homelab_console.transports.process import run_subprocess


class SshCommandRunner:
    """Execute fixed collector commands through the system OpenSSH client."""

    def __init__(
        self,
        config: SshTransportConfig,
        *,
        executable: str = "ssh",
    ) -> None:
        if not executable:
            raise ValueError("SSH executable must not be empty")
        self.config = config
        self.executable = executable

    async def run(
        self,
        command: tuple[str, ...],
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        if not command:
            raise ValueError("command must contain at least one element")
        if any("\x00" in argument for argument in command):
            raise ValueError("command arguments must not contain NUL bytes")

        argv = self._argv(command)
        command_timeout = (
            self.config.command_timeout if timeout is None else timeout
        )
        result = await run_subprocess(
            argv,
            # OpenSSH enforces the connection phase internally. The outer
            # deadline must also leave the remote command its full allowance.
            timeout=self.config.connect_timeout + command_timeout,
            reported_command=command,
        )
        if result.returncode == 255:
            raise _classify_ssh_error(result.stderr)
        return result

    def _argv(self, command: tuple[str, ...]) -> tuple[str, ...]:
        config = self.config
        # OpenSSH accepts an integer connection timeout. Round fractional
        # application values upward so the client never receives zero.
        connect_timeout = max(1, math.ceil(config.connect_timeout))
        return (
            self.executable,
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={config.known_hosts_file}",
            "-o",
            f"ConnectTimeout={connect_timeout}",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "NumberOfPasswordPrompts=0",
            "-p",
            str(config.port),
            "-i",
            config.identity_file,
            "--",
            f"{config.user}@{config.host}",
            shlex.join(command),
        )


def _classify_ssh_error(stderr: str) -> SshTransportError:
    normalized = stderr.casefold()
    message = stderr.strip() or "SSH transport failed"

    if (
        "permission denied" in normalized
        or "too many authentication failures" in normalized
        or "no more authentication methods" in normalized
    ):
        return SshAuthenticationError(message, stderr=stderr)
    if (
        "host key verification failed" in normalized
        or "remote host identification has changed" in normalized
        or ("host key is known" in normalized and "strict checking" in normalized)
    ):
        return SshHostKeyError(message, stderr=stderr)
    if any(
        marker in normalized
        for marker in (
            "connection refused",
            "connection timed out",
            "operation timed out",
            "no route to host",
            "could not resolve hostname",
            "connection closed",
            "connection reset",
            "network is unreachable",
        )
    ):
        return SshConnectionError(message, stderr=stderr)
    return SshTransportError(message, stderr=stderr)
