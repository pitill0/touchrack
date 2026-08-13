from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from typing import Any

from homelab_console.models import ContainerInfo, ContainersSnapshot


class CliContainersProvider:
    """Read-only container provider backed by a local CLI."""

    engine = "containers"

    def __init__(self, command: str, timeout_seconds: float = 5.0) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds

    async def collect(self) -> ContainersSnapshot:
        raise NotImplementedError

    async def _run(self, *args: str) -> str:
        process = await asyncio.create_subprocess_exec(
            self.command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise TimeoutError(f"command exceeded {self.timeout_seconds:.0f}s") from exc

        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip() or f"unknown {self.engine} error"
            raise RuntimeError(message)
        return stdout.decode(errors="replace").strip()


class PodmanContainersProvider(CliContainersProvider):
    """Read-only local Podman provider using JSON CLI output."""

    engine = "Podman"

    def __init__(self, command: str = "podman", timeout_seconds: float = 5.0) -> None:
        super().__init__(command, timeout_seconds)

    async def collect(self) -> ContainersSnapshot:
        collected_at = datetime.now(timezone.utc)
        try:
            ps_rows = await self._run_json_list("ps", "--all", "--format", "json")
        except FileNotFoundError:
            return _unavailable(collected_at, self.engine, "Podman is not installed or is not available in PATH")
        except (TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            return _unavailable(collected_at, self.engine, f"Unable to read Podman containers: {exc}")

        stats_by_id: dict[str, dict[str, Any]] = {}
        stats_error: str | None = None
        try:
            stats_rows = await self._run_json_list(
                "stats", "--all", "--no-stream", "--format", "json"
            )
            stats_by_id = _index_stats(stats_rows)
        except (TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            stats_error = f"Resource statistics unavailable: {exc}"

        containers = tuple(
            _build_podman_container(row, stats_by_id)
            for row in sorted(ps_rows, key=_podman_sort_key)
        )
        return ContainersSnapshot(
            containers=containers,
            collected_at=collected_at,
            engine=self.engine,
            stats_error=stats_error,
        )

    async def _run_json_list(self, *args: str) -> list[dict[str, Any]]:
        decoded = await self._run(*args)
        if not decoded:
            return []
        payload = json.loads(decoded)
        if not isinstance(payload, list):
            raise json.JSONDecodeError("expected a JSON list", decoded, 0)
        return [row for row in payload if isinstance(row, dict)]


class DockerContainersProvider(CliContainersProvider):
    """Read-only local Docker provider using one JSON object per output line."""

    engine = "Docker"

    def __init__(self, command: str = "docker", timeout_seconds: float = 5.0) -> None:
        super().__init__(command, timeout_seconds)

    async def collect(self) -> ContainersSnapshot:
        collected_at = datetime.now(timezone.utc)
        try:
            ps_rows = await self._run_json_lines(
                "ps", "--all", "--no-trunc", "--format", "{{json .}}"
            )
        except FileNotFoundError:
            return _unavailable(collected_at, self.engine, "Docker is not installed or is not available in PATH")
        except (TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            return _unavailable(collected_at, self.engine, f"Unable to read Docker containers: {exc}")

        stats_by_id: dict[str, dict[str, Any]] = {}
        stats_error: str | None = None
        try:
            stats_rows = await self._run_json_lines(
                "stats", "--all", "--no-stream", "--format", "{{json .}}"
            )
            stats_by_id = _index_stats(stats_rows)
        except (TimeoutError, RuntimeError, json.JSONDecodeError) as exc:
            stats_error = f"Resource statistics unavailable: {exc}"

        containers = tuple(
            _build_docker_container(row, stats_by_id)
            for row in sorted(ps_rows, key=_docker_sort_key)
        )
        return ContainersSnapshot(
            containers=containers,
            collected_at=collected_at,
            engine=self.engine,
            stats_error=stats_error,
        )

    async def _run_json_lines(self, *args: str) -> list[dict[str, Any]]:
        decoded = await self._run(*args)
        if not decoded:
            return []
        rows: list[dict[str, Any]] = []
        for line in decoded.splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                rows.append(payload)
        return rows


class AutoContainersProvider:
    """Select Podman or Docker according to what is installed locally.

    Podman is preferred when both CLIs are present, matching the project's
    current environment. A provider may still report a daemon/socket error;
    that is surfaced to the UI rather than silently switching engines.
    """

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = timeout_seconds
        self.provider = self._detect()

    def _detect(self) -> CliContainersProvider | None:
        if shutil.which("podman"):
            return PodmanContainersProvider(timeout_seconds=self.timeout_seconds)
        if shutil.which("docker"):
            return DockerContainersProvider(timeout_seconds=self.timeout_seconds)
        return None

    async def collect(self) -> ContainersSnapshot:
        if self.provider is None:
            return ContainersSnapshot(
                containers=(),
                collected_at=datetime.now(timezone.utc),
                engine="None",
                error="Neither Podman nor Docker is installed or available in PATH",
            )
        return await self.provider.collect()


def _unavailable(collected_at: datetime, engine: str, message: str) -> ContainersSnapshot:
    return ContainersSnapshot(
        containers=(), collected_at=collected_at, engine=engine, error=message
    )


def _index_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        identifier = str(
            row.get("id")
            or row.get("ID")
            or row.get("Container")
            or row.get("container_id")
            or ""
        )
        if identifier:
            indexed[identifier] = row
    return indexed


def _matching_stats(identifier: str, stats_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return next(
        (
            value
            for stats_id, value in stats_by_id.items()
            if identifier.startswith(stats_id) or stats_id.startswith(identifier)
        ),
        {},
    )


def _podman_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    state = str(row.get("State") or row.get("state") or "").lower()
    names = row.get("Names") or row.get("names") or [""]
    name = names[0] if isinstance(names, list) and names else str(names)
    return (0 if state == "running" else 1, name.lower())


def _docker_sort_key(row: dict[str, Any]) -> tuple[int, str]:
    state = str(row.get("State") or "").lower()
    return (0 if state == "running" else 1, str(row.get("Names") or "").lower())


def _build_podman_container(
    row: dict[str, Any], stats_by_id: dict[str, dict[str, Any]]
) -> ContainerInfo:
    identifier = str(row.get("Id") or row.get("ID") or row.get("id") or "")
    names = row.get("Names") or row.get("names") or []
    if isinstance(names, list):
        name = str(names[0]) if names else identifier[:12]
    else:
        name = str(names) or identifier[:12]
    stats = _matching_stats(identifier, stats_by_id)
    return ContainerInfo(
        container_id=identifier,
        name=name,
        image=str(row.get("Image") or row.get("image") or "Unknown image"),
        state=str(row.get("State") or row.get("state") or "unknown"),
        status=str(row.get("Status") or row.get("status") or "Unknown"),
        cpu_percent=_clean_stat(stats.get("cpu_percent")),
        memory_usage=_clean_stat(stats.get("mem_usage")),
        memory_percent=_clean_stat(stats.get("mem_percent")),
    )


def _build_docker_container(
    row: dict[str, Any], stats_by_id: dict[str, dict[str, Any]]
) -> ContainerInfo:
    identifier = str(row.get("ID") or "")
    stats = _matching_stats(identifier, stats_by_id)
    return ContainerInfo(
        container_id=identifier,
        name=str(row.get("Names") or identifier[:12]),
        image=str(row.get("Image") or "Unknown image"),
        state=str(row.get("State") or "unknown"),
        status=str(row.get("Status") or "Unknown"),
        cpu_percent=_clean_stat(stats.get("CPUPerc")),
        memory_usage=_clean_stat(stats.get("MemUsage")),
        memory_percent=_clean_stat(stats.get("MemPerc")),
    )


def _clean_stat(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text == "--":
        return None
    return text
