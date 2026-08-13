from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class HostSnapshot:
    hostname: str
    uptime_seconds: float
    cpu_percent: float
    load_average: tuple[float, float, float] | None
    memory_used: int
    memory_total: int
    disk_used: int
    disk_total: int
    temperature_c: float | None
    ip_address: str | None
    collected_at: datetime
    error: str | None = None

    @property
    def health(self) -> str:
        if self.error:
            return "Unavailable"
        if self.temperature_c is None or self.ip_address is None:
            return "Degraded"
        return "Healthy"


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    container_id: str
    name: str
    image: str
    state: str
    status: str
    cpu_percent: str | None = None
    memory_usage: str | None = None
    memory_percent: str | None = None

    @property
    def is_running(self) -> bool:
        return self.state.lower() == "running"


@dataclass(frozen=True, slots=True)
class ContainersSnapshot:
    containers: tuple[ContainerInfo, ...]
    collected_at: datetime
    engine: str = "Unknown"
    error: str | None = None
    stats_error: str | None = None

    @property
    def total(self) -> int:
        return len(self.containers)

    @property
    def running(self) -> int:
        return sum(container.is_running for container in self.containers)

    @property
    def stopped(self) -> int:
        return self.total - self.running
