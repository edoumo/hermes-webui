"""H4 operational-control extension for the qualified Harness H3 BFF.

The H3 BFF remains unchanged.  This module adds only the H4 allowlisted routes
and delegates every other request, asset and security primitive back to
``api.harness_ui``.
"""
from __future__ import annotations

import re
from typing import Optional

from api import harness_ui as foundation

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
            foundation.j(
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


harness_enabled = foundation.harness_enabled
serve_harness_asset = foundation.serve_harness_asset


__all__ = [
    "handle_harness_request",
    "harness_enabled",
    "resolve_upstream",
    "serve_harness_asset",
]
