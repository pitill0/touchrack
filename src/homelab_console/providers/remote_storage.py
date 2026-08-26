from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite

from homelab_console.models import FilesystemInfo, StorageSnapshot
from homelab_console.transports import CommandRunner


_REMOTE_STORAGE_SCRIPT = r"""
set -eu
export LC_ALL=C

printf 'touchrack_storage_protocol=1\n'
awk '
    BEGIN { OFS="\t" }
    $3 !~ /^(autofs|binfmt_misc|cgroup|cgroup2|configfs|debugfs|devpts|devtmpfs|efivarfs|fusectl|hugetlbfs|mqueue|nsfs|overlay|proc|pstore|ramfs|rpc_pipefs|securityfs|squashfs|sysfs|tmpfs|tracefs)$/ {
        print $1, $2, $3, $4
    }
' /proc/self/mounts |
while IFS="	" read -r device_encoded mount_encoded filesystem options; do
    mountpoint=$(printf '%b' "$mount_encoded")
    usage=$(df -Pk "$mountpoint" 2>/dev/null | awk '
        NR == 2 { printf "%.0f\t%.0f\t%.0f\t%.2f", $2*1024, $3*1024, $4*1024, $5+0 }
    ')
    if [ -n "$usage" ]; then
        printf 'filesystem\t%s\t%s\t%s\t%s\t%s\n' \
            "$device_encoded" "$mount_encoded" "$filesystem" "$options" "$usage"
    fi
done
""".strip()


class RemoteStorageProvider:
    """Collect mounted filesystem usage through an injected remote runner."""

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner

    async def collect(self) -> StorageSnapshot:
        collected_at = datetime.now(timezone.utc)
        try:
            result = await self.runner.run(("sh", "-c", _REMOTE_STORAGE_SCRIPT))
            if result.returncode != 0:
                detail = result.stderr.strip() or f"exit status {result.returncode}"
                raise RuntimeError(f"remote storage collector failed: {detail}")
            filesystems = _parse_storage_snapshot(result.stdout)
            return StorageSnapshot(
                filesystems=filesystems,
                collected_at=collected_at,
            )
        except Exception as exc:  # provider boundary: return state, don't crash UI
            return StorageSnapshot(
                filesystems=(),
                collected_at=collected_at,
                error=f"{type(exc).__name__}: {exc}",
            )


def _parse_storage_snapshot(payload: str) -> tuple[FilesystemInfo, ...]:
    lines = payload.splitlines()
    if not lines or lines[0] != "touchrack_storage_protocol=1":
        raise ValueError("unsupported remote storage collector output")

    by_mountpoint: dict[str, FilesystemInfo] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 9 or fields[0] != "filesystem":
            raise ValueError("invalid remote storage collector output")

        _, device, mountpoint, filesystem, options, total, used, free, percent = fields
        mountpoint = _decode_proc_field(mountpoint)
        if not mountpoint:
            raise ValueError("remote storage mountpoint is empty")

        total_value = _non_negative_integer(total, "total")
        used_value = _non_negative_integer(used, "used")
        free_value = _non_negative_integer(free, "free")
        percent_value = float(percent)
        if not isfinite(percent_value) or percent_value < 0:
            raise ValueError("remote storage percentage is invalid")
        if used_value > total_value or free_value > total_value:
            raise ValueError("remote storage usage exceeds total capacity")

        by_mountpoint.setdefault(
            mountpoint,
            FilesystemInfo(
                device=_decode_proc_field(device),
                mountpoint=mountpoint,
                filesystem=filesystem,
                mount_options=options,
                total=total_value,
                used=used_value,
                free=free_value,
                usage_percent=percent_value,
            ),
        )

    return tuple(
        sorted(
            by_mountpoint.values(),
            key=lambda item: (
                0 if item.mountpoint == "/" else 1,
                item.mountpoint.casefold(),
            ),
        )
    )


def _decode_proc_field(value: str) -> str:
    decoded = value
    for encoded, character in (
        (r"\040", " "),
        (r"\011", "\t"),
        (r"\012", "\n"),
        (r"\134", "\\"),
    ):
        decoded = decoded.replace(encoded, character)
    if "\x00" in decoded or "\n" in decoded or "\t" in decoded:
        raise ValueError("remote storage field contains control characters")
    return decoded


def _non_negative_integer(value: str, label: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise ValueError(f"remote storage {label} must not be negative")
    return parsed
