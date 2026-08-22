"""Regression tests for the Harness standalone BFF SSE relay."""
from __future__ import annotations

import io
from types import SimpleNamespace

from harness_runtime import bff as harness


class _FakeResponse:
    status = 200

    def __init__(self, lines):
        self._lines = iter(lines)
        self.closed = False

    def readline(self, _limit=-1):
        value = next(self._lines)
        if isinstance(value, BaseException):
            raise value
        return value

    def read(self, _size=-1):
        raise AssertionError("SSE relay must be line-oriented, not block-buffered")

    def close(self):
        self.closed = True


class _FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None
        self.timeout = None

    def open(self, request, timeout=None):
        self.request = request
        self.timeout = timeout
        return self.response


class _FakeHandler:
    def __init__(self, headers=None):
        self.headers = headers or {}
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = {}

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers[key] = value

    def end_headers(self):
        pass


def test_sse_proxy_relays_each_line_before_upstream_eof(monkeypatch):
    token = "a" * 64
    response = _FakeResponse(
        [
            f"id: {token}\n".encode(),
            b"event: durable_workers.changed\n",
            b"data: {\"event\":\"durable_workers.changed\"}\n",
            b"\n",
            ConnectionResetError("test disconnect after first frame"),
        ]
    )
    opener = _FakeOpener(response)
    monkeypatch.setattr(harness, "_OPENER", opener)
    monkeypatch.setenv("HERMES_HARNESS_GATEWAY_BASE_URL", "http://127.0.0.1:53481")
    monkeypatch.setenv("HERMES_HARNESS_GATEWAY_API_KEY", "test-secret")

    handler = _FakeHandler()
    parsed = SimpleNamespace(query="")

    assert harness._proxy_sse(
        handler,
        parsed,
        upstream_path="/api/sessions/session_1/worker-events",
    ) is True

    payload = handler.wfile.getvalue()
    assert handler.status == 200
    assert handler.sent_headers["Content-Type"] == "text/event-stream"
    assert f"id: {token}\n".encode() in payload
    assert b"event: durable_workers.changed\n" in payload
    assert b"data: {\"event\":\"durable_workers.changed\"}\n\n" in payload
    assert response.closed is True
    assert opener.request.get_header("Authorization") == "Bearer test-secret"


def test_sse_proxy_forwards_last_event_id(monkeypatch):
    token = "b" * 64
    response = _FakeResponse([ConnectionResetError("done")])
    opener = _FakeOpener(response)
    monkeypatch.setattr(harness, "_OPENER", opener)
    monkeypatch.setenv("HERMES_HARNESS_GATEWAY_BASE_URL", "http://127.0.0.1:53481")
    monkeypatch.setenv("HERMES_HARNESS_GATEWAY_API_KEY", "test-secret")

    handler = _FakeHandler({"Last-Event-ID": token})
    parsed = SimpleNamespace(query="")

    assert harness._proxy_sse(
        handler,
        parsed,
        upstream_path="/api/sessions/session_1/worker-events",
    ) is True
    assert opener.request.get_header("Last-event-id") == token
