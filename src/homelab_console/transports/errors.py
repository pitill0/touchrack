from __future__ import annotations


class TransportError(RuntimeError):
    """Base error for failures before a monitored command can be evaluated."""


class TransportUnavailable(TransportError):
    """The executable required by a transport is not available."""


class CommandTimeout(TransportError):
    """A transport command exceeded its configured deadline."""

    def __init__(self, command: tuple[str, ...], timeout: float) -> None:
        self.command = command
        self.timeout = timeout
        super().__init__(f"command exceeded {timeout:g}s")


class SshTransportError(TransportError):
    """OpenSSH could not establish or maintain the remote transport."""

    def __init__(self, message: str, *, stderr: str = "") -> None:
        self.stderr = stderr
        super().__init__(message)


class SshAuthenticationError(SshTransportError):
    """The remote node rejected the configured SSH identity."""


class SshHostKeyError(SshTransportError):
    """Strict SSH host-key verification failed."""


class SshConnectionError(SshTransportError):
    """The remote SSH endpoint could not be reached."""
