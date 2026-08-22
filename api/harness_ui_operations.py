"""H4 operational-control extension for the qualified Harness H3 BFF.

The H3 BFF remains unchanged. This module adds only the H4 allowlisted routes
and one local browser asset, delegating every other request and security
primitive back to ``api.harness_ui``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from api import harness_ui as foundation
from harness_runtime.http import j

_SAFE_ID = r"[A-Za-z0-9._:-]{1,256}"

_H4_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "GET",
        re.compile(rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-operations$"),
        "/api/sessions/{session_id}/worker-operations",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/retry$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/retry",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/activations/(?P<activation_id>{_SAFE_ID})/cancel$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/activations/{activation_id}/cancel",
    ),
)

_H3_BOOT = """s.onload=function(){
      if(document.readyState!=='loading')document.dispatchEvent(new Event('DOMContentLoaded'));
    };"""
_H4_BOOT = """s.onload=function(){
      var h4=document.createElement('script');
      h4.src='/harness-operations.js';
      h4.onload=function(){
        if(document.readyState!=='loading')document.dispatchEvent(new Event('DOMContentLoaded'));
      };
      document.head.appendChild(h4);
    };"""


def resolve_upstream(method: str, browser_path: str) -> Optional[str]:
    """Resolve H4 routes first, then preserve the exact H3 allowlist."""
    method = str(method or "").upper()
    prefix = "/api/harness"
    if browser_path.startswith(prefix):
        suffix = browser_path[len(prefix) :] or "/"
        for route_method, pattern, template in _H4_ROUTES:
            if route_method != method:
                continue
            match = pattern.fullmatch(suffix)
            if match:
                return template.format(**match.groupdict())
    return foundation.resolve_upstream(method, browser_path)


def handle_harness_request(handler, parsed, *, method: str) -> bool:
    """Handle H4 JSON controls or delegate unchanged H3 behavior."""
    if not foundation.harness_enabled():
        return False
    upstream = resolve_upstream(method, parsed.path)
    foundation_upstream = foundation.resolve_upstream(method, parsed.path)
    if upstream is None:
        return False
    if upstream != foundation_upstream:
        if parsed.query:
            j(
                handler,
                {"error": "H4 Harness control routes do not accept query parameters"},
                status=400,
            )
            return True
        return foundation._proxy_json(
            handler,
            parsed,
            method=method,
            upstream_path=upstream,
        )
    return foundation.handle_harness_request(handler, parsed, method=method)


def _serve_h4_html(handler) -> bool:
    target = Path(__file__).resolve().parent.parent / "static" / "harness.html"
    if not target.is_file():
        j(handler, {"error": "Harness asset missing"}, status=500)
        return True
    html = target.read_text(encoding="utf-8")
    if _H3_BOOT not in html:
        j(handler, {"error": "Harness H3 bootstrap contract changed"}, status=500)
        return True
    data = html.replace(_H3_BOOT, _H4_BOOT, 1).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


def serve_harness_asset(handler, path: str) -> bool:
    """Serve H4 shell/asset or delegate unchanged H3 static assets."""
    if path in {"/harness", "/harness/"}:
        return _serve_h4_html(handler)
    if path != "/harness-operations.js":
        return foundation.serve_harness_asset(handler, path)
    target = Path(__file__).resolve().parent.parent / "static" / "harness-operations.js"
    if not target.is_file():
        j(handler, {"error": "Harness operations asset missing"}, status=500)
        return True
    data = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/javascript; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


harness_enabled = foundation.harness_enabled


__all__ = [
    "handle_harness_request",
    "harness_enabled",
    "resolve_upstream",
    "serve_harness_asset",
]
