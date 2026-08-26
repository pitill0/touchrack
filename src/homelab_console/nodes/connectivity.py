from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


ConnectionStatus = Literal["unknown", "online", "offline"]


@dataclass(frozen=True, slots=True)
class NodeConnectivity:
    status: ConnectionStatus = "unknown"
    checked_at: datetime | None = None
    error: str | None = None


class NodeConnectivityRegistry:
    """Keep transport health separate from capability snapshots and views."""

    def __init__(self, node_ids: tuple[str, ...]) -> None:
        self._states = {node_id: NodeConnectivity() for node_id in node_ids}

    def get(self, node_id: str) -> NodeConnectivity:
        return self._states[node_id]

    def all(self) -> dict[str, NodeConnectivity]:
        return dict(self._states)

    def record(self, node_id: str, *, error: str | None) -> NodeConnectivity:
        state = NodeConnectivity(
            status="offline" if error else "online",
            checked_at=datetime.now(timezone.utc),
            error=error,
        )
        self._states[node_id] = state
        return state
