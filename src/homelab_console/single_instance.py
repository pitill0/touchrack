from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path


DEFAULT_LOCK_PATH = Path("/run/lock/homelab-touch-console.lock")
FALLBACK_LOCK_PATH = Path("/tmp/homelab-touch-console.lock")


class AlreadyRunningError(RuntimeError):
    """Raised when another homelab-console process owns the instance lock."""

    def __init__(self, path: Path, owner_pid: int | None = None) -> None:
        self.path = path
        self.owner_pid = owner_pid
        detail = f" (PID {owner_pid})" if owner_pid is not None else ""
        super().__init__(f"homelab-console is already running{detail}")


class SingleInstanceLock:
    """Advisory process lock kept for the lifetime of the application.

    The lock file may remain on disk after exit; ownership is determined by
    ``flock``, not by the file's existence. Keeping the descriptor open is what
    holds the lock.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        configured = os.environ.get("HOMELAB_LOCK_FILE")
        self.path = Path(path or configured or DEFAULT_LOCK_PATH)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return

        try:
            self._fd = self._open_lock(self.path)
        except PermissionError:
            if self.path != DEFAULT_LOCK_PATH:
                raise
            self.path = FALLBACK_LOCK_PATH
            self._fd = self._open_lock(self.path)

        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                self.release()
                raise
            owner_pid = self._read_owner_pid()
            self.release()
            raise AlreadyRunningError(self.path, owner_pid) from None

        os.ftruncate(self._fd, 0)
        os.write(self._fd, f"{os.getpid()}\n".encode("ascii"))
        os.fsync(self._fd)

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> SingleInstanceLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    @staticmethod
    def _open_lock(path: Path) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(path, flags, 0o644)

    def _read_owner_pid(self) -> int | None:
        if self._fd is None:
            return None
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            raw = os.read(self._fd, 64).decode("ascii", errors="ignore").strip()
            return int(raw) if raw.isdigit() else None
        except (OSError, ValueError):
            return None
