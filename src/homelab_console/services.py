from __future__ import annotations

import asyncio
import os
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib import error as urllib_error
from urllib import request as urllib_request

import yaml

from homelab_console.models import ContainersSnapshot


SERVICE_STATUSES = ("OK", "IDLE", "WARN", "ERROR", "UNKNOWN")


@dataclass(frozen=True, slots=True)
class ServiceDefinition:
    id: str
    title: str
    provider: str
    target: str
    pinned: bool = True
    priority: int = 100
    group: str = "core"
    metric: str = "status"
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ServiceState:
    id: str
    title: str
    provider: str
    target: str
    status: str
    primary: str
    secondary: str
    checked_at: datetime
    priority: int = 100
    pinned: bool = True
    group: str = "core"
    error: str | None = None
    details: tuple[tuple[str, str], ...] = ()

    @property
    def is_attention(self) -> bool:
        return self.status in {"WARN", "ERROR"}


@dataclass(frozen=True, slots=True)
class ServicesSnapshot:
    services: tuple[ServiceState, ...]
    collected_at: datetime
    config_path: str | None = None
    error: str | None = None


class ServiceProvider(Protocol):
    async def check(self, definition: ServiceDefinition) -> ServiceState:
        """Return one normalized state for a configured service."""


class ContainerSnapshotServiceProvider:
    """Resolve service health from the already-collected container snapshot.

    This keeps SERVICES lightweight: it does not execute a second Podman/Docker
    stats pass just to render service cards.
    """

    def __init__(self, snapshot_getter: Callable[[], ContainersSnapshot | None]) -> None:
        self.snapshot_getter = snapshot_getter

    async def check(self, definition: ServiceDefinition) -> ServiceState:
        checked_at = datetime.now(timezone.utc)
        snapshot = self.snapshot_getter()
        if snapshot is None:
            return _state(definition, "UNKNOWN", "Waiting", "Container snapshot pending", checked_at)
        if snapshot.error:
            return _state(definition, "UNKNOWN", "Unavailable", snapshot.error, checked_at, error=snapshot.error)
        container = next(
            (item for item in snapshot.containers if item.name == definition.target),
            None,
        )
        if container is None:
            return _state(definition, "ERROR", "Missing", definition.target, checked_at, error="Container not found")

        status = "OK" if container.is_running else "ERROR"
        metric = definition.metric.lower()
        if metric == "cpu":
            primary = f"CPU {container.cpu_percent or '--'}"
        elif metric in {"memory", "mem"}:
            primary = f"MEM {container.memory_percent or '--'}"
        elif metric == "usage":
            primary = container.memory_usage or "No live stats"
        else:
            primary = "RUNNING" if container.is_running else "STOPPED"
        secondary = container.status
        details = (
            ("STATE", "RUNNING" if container.is_running else "STOPPED"),
            ("CPU", container.cpu_percent or "--"),
            ("MEMORY", container.memory_percent or "--"),
            ("USAGE", container.memory_usage or "No live stats"),
            ("IMAGE", container.image or "--"),
            ("CONTAINER", container.name),
            ("STATUS", container.status or "--"),
            ("ENGINE", snapshot.engine or "Unknown"),
        )
        return _state(
            definition,
            status,
            primary,
            secondary,
            checked_at,
            details=details,
        )


class SystemdServiceProvider:
    async def check(self, definition: ServiceDefinition) -> ServiceState:
        checked_at = datetime.now(timezone.utc)
        try:
            process = await asyncio.create_subprocess_exec(
                "systemctl",
                "is-active",
                definition.target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=3.0)
            text = stdout.decode(errors="replace").strip() or stderr.decode(errors="replace").strip()
            active = process.returncode == 0 and text == "active"
            return _state(
                definition,
                "OK" if active else "ERROR",
                text.upper() if text else "UNKNOWN",
                definition.target,
                checked_at,
                error=None if active else text or "systemctl reported inactive",
                details=(
                    ("STATE", text.upper() if text else "UNKNOWN"),
                    ("UNIT", definition.target),
                ),
            )
        except (FileNotFoundError, asyncio.TimeoutError) as exc:
            return _state(
                definition,
                "UNKNOWN",
                "Unavailable",
                str(exc),
                checked_at,
                error=str(exc),
                details=(("UNIT", definition.target),),
            )


class HttpServiceProvider:
    async def check(self, definition: ServiceDefinition) -> ServiceState:
        return await asyncio.to_thread(self._check_sync, definition)

    def _check_sync(self, definition: ServiceDefinition) -> ServiceState:
        checked_at = datetime.now(timezone.utc)
        timeout = float(definition.options.get("timeout", 2.0))
        expected = int(definition.options.get("expect", 200))
        started = datetime.now(timezone.utc)
        try:
            req = urllib_request.Request(definition.target, method="GET")
            with urllib_request.urlopen(req, timeout=timeout) as response:
                code = int(response.status)
            latency_ms = max(0, round((datetime.now(timezone.utc) - started).total_seconds() * 1000))
            ok = code == expected
            return _state(
                definition,
                "OK" if ok else "WARN",
                f"HTTP {code}",
                f"{latency_ms} ms",
                checked_at,
                error=None if ok else f"Expected HTTP {expected}",
                details=(
                    ("HTTP", str(code)),
                    ("LATENCY", f"{latency_ms} ms"),
                    ("EXPECTED", str(expected)),
                    ("TIMEOUT", f"{timeout:g}s"),
                    ("URL", definition.target),
                ),
            )
        except (urllib_error.URLError, TimeoutError, ValueError) as exc:
            return _state(
                definition,
                "ERROR",
                "HTTP DOWN",
                str(exc),
                checked_at,
                error=str(exc),
                details=(
                    ("EXPECTED", str(expected)),
                    ("TIMEOUT", f"{timeout:g}s"),
                    ("URL", definition.target),
                ),
            )


class TcpServiceProvider:
    async def check(self, definition: ServiceDefinition) -> ServiceState:
        return await asyncio.to_thread(self._check_sync, definition)

    def _check_sync(self, definition: ServiceDefinition) -> ServiceState:
        checked_at = datetime.now(timezone.utc)
        host, port = _split_host_port(definition.target)
        timeout = float(definition.options.get("timeout", 2.0))
        try:
            with socket.create_connection((host, port), timeout=timeout):
                pass
            return _state(
                definition,
                "OK",
                f"TCP {port}",
                host,
                checked_at,
                details=(
                    ("STATE", "OPEN"),
                    ("HOST", host),
                    ("PORT", str(port)),
                    ("TIMEOUT", f"{timeout:g}s"),
                ),
            )
        except OSError as exc:
            return _state(
                definition,
                "ERROR",
                "TCP DOWN",
                str(exc),
                checked_at,
                error=str(exc),
                details=(
                    ("STATE", "DOWN"),
                    ("HOST", host),
                    ("PORT", str(port)),
                    ("TIMEOUT", f"{timeout:g}s"),
                ),
            )


class ServicesRegistry:
    """Load declarative service definitions without coupling them to Textual."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else _default_config_path()

    def load(self) -> tuple[ServiceDefinition, ...]:
        if self.path is None or not self.path.exists():
            return ()
        payload = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        rows = payload.get("services", []) if isinstance(payload, dict) else []
        definitions: list[ServiceDefinition] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            service_id = str(row.get("id") or "").strip()
            title = str(row.get("title") or service_id).strip()
            provider = str(row.get("provider") or "").strip().lower()
            target = str(row.get("target") or row.get("name") or row.get("url") or "").strip()
            if not service_id or not title or not provider or not target:
                continue
            reserved = {"id", "title", "provider", "target", "name", "url", "pinned", "priority", "group", "metric"}
            options = {key: value for key, value in row.items() if key not in reserved}
            definitions.append(
                ServiceDefinition(
                    id=service_id,
                    title=title,
                    provider=provider,
                    target=target,
                    pinned=bool(row.get("pinned", True)),
                    priority=int(row.get("priority", 100)),
                    group=str(row.get("group", "core")),
                    metric=str(row.get("metric", "status")),
                    options=options,
                )
            )
        return tuple(definitions)


class ServicesManager:
    """Collect normalized service state and select the glanceable dashboard set."""

    def __init__(
        self,
        registry: ServicesRegistry,
        providers: Mapping[str, ServiceProvider],
        *,
        max_visible: int | None = None,
    ) -> None:
        self.registry = registry
        self.providers = dict(providers)
        self.max_visible = max_visible

    async def collect(self) -> ServicesSnapshot:
        collected_at = datetime.now(timezone.utc)
        try:
            definitions = self.registry.load()
        except (OSError, ValueError, yaml.YAMLError) as exc:
            return ServicesSnapshot((), collected_at, str(self.registry.path) if self.registry.path else None, str(exc))
        if not definitions:
            return ServicesSnapshot((), collected_at, str(self.registry.path) if self.registry.path else None)

        states = await asyncio.gather(*(self._check(definition) for definition in definitions))
        visible = _select_visible(states, self.max_visible)
        return ServicesSnapshot(
            tuple(visible),
            collected_at,
            str(self.registry.path) if self.registry.path else None,
        )

    async def _check(self, definition: ServiceDefinition) -> ServiceState:
        provider = self.providers.get(definition.provider)
        if provider is None:
            return _state(
                definition,
                "UNKNOWN",
                "No provider",
                definition.provider,
                datetime.now(timezone.utc),
                error=f"Unknown provider: {definition.provider}",
            )
        try:
            return await provider.check(definition)
        except Exception as exc:  # Provider failures must never take down the console.
            return _state(definition, "ERROR", "Check failed", str(exc), datetime.now(timezone.utc), error=str(exc))


def _select_visible(states: list[ServiceState], max_visible: int | None) -> list[ServiceState]:
    # Unpinned warning/error services are promoted temporarily. Pinned services
    # remain the normal dashboard set, ordered by explicit priority.
    attention = [state for state in states if state.is_attention and not state.pinned]
    pinned = [state for state in states if state.pinned]
    selected = sorted(attention, key=lambda state: (0 if state.status == "ERROR" else 1, state.priority, state.title.lower()))
    selected.extend(sorted(pinned, key=lambda state: (state.priority, state.title.lower())))
    deduped: list[ServiceState] = []
    seen: set[str] = set()
    for state in selected:
        if state.id in seen:
            continue
        seen.add(state.id)
        deduped.append(state)
    return deduped if max_visible is None else deduped[:max_visible]


def _state(
    definition: ServiceDefinition,
    status: str,
    primary: str,
    secondary: str,
    checked_at: datetime,
    *,
    error: str | None = None,
    details: tuple[tuple[str, str], ...] = (),
) -> ServiceState:
    normalized = status if status in SERVICE_STATUSES else "UNKNOWN"
    return ServiceState(
        id=definition.id,
        title=definition.title,
        provider=definition.provider,
        target=definition.target,
        status=normalized,
        primary=primary,
        secondary=secondary,
        checked_at=checked_at,
        priority=definition.priority,
        pinned=definition.pinned,
        group=definition.group,
        error=error,
        details=details,
    )


def _default_config_path() -> Path | None:
    explicit = os.environ.get("HOMELAB_SERVICES_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    cwd_path = Path.cwd() / "services.yaml"
    if cwd_path.exists():
        return cwd_path
    user_path = Path.home() / ".config" / "homelab-console" / "services.yaml"
    if user_path.exists():
        return user_path
    return cwd_path


def _split_host_port(value: str) -> tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise ValueError("TCP target must use host:port")
    return host, int(port_text)
