from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Decoded result of one command executed through a transport."""

    command: tuple[str, ...]
    stdout: str
    stderr: str
    returncode: int


class CommandRunner(Protocol):
    async def run(
        self,
        command: tuple[str, ...],
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        """Execute one fixed argument vector without invoking a local shell."""
