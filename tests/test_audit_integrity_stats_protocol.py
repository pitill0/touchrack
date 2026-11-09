from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from homelab_console.providers.remote_containers import (
    _REMOTE_CONTAINERS_SCRIPT,
    _parse_frames,
)

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts/touchrack-container-inventory"


@pytest.mark.parametrize("engine", ["podman", "docker"])
@pytest.mark.parametrize("fail_stats", [False, True])
def test_stats_stderr_isolated_and_failure_visible(
    tmp_path: Path, engine: str, fail_stats: bool
) -> None:
    executable = tmp_path / engine
    executable.write_text(
        """#!/bin/sh
case "$1" in
    ps)
        if [ "$(basename "$0")" = podman ]; then
            printf '%s\\n' '[{"Id":"abc","Names":["demo"],"State":"running"}]'
        else
            printf '%s\\n' '{"ID":"abc","Names":"demo","State":"running"}'
        fi
        ;;
    stats)
        printf '%s\\n' 'WARN[0000] noisy runtime warning' >&2
        if [ "${FAKE_FAIL:-0}" = 1 ]; then
            printf '%s\\n' 'permission denied' >&2
            exit 125
        fi
        if [ "$(basename "$0")" = podman ]; then
            printf '%s\\n' '[{"id":"abc","cpu_percent":"3%"}]'
        else
            printf '%s\\n' '{"ID":"abc","CPUPerc":"3%"}'
        fi
        ;;
    *)
        exit 64
        ;;
esac
"""
    )
    executable.chmod(0o755)

    scratch = tmp_path / "scratch"
    scratch.mkdir()

    # Do not expose host Podman/Docker when exercising the opposite
    # fake engine. Only the fake CLI and required shell tools are visible.
    for tool in ("basename", "mktemp", "cat", "rm"):
        binary = shutil.which(tool)
        assert binary is not None, f"missing test prerequisite: {tool}"
        (tmp_path / tool).symlink_to(binary)

    env = os.environ.copy()
    env["PATH"] = str(tmp_path)
    env["TMPDIR"] = str(scratch)
    env["FAKE_FAIL"] = "1" if fail_stats else "0"

    # Execute the real embedded collector with a fake container CLI.
    result = subprocess.run(
        ["/bin/sh", "-c", _REMOTE_CONTAINERS_SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    selected, ps_output, status, stats_output = _parse_frames(
        result.stdout
    )

    assert selected == engine.capitalize()
    assert "demo" in ps_output

    if fail_stats:
        assert status == 125
        assert "permission denied" in stats_output
    else:
        assert status == 0
        assert "WARN[" not in stats_output
        assert "3%" in stats_output

    # A successful command may emit warnings, but they must remain
    # outside the framed JSON stream.
    assert "WARN[" in result.stderr or (
        fail_stats and "WARN[" in stats_output
    )

    # The private diagnostic file must be cleaned on shell exit.
    assert list(scratch.iterdir()) == []


def test_embedded_and_privileged_stats_capture_stay_in_sync() -> None:
    helper = HELPER.read_text()

    def stats_section(source: str) -> str:
        return source.split("set +e\n", 1)[1].split(
            "printf 'stats_status=%s", 1
        )[0]

    assert stats_section(helper) == stats_section(
        _REMOTE_CONTAINERS_SCRIPT
    )


def test_privileged_helper_preserves_fixed_path_and_no_args() -> None:
    source = HELPER.read_text()
    assert 'if [ "$#" -ne 0 ]; then' in source
    assert "PATH=/usr/local/sbin:/usr/local/bin:" in source
    assert "touchrack_containers_protocol=1" in source
