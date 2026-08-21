"""H5 task-orchestration extension for the qualified H4 Harness BFF.

H5 adds only task graph/edit/dispatch routes and one local browser asset. H4
continues to own operational worker controls and H3 continues to own auth, SSE,
session selection and the foundational browser state.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from api import harness_ui as foundation
from api import harness_ui_operations as operations
from api.helpers import j

_SAFE_ID = r"[A-Za-z0-9._:-]{1,256}"

_H5_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "GET",
        re.compile(rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-task-graph$"),
        "/api/sessions/{session_id}/worker-task-graph",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks/(?P<task_id>{_SAFE_ID})/edit$"
        ),
        "/api/sessions/{session_id}/worker-tasks/{task_id}/edit",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks/(?P<task_id>{_SAFE_ID})/dependencies/add$"
        ),
        "/api/sessions/{session_id}/worker-tasks/{task_id}/dependencies/add",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks/(?P<task_id>{_SAFE_ID})/dependencies/remove$"
        ),
        "/api/sessions/{session_id}/worker-tasks/{task_id}/dependencies/remove",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks/(?P<task_id>{_SAFE_ID})/dispatch$"
        ),
        "/api/sessions/{session_id}/worker-tasks/{task_id}/dispatch",
    ),
)

_H5_BOOT = """s.onload=function(){
      var h4=document.createElement('script');
      h4.src='/harness-operations.js';
      h4.onload=function(){
        var h5=document.createElement('script');
        h5.src='/harness-tasks.js';
        h5.onload=function(){
          if(document.readyState!=='loading')document.dispatchEvent(new Event('DOMContentLoaded'));
        };
        document.head.appendChild(h5);
      };
      document.head.appendChild(h4);
    };"""


def resolve_upstream(method: str, browser_path: str) -> Optional[str]:
    method = str(method or "").upper()
    prefix = "/api/harness"
    if browser_path.startswith(prefix):
        suffix = browser_path[len(prefix) :] or "/"
        for route_method, pattern, template in _H5_ROUTES:
            if route_method != method:
                continue
            match = pattern.fullmatch(suffix)
            if match:
                return template.format(**match.groupdict())
    return operations.resolve_upstream(method, browser_path)


def handle_harness_request(handler, parsed, *, method: str) -> bool:
    if not foundation.harness_enabled():
        return False
    upstream = resolve_upstream(method, parsed.path)
    inherited = operations.resolve_upstream(method, parsed.path)
    if upstream is None:
        return False
    if upstream != inherited:
        if method != "GET" and parsed.query:
            j(
                handler,
                {"error": "H5 Harness task control routes do not accept query parameters"},
                status=400,
            )
            return True
        return foundation._proxy_json(
            handler,
            parsed,
            method=method,
            upstream_path=upstream,
        )
    return operations.handle_harness_request(handler, parsed, method=method)


def _serve_h5_html(handler) -> bool:
    target = Path(__file__).resolve().parent.parent / "static" / "harness.html"
    if not target.is_file():
        j(handler, {"error": "Harness asset missing"}, status=500)
        return True
    html = target.read_text(encoding="utf-8")
    if operations._H3_BOOT not in html:
        j(handler, {"error": "Harness H3 bootstrap contract changed"}, status=500)
        return True
    data = html.replace(operations._H3_BOOT, _H5_BOOT, 1).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


def serve_harness_asset(handler, path: str) -> bool:
    if path in {"/harness", "/harness/"}:
        return _serve_h5_html(handler)
    if path != "/harness-tasks.js":
        return operations.serve_harness_asset(handler, path)
    target = Path(__file__).resolve().parent.parent / "static" / "harness-tasks.js"
    if not target.is_file():
        j(handler, {"error": "Harness task orchestration asset missing"}, status=500)
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
