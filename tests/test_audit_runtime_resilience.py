"""Regression tests for the TouchRack runtime resilience audit."""

from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from homelab_console.providers.disk_health import SmartctlDiskHealthProvider
from homelab_console.services import HttpServiceProvider, ServiceDefinition


def _result(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(
        stdout=json.dumps(payload),
        stderr="",
        returncode=0,
    )


def test_smart_scan_timeout_is_reported_not_raised(monkeypatch):
    provider = SmartctlDiskHealthProvider(cache_seconds=0)

    def timeout(*args):
        raise subprocess.TimeoutExpired(args, 12)

    monkeypatch.setattr(provider, "_run", timeout)

    snapshot = asyncio.run(provider.collect())

    assert snapshot.available is False
    assert snapshot.disks == ()
    assert snapshot.error is not None
    assert "timeout" in snapshot.error.lower() or "timed out" in snapshot.error.lower()


def test_smart_device_timeout_preserves_other_disks(monkeypatch):
    provider = SmartctlDiskHealthProvider(
        cache_seconds=0,
        devices=("/dev/sda", "/dev/sdb"),
    )

    def fake_run(*args):
        if "--scan-open" in args:
            return _result({"devices": []})
        if "/dev/sda" in args:
            raise subprocess.TimeoutExpired(args, 12)
        return _result({
            "device": {"name": "/dev/sdb", "protocol": "ATA"},
            "smart_status": {"passed": True},
        })

    monkeypatch.setattr(provider, "_run", fake_run)

    snapshot = asyncio.run(provider.collect())

    assert snapshot.available is True
    assert [disk.device for disk in snapshot.disks] == ["/dev/sdb"]


@pytest.fixture
def http_test_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/healthy")
            elif self.path == "/unauthorized":
                self.send_response(401)
            elif self.path == "/failure":
                self.send_response(500)
            elif self.path == "/healthy":
                self.send_response(200)
            else:
                self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize(
    ("path", "expected", "status"),
    [
        ("/unauthorized", 401, "OK"),
        ("/failure", 200, "WARN"),
        ("/failure", 500, "OK"),
        ("/missing", 404, "OK"),
        ("/redirect", 302, "OK"),
    ],
)
def test_http_status_codes_are_observable(
    http_test_server, path, expected, status
):
    definition = ServiceDefinition(
        id="audit-http",
        title="Audit HTTP",
        provider="http",
        target=f"{http_test_server}{path}",
        options={"expect": expected},
    )

    state = asyncio.run(HttpServiceProvider().check(definition))

    assert state.status == status
    actual_code = {
        "/unauthorized": 401,
        "/failure": 500,
        "/missing": 404,
        "/redirect": 302,
    }[path]
    assert state.primary == f"HTTP {actual_code}"
    assert ("EXPECTED", str(expected)) in state.details
