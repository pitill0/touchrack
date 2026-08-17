from homelab_console.runtime_diagnostics import (
    RuntimeDiagnostics,
    TouchDiagnostic,
    build_diagnostics_rows,
)


def _facts(**overrides) -> RuntimeDiagnostics:
    values = {
        "version": "4.6.2",
        "screen_tty": "/dev/tty1",
        "width": 64,
        "height": 18,
        "touch": TouchDiagnostic("OK", "event3 · USB touch", "ok"),
        "container_engine": "podman",
        "container_total": 3,
        "containers_error": None,
        "services_count": 5,
        "services_error": None,
        "config_source_path": None,
        "sleep_label": "1m",
        "screen_blank_error": None,
    }
    values.update(overrides)
    return RuntimeDiagnostics(**values)


def test_runtime_diagnostics_formats_healthy_state() -> None:
    rows = build_diagnostics_rows(_facts())

    assert rows == (
        ("VERSION", "4.6.2", "ok"),
        ("DISPLAY", "tty1 · 64×18", "ok"),
        ("TOUCH", "OK · event3 · USB touch", "ok"),
        ("ENGINE", "podman · 3 containers", "ok"),
        ("SERVICES", "5 visible", "ok"),
        ("CONFIG", "defaults", "muted"),
        ("SLEEP", "1m · OK", "ok"),
    )


def test_runtime_diagnostics_preserves_waiting_and_empty_states() -> None:
    rows = build_diagnostics_rows(
        _facts(
            container_engine=None,
            container_total=None,
            services_count=0,
            touch=TouchDiagnostic("DISABLED", "disabled", "muted"),
        )
    )

    assert rows[2] == ("TOUCH", "DISABLED · disabled", "muted")
    assert rows[3] == ("ENGINE", "WAITING", "muted")
    assert rows[4] == ("SERVICES", "0 visible", "warn")


def test_runtime_diagnostics_distinguishes_runtime_errors(tmp_path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("display: {}\n", encoding="utf-8")

    rows = build_diagnostics_rows(
        _facts(
            containers_error="podman failed",
            services_error="invalid services config",
            config_source_path=str(config),
            screen_blank_error="setterm failed",
        )
    )

    assert rows[3] == ("ENGINE", "podman · ERROR", "error")
    assert rows[4] == ("SERVICES", "ERROR", "error")
    assert rows[5] == ("CONFIG", "config.yaml", "ok")
    assert rows[6] == ("SLEEP", "1m · ERROR", "error")
