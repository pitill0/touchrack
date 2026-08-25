from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from homelab_console.models import HostSnapshot
from homelab_console.transports import CommandRunner


_REMOTE_HOST_SCRIPT = r"""
set -eu
export LC_ALL=C

read_cpu() {
    read -r cpu user nice system idle iowait irq softirq steal rest < /proc/stat
    total=$((user + nice + system + idle + iowait + irq + softirq + steal))
    idle_total=$((idle + iowait))
    printf '%s %s\n' "$total" "$idle_total"
}

set -- $(read_cpu)
total_before=$1
idle_before=$2
sleep 0.15
set -- $(read_cpu)
total_after=$1
idle_after=$2
cpu_percent=$(awk -v dt="$((total_after - total_before))" \
    -v di="$((idle_after - idle_before))" \
    'BEGIN { if (dt <= 0) print "0.0"; else printf "%.2f", 100 * (dt-di) / dt }')

hostname_value=$(hostname)
uptime_value=$(awk '{print $1}' /proc/uptime)
load_value=$(awk '{print $1 " " $2 " " $3}' /proc/loadavg)
memory_value=$(awk '
    /^MemTotal:/ { total=$2 }
    /^MemAvailable:/ { available=$2 }
    /^MemFree:/ { free=$2 }
    /^Buffers:/ { buffers=$2 }
    /^Cached:/ { cached=$2 }
    END {
        if (!available) available=free+buffers+cached
        printf "%.0f %.0f", (total-available)*1024, total*1024
    }' /proc/meminfo)
disk_value=$(df -Pk / | awk 'NR==2 { printf "%.0f %.0f", $3*1024, $2*1024 }')

temperature_value=-
for temperature_path in /sys/class/thermal/thermal_zone*/temp; do
    if [ -r "$temperature_path" ]; then
        temperature_raw=$(cat "$temperature_path")
        temperature_value=$(awk -v value="$temperature_raw" \
            'BEGIN { if (value > 1000) value=value/1000; printf "%.2f", value }')
        break
    fi
done

ip_value=$(ip -o route get 192.0.2.1 2>/dev/null | awk '
    { for (i=1; i<=NF; i++) if ($i == "src") { print $(i+1); exit } }' || true)
if [ -z "$ip_value" ]; then
    ip_value=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
fi
if [ -z "$ip_value" ]; then ip_value=-; fi

printf 'touchrack_protocol=1\n'
printf 'hostname=%s\n' "$hostname_value"
printf 'uptime_seconds=%s\n' "$uptime_value"
printf 'cpu_percent=%s\n' "$cpu_percent"
printf 'load_average=%s\n' "$load_value"
printf 'memory=%s\n' "$memory_value"
printf 'disk=%s\n' "$disk_value"
printf 'temperature_c=%s\n' "$temperature_value"
printf 'ip_address=%s\n' "$ip_value"
""".strip()


class RemoteHostProvider:
    """Collect a universal host snapshot through an injected command runner."""

    def __init__(self, runner: CommandRunner, *, fallback_hostname: str) -> None:
        self.runner = runner
        self.fallback_hostname = fallback_hostname

    async def collect(self) -> HostSnapshot:
        collected_at = datetime.now(timezone.utc)
        try:
            result = await self.runner.run(("sh", "-c", _REMOTE_HOST_SCRIPT))
            if result.returncode != 0:
                detail = result.stderr.strip() or f"exit status {result.returncode}"
                raise RuntimeError(f"remote collector failed: {detail}")
            return _parse_host_snapshot(result.stdout, collected_at=collected_at)
        except Exception as exc:  # provider boundary: return state, don't crash UI
            return _error_snapshot(
                self.fallback_hostname,
                collected_at,
                f"{type(exc).__name__}: {exc}",
            )


def _parse_host_snapshot(payload: str, *, collected_at: datetime) -> HostSnapshot:
    fields: dict[str, str] = {}
    for line in payload.splitlines():
        key, separator, value = line.partition("=")
        if not separator or not key or key in fields:
            raise ValueError("invalid remote collector output")
        fields[key] = value.strip()

    expected = {
        "touchrack_protocol",
        "hostname",
        "uptime_seconds",
        "cpu_percent",
        "load_average",
        "memory",
        "disk",
        "temperature_c",
        "ip_address",
    }
    if set(fields) != expected or fields["touchrack_protocol"] != "1":
        raise ValueError("unsupported remote collector output")

    hostname = fields["hostname"]
    if not hostname:
        raise ValueError("remote hostname is empty")
    uptime = _non_negative_float(fields["uptime_seconds"], "uptime")
    cpu = _non_negative_float(fields["cpu_percent"], "CPU")
    if cpu > 100:
        raise ValueError("CPU percentage is outside its valid range")
    load = _float_tuple(fields["load_average"], 3, "load average")
    memory_used, memory_total = _integer_tuple(fields["memory"], "memory")
    disk_used, disk_total = _integer_tuple(fields["disk"], "disk")
    if memory_used > memory_total or disk_used > disk_total:
        raise ValueError("remote usage exceeds total capacity")

    temperature_text = fields["temperature_c"]
    temperature = (
        None
        if temperature_text == "-"
        else _finite_float(temperature_text, "temperature")
    )
    ip_address = fields["ip_address"]

    return HostSnapshot(
        hostname=hostname,
        uptime_seconds=uptime,
        cpu_percent=cpu,
        load_average=load,
        memory_used=memory_used,
        memory_total=memory_total,
        disk_used=disk_used,
        disk_total=disk_total,
        temperature_c=temperature,
        ip_address=None if ip_address == "-" else ip_address,
        collected_at=collected_at,
    )


def _non_negative_float(value: str, label: str) -> float:
    parsed = _finite_float(value, label)
    if parsed < 0:
        raise ValueError(f"{label} must not be negative")
    return parsed


def _float_tuple(value: str, length: int, label: str) -> tuple[float, ...]:
    parts = tuple(_finite_float(part, label) for part in value.split())
    if len(parts) != length or any(part < 0 for part in parts):
        raise ValueError(f"invalid remote {label}")
    return parts


def _integer_tuple(value: str, label: str) -> tuple[int, int]:
    parts = value.split()
    if len(parts) != 2:
        raise ValueError(f"invalid remote {label}")
    parsed = tuple(int(part) for part in parts)
    if any(part < 0 for part in parsed):
        raise ValueError(f"remote {label} must not be negative")
    return parsed


def _finite_float(value: str, label: str) -> float:
    parsed = float(value)
    if not isfinite(parsed):
        raise ValueError(f"{label} must be finite")
    return parsed


def _error_snapshot(
    hostname: str,
    collected_at: datetime,
    error: str,
) -> HostSnapshot:
    return HostSnapshot(
        hostname=hostname,
        uptime_seconds=0,
        cpu_percent=0,
        load_average=None,
        memory_used=0,
        memory_total=0,
        disk_used=0,
        disk_total=0,
        temperature_c=None,
        ip_address=None,
        collected_at=collected_at,
        error=error,
    )
