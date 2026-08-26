from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from homelab_console.nodes.config import (
    ConfiguredNode,
    LocalTransportConfig,
    RemoteContainersConfig,
    RemoteDiskHealthConfig,
    SshTransportConfig,
)
from homelab_console.nodes.models import Node


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
    # None preserves the v4.7 implicit local-node behaviour.
    nodes: tuple[ConfiguredNode, ...] | None = None
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
    nodes = _parse_nodes(payload)

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
        nodes=nodes,
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


def _parse_nodes(
    payload: dict[object, object],
) -> tuple[ConfiguredNode, ...] | None:
    if "nodes" not in payload:
        return None

    raw_nodes = payload["nodes"]
    if not isinstance(raw_nodes, list):
        raise ValueError("nodes must be a list")
    if not raw_nodes:
        raise ValueError("nodes must contain at least one node")

    configured: list[ConfiguredNode] = []
    seen_ids: set[str] = set()
    local_count = 0

    for index, raw_node in enumerate(raw_nodes):
        location = f"nodes[{index}]"
        node_mapping = _require_mapping(raw_node, location)
        _reject_unknown_keys(
            node_mapping,
            {"id", "title", "transport", "disk_health", "containers"},
            location,
        )

        node_id = _required_string(node_mapping, "id", location)
        title = _required_string(node_mapping, "title", location)
        if node_id in seen_ids:
            raise ValueError(f"duplicate node id: {node_id}")
        seen_ids.add(node_id)

        transport_mapping = _require_mapping(
            node_mapping.get("transport"),
            f"{location}.transport",
        )
        transport_type = _required_string(
            transport_mapping,
            "type",
            f"{location}.transport",
        )

        if transport_type == "local":
            _reject_unknown_keys(
                transport_mapping,
                {"type"},
                f"{location}.transport",
            )
            transport = LocalTransportConfig()
            local_count += 1
            if local_count > 1:
                raise ValueError("nodes may contain at most one local transport")
        elif transport_type == "ssh":
            transport = _parse_ssh_transport(transport_mapping, location)
        else:
            raise ValueError(
                f"{location}.transport.type must be one of: local, ssh"
            )

        disk_health = _parse_remote_disk_health(node_mapping, location)
        if disk_health is not None and transport.type != "ssh":
            raise ValueError(f"{location}.disk_health requires SSH transport")
        containers = _parse_remote_containers(node_mapping, location)
        if containers is not None and transport.type != "ssh":
            raise ValueError(f"{location}.containers requires SSH transport")

        configured.append(
            ConfiguredNode(
                node=Node(id=node_id, title=title, transport=transport.type),
                transport=transport,
                disk_health=disk_health,
                containers=containers,
            )
        )

    return tuple(configured)


def _parse_remote_disk_health(
    node: dict[object, object],
    node_location: str,
) -> RemoteDiskHealthConfig | None:
    if "disk_health" not in node:
        return None
    location = f"{node_location}.disk_health"
    section = _require_mapping(node["disk_health"], location)
    _reject_unknown_keys(section, {"devices", "helper"}, location)
    devices = _parse_string_list(
        section,
        "devices",
        require_absolute=True,
        section_name=location,
    )
    if not devices:
        raise ValueError(f"{location}.devices must contain at least one device")
    helper_value = section.get("helper", "/usr/local/libexec/touchrack-smartctl")
    if not isinstance(helper_value, str) or not helper_value.strip():
        raise ValueError(f"{location}.helper must be a non-empty string")
    helper = _absolute_path(helper_value.strip(), f"{location}.helper")
    return RemoteDiskHealthConfig(devices=devices, helper=helper)


def _parse_remote_containers(
    node: dict[object, object],
    node_location: str,
) -> RemoteContainersConfig | None:
    if "containers" not in node:
        return None
    location = f"{node_location}.containers"
    section = _require_mapping(node["containers"], location)
    _reject_unknown_keys(section, {"helper"}, location)
    helper_value = section.get(
        "helper", "/usr/local/libexec/touchrack-container-inventory"
    )
    if not isinstance(helper_value, str) or not helper_value.strip():
        raise ValueError(f"{location}.helper must be a non-empty string")
    helper = _absolute_path(helper_value.strip(), f"{location}.helper")
    return RemoteContainersConfig(helper=helper)


def _parse_ssh_transport(
    transport: dict[object, object],
    node_location: str,
) -> SshTransportConfig:
    location = f"{node_location}.transport"
    _reject_unknown_keys(
        transport,
        {
            "type",
            "host",
            "user",
            "port",
            "identity_file",
            "known_hosts_file",
            "connect_timeout",
            "command_timeout",
        },
        location,
    )

    host = _required_string(transport, "host", location)
    user = _required_string(transport, "user", location)
    if host.startswith("-") or any(character.isspace() for character in host):
        raise ValueError(f"{location}.host is not a safe SSH host")
    if (
        user.startswith("-")
        or "@" in user
        or any(character.isspace() for character in user)
    ):
        raise ValueError(f"{location}.user is not a safe SSH user")

    port = _bounded_integer(
        transport.get("port", 22),
        f"{location}.port",
        minimum=1,
        maximum=65535,
    )
    connect_timeout = _bounded_number(
        transport.get("connect_timeout", 3),
        f"{location}.connect_timeout",
        minimum=0.1,
        maximum=30.0,
    )
    command_timeout = _bounded_number(
        transport.get("command_timeout", 5),
        f"{location}.command_timeout",
        minimum=0.1,
        maximum=300.0,
    )

    identity_file = _absolute_path(
        _required_string(transport, "identity_file", location),
        f"{location}.identity_file",
    )
    known_hosts_file = _absolute_path(
        _required_string(transport, "known_hosts_file", location),
        f"{location}.known_hosts_file",
    )

    return SshTransportConfig(
        host=host,
        user=user,
        port=port,
        identity_file=identity_file,
        known_hosts_file=known_hosts_file,
        connect_timeout=connect_timeout,
        command_timeout=command_timeout,
    )


def _require_mapping(value: object, location: str) -> dict[object, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{location} must be a mapping")
    return value


def _required_string(
    mapping: dict[object, object],
    key: str,
    location: str,
) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location}.{key} must be a non-empty string")
    return value.strip()


def _reject_unknown_keys(
    mapping: dict[object, object],
    allowed: set[str],
    location: str,
) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise ValueError(
            f"{location} contains unknown keys: {', '.join(unknown)}"
        )


def _absolute_path(value: str, location: str) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError(f"{location} must be an absolute path")
    return str(path)


def _bounded_integer(
    value: object,
    location: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{location} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{location} must be between {minimum} and {maximum}")
    return value


def _bounded_number(
    value: object,
    location: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{location} must be a number")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{location} must be between {minimum} and {maximum}")
    return number



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
