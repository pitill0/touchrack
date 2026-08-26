from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from homelab_console.models import ContainersSnapshot
from homelab_console.providers.containers import (
    decode_json_lines,
    decode_json_list,
    normalize_docker_rows,
    normalize_podman_rows,
)
from homelab_console.transports import CommandRunner


_REMOTE_CONTAINERS_SCRIPT = r"""
set -eu
export LC_ALL=C

if command -v podman >/dev/null 2>&1; then
    engine=Podman
elif command -v docker >/dev/null 2>&1; then
    engine=Docker
else
    printf '%s\n' 'Neither Podman nor Docker is installed or available in PATH' >&2
    exit 127
fi

printf 'touchrack_containers_protocol=1\n'
printf 'engine=%s\n' "$engine"
printf 'ps_begin\n'
if [ "$engine" = Podman ]; then
    podman ps --all --format json
else
    docker ps --all --no-trunc --format '{{json .}}'
fi
printf 'ps_end\n'

set +e
if [ "$engine" = Podman ]; then
    stats_output=$(podman stats --all --no-stream --format json 2>&1)
    stats_status=$?
else
    stats_output=$(docker stats --all --no-stream --format '{{json .}}' 2>&1)
    stats_status=$?
fi
set -e

printf 'stats_status=%s\n' "$stats_status"
printf 'stats_begin\n'
printf '%s\n' "$stats_output"
printf 'stats_end\n'
""".strip()


class RemoteContainersProvider:
    """Collect Podman or Docker state through an injected remote runner."""

    def __init__(self, runner: CommandRunner, *, helper: str | None = None) -> None:
        self.runner = runner
        self.helper = helper

    async def collect(self) -> ContainersSnapshot:
        collected_at = datetime.now(timezone.utc)
        try:
            command = (
                ("sudo", "-n", self.helper)
                if self.helper is not None
                else ("sh", "-c", _REMOTE_CONTAINERS_SCRIPT)
            )
            result = await self.runner.run(command)
            if result.returncode != 0:
                detail = result.stderr.strip() or f"exit status {result.returncode}"
                raise RuntimeError(f"remote containers collector failed: {detail}")
            engine, ps_output, stats_status, stats_output = _parse_frames(
                result.stdout
            )
            ps_rows = _decode_rows(engine, ps_output)

            stats_error: str | None = None
            stats_rows: list[dict[str, Any]] = []
            if stats_status == 0:
                try:
                    stats_rows = _decode_rows(engine, stats_output)
                except json.JSONDecodeError as exc:
                    stats_error = f"Resource statistics unavailable: {exc}"
            else:
                detail = stats_output.strip() or f"exit status {stats_status}"
                stats_error = f"Resource statistics unavailable: {detail}"

            containers = (
                normalize_podman_rows(ps_rows, stats_rows)
                if engine == "Podman"
                else normalize_docker_rows(ps_rows, stats_rows)
            )
            return ContainersSnapshot(
                containers=containers,
                collected_at=collected_at,
                engine=engine,
                stats_error=stats_error,
            )
        except Exception as exc:  # provider boundary: return state, don't crash UI
            return ContainersSnapshot(
                containers=(),
                collected_at=collected_at,
                engine="Remote",
                error=f"{type(exc).__name__}: {exc}",
            )


def _parse_frames(payload: str) -> tuple[str, str, int, str]:
    lines = payload.splitlines()
    if len(lines) < 7 or lines[0] != "touchrack_containers_protocol=1":
        raise ValueError("unsupported remote containers collector output")
    if not lines[1].startswith("engine=") or lines[2] != "ps_begin":
        raise ValueError("invalid remote containers collector output")

    engine = lines[1].removeprefix("engine=")
    if engine not in {"Podman", "Docker"}:
        raise ValueError(f"unsupported remote container engine: {engine}")

    try:
        ps_end = lines.index("ps_end", 3)
    except ValueError as exc:
        raise ValueError("invalid remote containers collector output") from exc
    if ps_end + 2 >= len(lines):
        raise ValueError("invalid remote containers collector output")

    status_line = lines[ps_end + 1]
    if (
        not status_line.startswith("stats_status=")
        or lines[ps_end + 2] != "stats_begin"
    ):
        raise ValueError("invalid remote containers collector output")
    try:
        stats_status = int(status_line.removeprefix("stats_status="))
    except ValueError as exc:
        raise ValueError("invalid remote containers stats status") from exc
    if stats_status < 0 or lines[-1] != "stats_end":
        raise ValueError("invalid remote containers collector output")

    ps_output = "\n".join(lines[3:ps_end]).strip()
    stats_output = "\n".join(lines[ps_end + 3 : -1]).strip()
    return engine, ps_output, stats_status, stats_output


def _decode_rows(engine: str, output: str) -> list[dict[str, Any]]:
    return (
        decode_json_list(output)
        if engine == "Podman"
        else decode_json_lines(output)
    )
