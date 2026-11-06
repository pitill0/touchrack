"""HTTP resilience regression tests."""

from __future__ import annotations

import asyncio
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from homelab_console.services import HttpServiceProvider, ServiceDefinition


@pytest.fixture
def http_edge_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/healthy")
                self.end_headers()
            elif self.path == "/healthy":
                self.send_response(200)
                self.end_headers()
            elif self.path == "/disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize(
    ("path", "expected", "status", "primary"),
    [
        ("/redirect", 200, "OK", "HTTP 200"),
        ("/redirect", 302, "OK", "HTTP 302"),
        ("/disconnect", 200, "ERROR", "HTTP DOWN"),
    ],
)
def test_http_redirects_and_disconnects(
    http_edge_server, path, expected, status, primary
):
    definition = ServiceDefinition(
        id="audit-http",
        title="Audit HTTP",
        provider="http",
        target=f"{http_edge_server}{path}",
        options={"expect": expected, "timeout": 2.0},
    )

    state = asyncio.run(HttpServiceProvider().check(definition))

    assert state.status == status
    assert state.primary == primary

    if status == "ERROR":
        assert state.error
    else:
        assert ("HTTP", str(expected)) in state.details
