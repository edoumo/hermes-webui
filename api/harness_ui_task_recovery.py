"""H5 recovery plus H6.1/H6.2/H6.3 human-UAT polish extensions for Harness."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from api import harness_ui as foundation
from api import harness_ui_operations as operations
from api import harness_ui_tasks as tasks
from api.helpers import j

_SAFE_ID = r"[A-Za-z0-9._:-]{1,256}"

_RECOVERY_ROUTE = (
    "POST",
    re.compile(
        rf"^/sessions/(?P<session_id>{_SAFE_ID})/worker-tasks/(?P<task_id>{_SAFE_ID})/recover$"
    ),
    "/api/sessions/{session_id}/worker-tasks/{task_id}/recover",
)

_H61_WORKER_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/edit$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/edit",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/archive$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/archive",
    ),
    (
        "POST",
        re.compile(
            rf"^/sessions/(?P<session_id>{_SAFE_ID})/workers/(?P<worker_id>{_SAFE_ID})/restore$"
        ),
        "/api/sessions/{session_id}/workers/{worker_id}/restore",
    ),
)

# Hermes' stock API owns both the provider/model inventory and the auxiliary
# slot projection. Harness only exposes those existing contracts through the
# same server-side authenticated BFF; it does not maintain a second catalog.
_H62_ROUTES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("GET", re.compile(r"^/model-options$"), "/api/model/options"),
    ("GET", re.compile(r"^/model-auxiliary$"), "/api/model/auxiliary"),
    ("POST", re.compile(r"^/model-set$"), "/api/model/set"),
)

_H5_RECOVERY_BOOT = """s.onload=function(){
      var h4=document.createElement('script');
      h4.src='/harness-operations.js';
      h4.onload=function(){
        var h5=document.createElement('script');
        h5.src='/harness-tasks.js';
        h5.onload=function(){
          var h5r=document.createElement('script');
          h5r.src='/harness-task-recovery.js';
          h5r.onload=function(){
            var h62=document.createElement('script');
            h62.src='/harness-polish2.js';
            h62.onload=function(){
              var h63=document.createElement('script');
              h63.src='/harness-polish3.js';
              h63.onload=function(){
                var hm=document.createElement('script');
                hm.src='/harness-models.js';
                hm.onload=function(){
                  if(document.readyState!=='loading')document.dispatchEvent(new Event('DOMContentLoaded'));
                };
                document.head.appendChild(hm);
              };
              document.head.appendChild(h63);
            };
            document.head.appendChild(h62);
          };
          document.head.appendChild(h5r);
        };
        document.head.appendChild(h4);
      };
      document.head.appendChild(h4);
    };"""


def resolve_upstream(method: str, browser_path: str) -> Optional[str]:
    method = str(method or "").upper()
    prefix = "/api/harness"
    if browser_path.startswith(prefix):
        suffix = browser_path[len(prefix) :] or "/"
        if method == _RECOVERY_ROUTE[0]:
            match = _RECOVERY_ROUTE[1].fullmatch(suffix)
            if match:
                return _RECOVERY_ROUTE[2].format(**match.groupdict())
        for route_method, pattern, template in _H61_WORKER_ROUTES + _H62_ROUTES:
            if route_method != method:
                continue
            match = pattern.fullmatch(suffix)
            if match:
                return template.format(**match.groupdict())
    return tasks.resolve_upstream(method, browser_path)


def handle_harness_request(handler, parsed, *, method: str) -> bool:
    if not foundation.harness_enabled():
        return False
    upstream = resolve_upstream(method, parsed.path)
    inherited = tasks.resolve_upstream(method, parsed.path)
    if upstream is None:
        return False
    if upstream != inherited:
        proxy_parsed = parsed
        proxy_upstream = upstream
        if parsed.query:
            # Keep this extension strict: the only accepted query is the
            # model-options UI's explicit refresh=true hint. Forward that exact
            # flag to Hermes without widening the generic Harness query surface.
            if method == "GET" and parsed.path == "/api/harness/model-options" and parsed.query == "refresh=true":
                proxy_parsed = parsed._replace(query="")
                proxy_upstream = upstream + "?refresh=true"
            else:
                j(
                    handler,
                    {"error": "Harness operator/catalog routes do not accept query parameters"},
                    status=400,
                )
                return True
        return foundation._proxy_json(
            handler,
            proxy_parsed,
            method=method,
            upstream_path=proxy_upstream,
        )
    return tasks.handle_harness_request(handler, parsed, method=method)


def _serve_recovery_html(handler) -> bool:
    target = Path(__file__).resolve().parent.parent / "static" / "harness.html"
    if not target.is_file():
        j(handler, {"error": "Harness asset missing"}, status=500)
        return True
    html = target.read_text(encoding="utf-8")
    if operations._H3_BOOT not in html:
        j(handler, {"error": "Harness H3 bootstrap contract changed"}, status=500)
        return True
    data = html.replace(operations._H3_BOOT, _H5_RECOVERY_BOOT, 1).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


def _serve_js_asset(handler, filename: str) -> bool:
    target = Path(__file__).resolve().parent.parent / "static" / filename
    if not target.is_file():
        j(handler, {"error": "Harness asset missing"}, status=500)
        return True
    data = target.read_bytes()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/javascript; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
    return True


def serve_harness_asset(handler, path: str) -> bool:
    if path in {"/harness", "/harness/"}:
        return _serve_recovery_html(handler)
    assets = {
        "/harness-task-recovery.js": "harness-task-recovery.js",
        "/harness-preferences.js": "harness-preferences.js",
        "/harness-locales.js": "harness-locales.js",
        "/harness-polish2.js": "harness-polish2.js",
        "/harness-polish3.js": "harness-polish3.js",
        "/harness-models.js": "harness-models.js",
    }
    if path in assets:
        return _serve_js_asset(handler, assets[path])
    return tasks.serve_harness_asset(handler, path)


harness_enabled = foundation.harness_enabled


__all__ = [
    "handle_harness_request",
    "harness_enabled",
    "resolve_upstream",
    "serve_harness_asset",
]
