from __future__ import annotations

from homelab_console.transports.base import CommandResult
from homelab_console.transports.process import run_subprocess


class LocalCommandRunner:
    """Execute read-only collector commands on the TouchRack host."""

    def __init__(self, *, timeout: float = 5.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.timeout = timeout

    async def run(
        self,
        command: tuple[str, ...],
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        if not command:
            raise ValueError("command must contain at least one element")
        return await run_subprocess(
            command,
            timeout=self.timeout if timeout is None else timeout,
        )
