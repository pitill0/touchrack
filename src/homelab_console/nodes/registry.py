from __future__ import annotations

from collections.abc import Iterable, Iterator

from homelab_console.nodes.runtime import NodeRuntime


class NodeRegistry:
    """Ordered, immutable registry of configured node runtimes."""

    def __init__(
        self,
        runtimes: Iterable[NodeRuntime],
        *,
        default_node_id: str,
    ) -> None:
        ordered = tuple(runtimes)
        if not ordered:
            raise ValueError("node registry must contain at least one node")

        by_id: dict[str, NodeRuntime] = {}
        for runtime in ordered:
            node_id = runtime.node.id
            if node_id in by_id:
                raise ValueError(f"duplicate node id: {node_id}")
            by_id[node_id] = runtime

        if default_node_id not in by_id:
            raise ValueError(f"default node does not exist: {default_node_id}")

        self._ordered = ordered
        self._by_id = by_id
        self._default_node_id = default_node_id

    @property
    def default(self) -> NodeRuntime:
        return self._by_id[self._default_node_id]

    def get(self, node_id: str) -> NodeRuntime:
        try:
            return self._by_id[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown node id: {node_id}") from exc

    def all(self) -> tuple[NodeRuntime, ...]:
        return self._ordered

    def __iter__(self) -> Iterator[NodeRuntime]:
        return iter(self._ordered)

    def __len__(self) -> int:
        return len(self._ordered)
