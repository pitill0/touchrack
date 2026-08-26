from __future__ import annotations

from homelab_console.providers import RemoteStorageProvider
from homelab_console.transports import CommandResult, SshConnectionError


VALID_PAYLOAD = """\
touchrack_storage_protocol=1
filesystem\t/dev/mmcblk0p2\t/\text4\trw,relatime\t31914983424\t7340032000\t22900899840\t24.00
filesystem\t/dev/sda1\t/mnt/backup\\040disk\text4\trw,nosuid\t107374182400\t53687091200\t53687091200\t50.00
"""


class FakeRunner:
    def __init__(
        self,
        *,
        stdout: str = VALID_PAYLOAD,
        stderr: str = "",
        returncode: int = 0,
        error: Exception | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.error = error
        self.commands: list[tuple[str, ...]] = []

    async def run(self, command: tuple[str, ...], *, timeout=None):
        self.commands.append(command)
        if self.error is not None:
            raise self.error
        return CommandResult(
            command=command,
            stdout=self.stdout,
            stderr=self.stderr,
            returncode=self.returncode,
        )


async def test_remote_storage_collects_and_normalizes_filesystems() -> None:
    runner = FakeRunner()

    snapshot = await RemoteStorageProvider(runner).collect()

    assert snapshot.error is None
    assert [item.mountpoint for item in snapshot.filesystems] == [
        "/",
        "/mnt/backup disk",
    ]
    root = snapshot.filesystems[0]
    assert root.device == "/dev/mmcblk0p2"
    assert root.filesystem == "ext4"
    assert root.mount_options == "rw,relatime"
    assert root.total == 31914983424
    assert root.used == 7340032000
    assert root.free == 22900899840
    assert root.usage_percent == 24.0
    assert runner.commands[0][:2] == ("sh", "-c")
    assert "/proc/self/mounts" in runner.commands[0][2]
    assert "df -Pk" in runner.commands[0][2]


async def test_remote_storage_deduplicates_mounts_and_keeps_root_first() -> None:
    duplicate = VALID_PAYLOAD + VALID_PAYLOAD.splitlines()[1] + "\n"

    snapshot = await RemoteStorageProvider(FakeRunner(stdout=duplicate)).collect()

    assert [item.mountpoint for item in snapshot.filesystems] == [
        "/",
        "/mnt/backup disk",
    ]


async def test_remote_storage_converts_transport_failure_to_snapshot() -> None:
    snapshot = await RemoteStorageProvider(
        FakeRunner(error=SshConnectionError("connection refused"))
    ).collect()

    assert snapshot.filesystems == ()
    assert snapshot.error == "SshConnectionError: connection refused"


async def test_remote_storage_rejects_command_and_protocol_failures() -> None:
    failed = await RemoteStorageProvider(
        FakeRunner(stderr="df: not found", returncode=127)
    ).collect()
    malformed = await RemoteStorageProvider(
        FakeRunner(stdout=VALID_PAYLOAD.replace("\t24.00", "\tbad"))
    ).collect()

    assert failed.error == (
        "RuntimeError: remote storage collector failed: df: not found"
    )
    assert malformed.error == "ValueError: could not convert string to float: 'bad'"


async def test_remote_storage_rejects_unsafe_or_impossible_values() -> None:
    control_character = VALID_PAYLOAD.replace(
        "/mnt/backup\\040disk",
        r"/mnt/backup\011disk",
    )
    impossible = VALID_PAYLOAD.replace(
        "7340032000\t22900899840\t24.00",
        "41914983424\t22900899840\t24.00",
    )

    control_snapshot = await RemoteStorageProvider(
        FakeRunner(stdout=control_character)
    ).collect()
    impossible_snapshot = await RemoteStorageProvider(
        FakeRunner(stdout=impossible)
    ).collect()

    assert "control characters" in (control_snapshot.error or "")
    assert "exceeds total capacity" in (impossible_snapshot.error or "")
