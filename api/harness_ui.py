"""Experimental Hermes Harness UI backend-for-frontend.

This module is deliberately small and independent from the legacy WebUI chat
runtime.  Browser requests stay same-origin; the Hermes API bearer credential
is read only by the server and is never returned to JavaScript.

The public surface is an allowlist.  No arbitrary proxy path, host, method, or
header is accepted.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from api.helpers import j

_HARNESS_PREFIX = "/api/harness"
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_NORMAL_TIMEOUT_SECONDS = 30.0
_SSE_TIMEOUT_SECONDS = 650.0
_SAFE_ID = r"[A-Za-z0-9._:-]{1,256}"


class HarnessConfigError(RuntimeError):
    """Harness UI configuration is incomplete or unsafe."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
)

# (method, browser path regex, upstream path template)
_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("GET", re.compile(r"^/models$"), "/v1/models"),
    ("GET", re.compile(r"^/sessions$"), "/api/sessions"),
    ("POST", re.compile(r"^/sessions$"), "/api/sessions"),
    (
        "GET",
        re.compile(rf"^/sessions/(?P<session_id>{_SAFE_ID})$"),
        "/api/sessions/{session_id}",
    ),
    (
        "GET",
        re.compile(rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers$"),
        "/api/sessions/{session_id}/workers",
    ),
    (
        "POST",
        re.compile(rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers$"),
        "/api/sessions/{session_id}/workers",
    ),
    (
        "GET",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}",
    ),
    (
        "GET",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/messages$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/messages",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/messages$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/messages",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/run$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/run",
    ),
    (
        "GET",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/activations$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/activations",
    ),
    (
        "GET",
        re.compile(rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks$"),
        "/api/sessions/{session_id}/worker-tasks",
    ),
    (
        "POST",
        re.compile(rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks$"),
        "/api/sessions/{session_id}/worker-tasks",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks/(?P<task_id>{_SAFE_ID})/status$"
        ),
        "/api/sessions/{session_id}/worker-tasks/{task_id}/status",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks/(?P<task_id>{_SAFE_ID})/dependencies$"
        ),
        "/api/sessions/{session_id}/worker-tasks/{task_id}/dependencies",
    ),
    (
        "GET",
        re.compile(rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-events$"),
        "/api/sessions/{session_id}/worker-events",
    ),
)

_QUERY_ALLOWLIST = frozenset({"limit", "cursor"})


def harness_enabled(environ: Optional[dict[str, str]] = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get("HERMES_WEBUI_HARNESS_UI", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _gateway_base_url(environ: Optional[dict[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    raw = str(
        env.get("HERMES_WEBUI_GATEWAY_BASE_URL", "http://127.0.0.1:8642")
    ).strip()
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        raise HarnessConfigError("Hermes API URL must use http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise HarnessConfigError("Hermes API URL must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise HarnessConfigError("Hermes API URL must be an origin without path/query")
    host = parsed.hostname.lower()
    # Foundation phase is intentionally loopback-only.  Remote control-plane
    # exposure needs a separate threat-model and explicit operator decision.
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise HarnessConfigError("Hermes API URL must be loopback in Harness foundation")
    return raw.rstrip("/")


def _gateway_api_key(environ: Optional[dict[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    value = str(env.get("HERMES_WEBUI_GATEWAY_API_KEY", "")).strip()
    if not value:
        raise HarnessConfigError("HERMES_WEBUI_GATEWAY_API_KEY is required")
    if "\r" in value or "\n" in value:
        raise HarnessConfigError("Hermes API key contains invalid characters")
    return value


def resolve_upstream(method: str, browser_path: str) -> Optional[str]:
    """Resolve one allowlisted BFF path to its Hermes API path."""
    method = str(method or "").upper()
    if not browser_path.startswith(_HARNESS_PREFIX):
        return None
    suffix = browser_path[len(_HARNESS_PREFIX) :] or "/"
    for route_method, pattern, template in _ROUTES:
        if route_method != method:
            continue
        match = pattern.fullmatch(suffix)
        if match:
            return template.format(**match.groupdict())
    return None


def _safe_query(raw_query: str) -> str:
    if not raw_query:
        return ""
    pairs = urllib.parse.parse_qsl(raw_query, keep_blank_values=True, max_num_fields=8)
    safe: list[tuple[str, str]] = []
    for key, value in pairs:
        if key not in _QUERY_ALLOWLIST:
            raise HarnessConfigError(f"Unsupported Harness query parameter: {key}")
        if len(value) > 2048:
            raise HarnessConfigError("Harness query value is too long")
        safe.append((key, value))
    return urllib.parse.urlencode(safe)


def _read_json_body(handler) -> bytes:
    raw_length = handler.headers.get("Content-Length")
    try:
        length = int(raw_length or "0")
    except (TypeError, ValueError) as exc:
        raise HarnessConfigError("Invalid Content-Length") from exc
    if length < 0 or length > _MAX_REQUEST_BYTES:
        raise HarnessConfigError("Harness request body is too large")
    if length == 0:
        return b"{}"
    data = handler.rfile.read(length)
    if len(data) != length:
        raise HarnessConfigError("Incomplete Harness request body")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise HarnessConfigError("Harness request body must be valid JSON") from exc
    if not isinstance(parsed, dict):
        raise HarnessConfigError("Harness request body must be a JSON object")
    return json.dumps(parsed, separators=(",", ":")).encode("utf-8")


def _upstream_request(
    handler,
    parsed,
    *,
    method: str,
    upstream_path: str,
    environ: Optional[dict[str, str]] = None,
):
    query = _safe_query(parsed.query) if method == "GET" else ""
    base = _gateway_base_url(environ)
    target = f"{base}{upstream_path}"
    if query:
        target += "?" + query
    body = _read_json_body(handler) if method == "POST" else None
    headers = {
        "Authorization": "Bearer " + _gateway_api_key(environ),
        "Accept": "application/json",
        "User-Agent": "Hermes-Harness-UI/0.1",
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    return urllib.request.Request(target, data=body, headers=headers, method=method)


def _send_bytes(handler, status: int, data: bytes, headers: Any) -> None:
    content_type = str(headers.get("Content-Type") or "application/json; charset=utf-8")
    handler.send_response(int(status))
    handler.send_header("Content-Type", content_type)
    retry_after = headers.get("Retry-After")
    if retry_after and str(retry_after).isdigit():
        handler.send_header("Retry-After", str(retry_after))
    session_id = headers.get("X-Hermes-Session-Id")
    if session_id and re.fullmatch(_SAFE_ID, str(session_id)):
        handler.send_header("X-Hermes-Session-Id", str(session_id))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _read_bounded_response(response) -> bytes:
    data = response.read(_MAX_RESPONSE_BYTES + 1)
    if len(data) > _MAX_RESPONSE_BYTES:
        raise HarnessConfigError("Hermes API response exceeded Harness limit")
    return data


def _proxy_json(handler, parsed, *, method: str, upstream_path: str) -> bool:
    try:
        request = _upstream_request(
            handler,
            parsed,
            method=method,
            upstream_path=upstream_path,
        )
        try:
            response = _OPENER.open(request, timeout=_NORMAL_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            data = _read_bounded_response(exc)
            _send_bytes(handler, exc.code, data, exc.headers)
            return True
        with response:
            data = _read_bounded_response(response)
            _send_bytes(handler, response.status, data, response.headers)
            return True
    except HarnessConfigError as exc:
        j(handler, {"error": str(exc), "code": "harness_configuration"}, status=503)
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        j(
            handler,
            {"error": "Hermes API is unavailable", "code": "harness_upstream_unavailable"},
            status=502,
        )
        return True


def _proxy_sse(handler, parsed, *, upstream_path: str) -> bool:
    try:
        base = _gateway_base_url()
        request = urllib.request.Request(
            f"{base}{upstream_path}",
            headers={
                "Authorization": "Bearer " + _gateway_api_key(),
                "Accept": "text/event-stream",
                "User-Agent": "Hermes-Harness-UI/0.1",
            },
            method="GET",
        )
        last_event_id = str(handler.headers.get("Last-Event-ID") or "").strip()
        if last_event_id:
            if not re.fullmatch(r"(?:empty|[0-9a-f]{64})", last_event_id):
                j(handler, {"error": "Invalid Last-Event-ID"}, status=400)
                return True
            request.add_header("Last-Event-ID", last_event_id)
        try:
            response = _OPENER.open(request, timeout=_SSE_TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            data = _read_bounded_response(exc)
            _send_bytes(handler, exc.code, data, exc.headers)
            return True
        handler.send_response(int(response.status))
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Cache-Control", "no-cache")
        handler.send_header("X-Accel-Buffering", "no")
        handler.send_header("Connection", "close")
        handler.end_headers()
        try:
            while True:
                chunk = response.read(4096)
                if not chunk:
                    break
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            response.close()
        return True
    except HarnessConfigError as exc:
        j(handler, {"error": str(exc), "code": "harness_configuration"}, status=503)
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        j(
            handler,
            {"error": "Hermes API event stream is unavailable", "code": "harness_upstream_unavailable"},
            status=502,
        )
        return True


def serve_harness_asset(handler, path: str) -> bool:
    """Serve the isolated Harness shell and its two static assets."""
    if path not in {"/harness", "/harness/", "/harness.js", "/harness.css"}:
        return False
    filename = {
        "/harness": "harness.html",
        "/harness/": "harness.html",
        "/harness.js": "harness.js",
        "/harness.css": "harness.css",
    }[path]
    target = Path(__file__).resolve().parent.parent / "static" / filename
    if not target.is_file():
        j(handler, {"error": "Harness asset missing"}, status=500)
        return True
    data = target.read_bytes()
    content_type = {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
    }[target.suffix]
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


def handle_harness_request(handler, parsed, *, method: str) -> bool:
    """Handle an allowlisted Harness API request; False means not ours."""
    if not harness_enabled():
        return False
    upstream_path = resolve_upstream(method, parsed.path)
    if upstream_path is None:
        return False
    if upstream_path.endswith("/worker-events"):
        if method != "GET" or parsed.query:
            j(handler, {"error": "Invalid Harness event request"}, status=400)
            return True
        return _proxy_sse(handler, parsed, upstream_path=upstream_path)
    return _proxy_json(handler, parsed, method=method, upstream_path=upstream_path)


__all__ = [
    "HarnessConfigError",
    "handle_harness_request",
    "harness_enabled",
    "resolve_upstream",
    "serve_harness_asset",
]
