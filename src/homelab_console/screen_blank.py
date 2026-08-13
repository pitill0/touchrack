from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScreenBlankOption:
    minutes: int
    label: str


SCREEN_BLANK_OPTIONS = (
    ScreenBlankOption(1, "1m"),
    ScreenBlankOption(5, "5m"),
    ScreenBlankOption(15, "15m"),
    ScreenBlankOption(0, "ON"),
)


def normalize_screen_blank_minutes(value: int) -> int:
    values = tuple(option.minutes for option in SCREEN_BLANK_OPTIONS)
    return min(values, key=lambda option: abs(option - value))


def screen_blank_label(minutes: int, *, compact: bool = True) -> str:
    option = next(
        (item for item in SCREEN_BLANK_OPTIONS if item.minutes == minutes),
        SCREEN_BLANK_OPTIONS[0],
    )
    return f"SCREEN {option.label}" if not compact else option.label


def _run_setterm_blank(value: str, *, tty_path: str = "/dev/tty1") -> None:
    """Run a setterm blanking command against a real virtual console."""

    setterm = shutil.which("setterm")
    if setterm is None:
        raise RuntimeError("setterm is not installed")
    if not os.path.exists(tty_path):
        raise RuntimeError(f"console {tty_path} does not exist")

    with open(tty_path, "r+b", buffering=0) as tty:
        subprocess.run(
            [setterm, "--blank", value],
            stdin=tty,
            stdout=tty,
            stderr=subprocess.PIPE,
            check=True,
            timeout=3,
        )


def apply_screen_blank(minutes: int, *, tty_path: str = "/dev/tty1") -> None:
    """Set the Linux virtual-console blanking timeout."""
    _run_setterm_blank(str(minutes), tty_path=tty_path)


def force_screen_blank(*, tty_path: str = "/dev/tty1") -> None:
    """Blank a Linux virtual console immediately."""
    _run_setterm_blank("force", tty_path=tty_path)


def wake_screen(*, tty_path: str = "/dev/tty1") -> None:
    """Wake a blanked Linux virtual console without changing its timeout."""
    _run_setterm_blank("poke", tty_path=tty_path)
