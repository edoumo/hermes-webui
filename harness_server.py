#!/usr/bin/env python3
"""Experimental standalone entry point for Hermes Harness UI.

The server reuses Hermes WebUI authentication and HTTP hardening but has its
own localhost listener and its own WebUI auth state. It does not instantiate
Hermes Agent: all worker and session operations are relayed to the canonical
Hermes API by the Harness BFF layer.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import threading
import time
import traceback
from urllib.parse import urlparse

# Two WebUI processes on the same hostname must not share their auth cookie or
# concurrently rewrite the same .sessions.json file. Configure the Harness
# process before importing any api.* module so api.config/api.auth resolve a
# dedicated state directory and cookie namespace.
_harness_state = str(os.environ.get("HERMES_HARNESS_STATE_DIR", "")).strip()
if not _harness_state:
    _harness_state = str(Path.home() / ".hermes" / "webui-harness")
os.environ["HERMES_WEBUI_STATE_DIR"] = _harness_state
os.environ.setdefault("HERMES_WEBUI_COOKIE_NAME", "hermes_harness_session")

from api.auth import (  # noqa: E402
    check_auth,
    csrf_token_for_session,
    parse_cookie,
    reset_trusted_auth_request_state,
)
from api.harness_ui_operations import (  # noqa: E402
    handle_harness_request,
    harness_enabled,
    serve_harness_asset,
)
from api.helpers import _CLIENT_DISCONNECT_ERRORS, get_profile_cookie, j  # noqa: E402
from api.profiles import clear_request_profile, set_request_profile  # noqa: E402
from api.routes import _check_csrf, _csrf_rejection_error  # noqa: E402
from server import Handler, QuietHTTPServer, _ignore_sigpipe  # noqa: E402

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8790


class HarnessHandler(Handler):
    """Intercept only Harness routes; delegate login/static compatibility to WebUI."""

    @staticmethod
    def _is_harness_path(path: str) -> bool:
        return path in {
            "/harness",
            "/harness/",
            "/harness.js",
            "/harness.css",
            "/harness-operations.js",
        } or path.startswith("/api/harness")

    def _begin_harness_request(self):
        self._req_t0 = time.time()
        reset_trusted_auth_request_state(self)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not self._is_harness_path(parsed.path):
            return super().do_GET()
        self._begin_harness_request()
        try:
            if not check_auth(self, parsed):
                return
            if not harness_enabled():
                return j(self, {"error": "Harness UI is disabled"}, status=404)
            if parsed.path == "/api/harness/csrf":
                cookie = parse_cookie(self)
                token = csrf_token_for_session(cookie) if cookie else ""
                return j(self, {"csrfToken": token or ""})
            if serve_harness_asset(self, parsed.path):
                return
            if handle_harness_request(self, parsed, method="GET"):
                return
            return j(self, {"error": "Harness route not found"}, status=404)
        except _CLIENT_DISCONNECT_ERRORS:
            return
        except Exception:
            self._safe_webui_print(
                f"[harness] ERROR GET {self.path}\n" + traceback.format_exc()
            )
            try:
                j(self, {"error": "Internal Harness server error"}, status=500)
            except _CLIENT_DISCONNECT_ERRORS:
                pass
        finally:
            clear_request_profile()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/harness"):
            return super().do_POST()
        self._begin_harness_request()
        try:
            if not check_auth(self, parsed):
                return
            if not harness_enabled():
                return j(self, {"error": "Harness UI is disabled"}, status=404)
            if not _check_csrf(self):
                return j(
                    self,
                    {"error": _csrf_rejection_error(self), "code": "csrf_rejected"},
                    status=403,
                )
            if handle_harness_request(self, parsed, method="POST"):
                return
            return j(self, {"error": "Harness route not found"}, status=404)
        except _CLIENT_DISCONNECT_ERRORS:
            return
        except Exception:
            self._safe_webui_print(
                f"[harness] ERROR POST {self.path}\n" + traceback.format_exc()
            )
            try:
                j(self, {"error": "Internal Harness server error"}, status=500)
            except _CLIENT_DISCONNECT_ERRORS:
                pass
        finally:
            clear_request_profile()


def _host_port() -> tuple[str, int]:
    host = str(os.environ.get("HERMES_HARNESS_HOST", _DEFAULT_HOST)).strip() or _DEFAULT_HOST
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise RuntimeError("Hermes Harness UI foundation is localhost-only")
    raw_port = str(os.environ.get("HERMES_HARNESS_PORT", _DEFAULT_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("HERMES_HARNESS_PORT must be an integer") from exc
    if port < 1 or port > 65535:
        raise RuntimeError("HERMES_HARNESS_PORT must be between 1 and 65535")
    return host, port


def main() -> None:
    if not harness_enabled():
        raise SystemExit(
            "Hermes Harness UI is experimental and disabled. "
            "Set HERMES_WEBUI_HARNESS_UI=1 to start it."
        )
    _ignore_sigpipe()
    host, port = _host_port()
    httpd = QuietHTTPServer((host, port), HarnessHandler)
    stop_requested = threading.Event()

    def _request_shutdown(_signum, _frame):
        if stop_requested.is_set():
            return
        stop_requested.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)
    except (ValueError, OSError):
        pass

    print(f"  Hermes Harness UI listening on http://{host}:{port}/harness", flush=True)
    print("  Backend: canonical Hermes API (server-side authenticated BFF)", flush=True)
    print(f"  Harness auth state: {_harness_state}", flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
