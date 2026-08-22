#!/usr/bin/env python3
"""Experimental standalone entry point for Hermes Harness UI.

The server reuses Hermes WebUI authentication and HTTP hardening but has its
own listener and its own WebUI auth state. It does not instantiate Hermes
Agent: all worker and session operations are relayed to the canonical Hermes
API by the Harness BFF layer.

The default remains loopback-only. A non-loopback/LAN bind requires both an
explicit ``HERMES_HARNESS_ALLOW_REMOTE=1`` opt-in and a configured
``HERMES_WEBUI_PASSWORD`` so a convenience bind cannot silently publish an
unauthenticated control plane.
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
from api.harness_ui_bind import _LOOPBACK_HOSTS, resolve_harness_bind  # noqa: E402
from api.harness_ui_task_recovery import (  # noqa: E402
    handle_harness_request,
    harness_enabled,
    serve_harness_asset,
)
from api.helpers import _CLIENT_DISCONNECT_ERRORS, get_profile_cookie, j  # noqa: E402
from api.profiles import clear_request_profile, set_request_profile  # noqa: E402
from api.routes import _check_csrf, _csrf_rejection_error  # noqa: E402
from server import Handler, QuietHTTPServer, _ignore_sigpipe  # noqa: E402


class HarnessHandler(Handler):
    """Intercept only Harness routes; delegate login/static compatibility to WebUI."""

    @staticmethod
    def _is_harness_path(path: str) -> bool:
        return path in {
            "/harness",
            "/harness/",
            "/harness.js",
            "/harness.css",
            "/harness-locales.js",
            "/harness-preferences.js",
            "/harness-operations.js",
            "/harness-tasks.js",
            "/harness-task-recovery.js",
            "/harness-polish2.js",
            "/harness-polish3.js",
            "/harness-models.js",
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


def main() -> None:
    if not harness_enabled():
        raise SystemExit(
            "Hermes Harness UI is experimental and disabled. "
            "Set HERMES_WEBUI_HARNESS_UI=1 to start it."
        )
    _ignore_sigpipe()
    host, port = resolve_harness_bind()
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
    if host not in _LOOPBACK_HOSTS:
        print("  Remote/LAN bind explicitly enabled with password authentication", flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
