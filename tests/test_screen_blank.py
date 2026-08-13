from __future__ import annotations

from unittest.mock import mock_open, patch

from homelab_console.screen_blank import (
    force_screen_blank,
    normalize_screen_blank_minutes,
    screen_blank_label,
    wake_screen,
)


def test_normalize_screen_blank_minutes() -> None:
    assert normalize_screen_blank_minutes(1) == 1
    assert normalize_screen_blank_minutes(4) == 5
    assert normalize_screen_blank_minutes(14) == 15
    assert normalize_screen_blank_minutes(0) == 0


def test_screen_blank_labels() -> None:
    assert screen_blank_label(1, compact=True) == "1m"
    assert screen_blank_label(15, compact=False) == "SCREEN 15m"
    assert screen_blank_label(0, compact=True) == "ON"


def test_wake_screen_uses_blank_poke() -> None:
    tty = mock_open()
    with (
        patch("homelab_console.screen_blank.shutil.which", return_value="/usr/bin/setterm"),
        patch("homelab_console.screen_blank.os.path.exists", return_value=True),
        patch("builtins.open", tty),
        patch("homelab_console.screen_blank.subprocess.run") as run,
    ):
        wake_screen(tty_path="/dev/tty1")

    run.assert_called_once()
    assert run.call_args.args[0] == ["/usr/bin/setterm", "--blank", "poke"]


def test_force_screen_blank_uses_blank_force() -> None:
    tty = mock_open()
    with (
        patch("homelab_console.screen_blank.shutil.which", return_value="/usr/bin/setterm"),
        patch("homelab_console.screen_blank.os.path.exists", return_value=True),
        patch("builtins.open", tty),
        patch("homelab_console.screen_blank.subprocess.run") as run,
    ):
        force_screen_blank(tty_path="/dev/tty1")

    run.assert_called_once()
    assert run.call_args.args[0] == ["/usr/bin/setterm", "--blank", "force"]
