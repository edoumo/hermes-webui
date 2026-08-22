"""Small HTTP helpers owned by Hermes Harness.

Kept deliberately stdlib-only so the standalone server does not need the
legacy WebUI helper module just to emit JSON or identify client disconnects.
"""
from __future__ import annotations

import json
from typing import Any

CLIENT_DISCONNECT_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
)


def json_response(handler, payload: Any, *, status: int = 200) -> None:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(int(status))
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)
