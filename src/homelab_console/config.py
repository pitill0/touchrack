from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True, slots=True)
class DisplayConfig:
    tty: str = "/dev/tty1"
    sleep_minutes: int = 1


@dataclass(frozen=True, slots=True)
class RefreshConfig:
    seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class TouchConfig:
    enabled: bool = True
    device: str = "auto"


@dataclass(frozen=True, slots=True)
class StorageConfig:
    # None means autodetect. An explicit empty tuple means show none.
    mounts: tuple[str, ...] | None = None
    disks: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    display: DisplayConfig = DisplayConfig()
    refresh: RefreshConfig = RefreshConfig()
    touch: TouchConfig = TouchConfig()
    storage: StorageConfig = StorageConfig()
    services_file: str | None = None
    source_path: str | None = None


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load optional YAML configuration while preserving safe defaults."""
    config_path = Path(path).expanduser() if path else _default_config_path()
    if config_path is None or not config_path.exists():
        return AppConfig(source_path=str(config_path) if config_path else None)

    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError("config root must be a mapping")

    display = payload.get("display") or {}
    refresh = payload.get("refresh") or {}
    touch = payload.get("touch") or {}
    storage = payload.get("storage") or {}
    services = payload.get("services") or {}
    if not all(
        isinstance(section, dict)
        for section in (display, refresh, touch, storage, services)
    ):
        raise ValueError(
            "display, refresh, touch, storage and services must be mappings"
        )

    tty = str(display.get("tty", "/dev/tty1")).strip() or "/dev/tty1"
    sleep_minutes = _parse_sleep_minutes(display.get("sleep", "1m"))
    refresh_seconds = float(refresh.get("seconds", 5))
    enabled = _parse_bool(touch.get("enabled", True))
    device = str(touch.get("device", "auto")).strip() or "auto"
    storage_mounts = _parse_string_list(
        storage,
        "mounts",
        require_absolute=True,
        section_name="storage",
    )
    storage_disks = _parse_string_list(
        storage,
        "disks",
        require_absolute=True,
        section_name="storage",
    )

    services_file = services.get("file")
    if services_file is not None:
        services_file = str(services_file).strip() or None
        if services_file and not Path(services_file).expanduser().is_absolute():
            services_file = str((config_path.parent / services_file).resolve())

    return AppConfig(
        display=DisplayConfig(tty=tty, sleep_minutes=sleep_minutes),
        refresh=RefreshConfig(seconds=refresh_seconds),
        touch=TouchConfig(enabled=enabled, device=device),
        storage=StorageConfig(
            mounts=storage_mounts,
            disks=storage_disks,
        ),
        services_file=services_file,
        source_path=str(config_path),
    )


def _default_config_path() -> Path:
    explicit = os.environ.get("HOMELAB_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    cwd_path = Path.cwd() / "config.yaml"
    if cwd_path.exists():
        return cwd_path
    user_path = Path.home() / ".config" / "homelab-console" / "config.yaml"
    if user_path.exists():
        return user_path
    return cwd_path


def _parse_sleep_minutes(value: object) -> int:
    if isinstance(value, (int, float)):
        minutes = int(value)
    else:
        text = str(value).strip().lower()
        if text in {"on", "off", "never", "0"}:
            return 0
        if text.endswith("m"):
            text = text[:-1]
        minutes = int(text)
    if minutes not in {0, 1, 5, 15}:
        raise ValueError("display.sleep must be one of: 1m, 5m, 15m, on")
    return minutes


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("touch.enabled must be a boolean")



def _parse_string_list(
    section: dict[object, object],
    key: str,
    *,
    require_absolute: bool,
    section_name: str,
) -> tuple[str, ...] | None:
    """Parse an optional ordered list while preserving absent vs explicit empty."""
    if key not in section:
        return None

    raw = section[key]
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"{section_name}.{key} must be a list")

    values: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"{section_name}.{key} entries must be strings")
        value = item.strip()
        if not value:
            raise ValueError(f"{section_name}.{key} entries must not be empty")
        if require_absolute and not Path(value).expanduser().is_absolute():
            raise ValueError(f"{section_name}.{key} entries must be absolute paths")
        if value not in seen:
            seen.add(value)
            values.append(value)
    return tuple(values)
