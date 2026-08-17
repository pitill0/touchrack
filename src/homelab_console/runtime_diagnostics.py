from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DiagnosticRow = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class TouchDiagnostic:
    state: str
    detail: str
    style: str


@dataclass(frozen=True, slots=True)
class RuntimeDiagnostics:
    version: str
    screen_tty: str
    width: int
    height: int
    touch: TouchDiagnostic
    container_engine: str | None
    container_total: int | None
    containers_error: str | None
    services_count: int | None
    services_error: str | None
    config_source_path: str | None
    sleep_label: str
    screen_blank_error: str | None


def build_diagnostics_rows(
    facts: RuntimeDiagnostics,
) -> tuple[DiagnosticRow, ...]:
    """Format already-collected runtime facts for the diagnostics screen."""
    if facts.container_total is None:
        engine_value, engine_class = "WAITING", "muted"
    elif facts.containers_error:
        engine = facts.container_engine or "unknown"
        engine_value, engine_class = f"{engine} · ERROR", "error"
    else:
        engine = facts.container_engine or "unknown"
        engine_value = f"{engine} · {facts.container_total} containers"
        engine_class = "ok"

    if facts.services_count is None:
        services_value, services_class = "WAITING", "muted"
    elif facts.services_error:
        services_value, services_class = "ERROR", "error"
    else:
        services_value = f"{facts.services_count} visible"
        services_class = "ok" if facts.services_count else "warn"

    config_path = (
        Path(facts.config_source_path).expanduser()
        if facts.config_source_path
        else None
    )
    if config_path is not None and config_path.exists():
        config_value, config_class = config_path.name, "ok"
    else:
        config_value, config_class = "defaults", "muted"

    blank_value = (
        f"{facts.sleep_label} · "
        f"{'OK' if not facts.screen_blank_error else 'ERROR'}"
    )
    blank_class = "ok" if not facts.screen_blank_error else "error"

    return (
        ("VERSION", facts.version, "ok"),
        (
            "DISPLAY",
            f"{Path(facts.screen_tty).name} · "
            f"{facts.width}×{facts.height}",
            "ok",
        ),
        (
            "TOUCH",
            f"{facts.touch.state} · {facts.touch.detail}",
            facts.touch.style,
        ),
        ("ENGINE", engine_value, engine_class),
        ("SERVICES", services_value, services_class),
        ("CONFIG", config_value, config_class),
        ("SLEEP", blank_value, blank_class),
    )
