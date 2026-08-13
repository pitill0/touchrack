from __future__ import annotations

from typing import Protocol

from homelab_console.models import ContainersSnapshot, HostSnapshot


class HostProvider(Protocol):
    async def collect(self) -> HostSnapshot:
        """Return one immutable snapshot of the host."""


class ContainersProvider(Protocol):
    async def collect(self) -> ContainersSnapshot:
        """Return one immutable snapshot of local containers."""
