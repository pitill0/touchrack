from __future__ import annotations

from unittest.mock import patch

from homelab_console.app import HomelabConsole


class RefreshCountingApp(HomelabConsole):
    def __init__(self) -> None:
        super().__init__(
            refresh_seconds=30,
            screen_blank_minutes=1,
            touch_enabled=False,
        )
        self.refresh_calls: list[str] = []

    def refresh_host(self) -> None:
        self.refresh_calls.append("host")

    def refresh_containers(self) -> None:
        self.refresh_calls.append("containers")

    def refresh_storage(self) -> None:
        self.refresh_calls.append("storage")


def test_scheduler_does_not_refresh_or_advance_while_display_is_blanked() -> None:
    app = RefreshCountingApp()
    app._display_blanked = True
    app._last_scheduled_refresh = 10.0

    with patch("homelab_console.app.monotonic", return_value=100.0):
        app._refresh_scheduler_tick()

    assert app.refresh_calls == []
    assert app._last_scheduled_refresh == 10.0


def test_wake_runs_one_refresh_batch_and_does_not_replay_missed_cycles() -> None:
    app = RefreshCountingApp()
    app._display_blanked = True
    app._last_scheduled_refresh = 10.0

    def run_on_app_thread(callback) -> None:
        callback()

    app.call_from_thread = run_on_app_thread  # type: ignore[method-assign]

    with (
        patch("homelab_console.app.monotonic", return_value=100.0),
        patch("homelab_console.app.wake_screen") as wake,
    ):
        app._wake_display_from_thread()
        app._refresh_scheduler_tick()

    wake.assert_called_once_with(tty_path="/dev/tty1")
    assert app._display_blanked is False
    assert app._discard_next_tap is True
    assert app._last_scheduled_refresh == 100.0
    assert app.refresh_calls == ["host", "containers", "storage"]


def test_visible_scheduler_still_refreshes_at_configured_interval() -> None:
    app = RefreshCountingApp()
    app._display_blanked = False
    app._last_scheduled_refresh = 10.0

    with patch("homelab_console.app.monotonic", return_value=40.0):
        app._refresh_scheduler_tick()

    assert app._last_scheduled_refresh == 40.0
    assert app.refresh_calls == ["host", "containers", "storage"]
