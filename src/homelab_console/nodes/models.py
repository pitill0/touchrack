from __future__ import annotations

import re
from dataclasses import dataclass


_NODE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*")


@dataclass(frozen=True, slots=True)
class Node:
    """Stable identity and presentation metadata for one monitored system."""

    id: str
    title: str
    transport: str = "local"

    def __post_init__(self) -> None:
        if not _NODE_ID_PATTERN.fullmatch(self.id):
            raise ValueError(
                "node id must match [a-z0-9][a-z0-9_-]*"
            )
        if not self.title.strip():
            raise ValueError("node title must not be empty")
        if not self.transport.strip():
            raise ValueError("node transport must not be empty")
