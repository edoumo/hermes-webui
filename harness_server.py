#!/usr/bin/env python3
"""Standalone Hermes Harness HTTP server.

This entry point deliberately does not import Hermes WebUI authentication,
profiles, routes or server classes. Harness owns its small HTTP/auth surface and
relays durable-worker/model operations to the canonical Hermes API through the
strict Harness BFF.

Loopback is the default. A non-loopback bind requires both
``HERMES_HARNESS_ALLOW_REMOTE=1`` and ``HERMES_HARNESS_PASSWORD``.
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from harness_runtime import auth
from harness_runtime.http import CLIENT_DISCONNECT_ERRORS, json_response

# The existing BFF modules still honour the historical feature flag while they
# live in the hermes-webui tree. Standalone Harness is itself the opt-in entry
# point, so enable that compatibility gate before importing the BFF chain.
os.environ.setdefault("HERMES_WEBUI_HARNESS_UI", "1")

from api.harness_ui_task_recovery import (  # noqa: E402
    handle_harness_request,
    serve_harness_asset,
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_MAX_LOGIN_BODY = 16 * 1024

_LOGIN_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hermes Harness · Login</title><meta name="color-scheme" content="dark light">
<style>
:root{font-family:Inter,system-ui,sans-serif;color-scheme:dark;background:#11151b;color:#edf1f7}
body{min-height:100vh;margin:0;display:grid;place-items:center}.card{width:min(380px,calc(100vw - 40px));padding:28px;border:1px solid #303844;border-radius:16px;background:#171d25;box-shadow:0 18px 60px #0008}h1{margin:0 0 8px;font-size:1.45rem}p{color:#aeb8c6}label{display:grid;gap:7px;margin:20px 0}input,button{font:inherit;border-radius:9px;padding:11px 12px}input{border:1px solid #3a4554;background:#0e1319;color:inherit}button{border:0;background:#e9eef5;color:#10151b;font-weight:700;cursor:pointer;width:100%}.error{min-height:1.25em;color:#ff8b94;font-size:.9rem}
</style></head><body><main class="card"><h1>Hermes Harness</h1><p>Durable worker control plane</p>
<form id="f"><label>Password<input id="p" type="password" autocomplete="current-password" required autofocus></label><div id="e" class="error"></div><button type="submit">Sign in</button></form></main>
<script>document.getElementById('f').addEventListener('submit',async function(ev){ev.preventDefault();const e=document.getElementById('e');e.textContent='';try{const r=await fetch('/api/harness/auth/login',{method:'POST',credentials:'include',headers:{'Content-Type':'application/json','Accept':'application/json'},body:JSON.stringify({password:document.getElementById('p').value})});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.error||'Authentication failed');location.replace('/harness');}catch(err){e.textContent=String(err.message||err);}});</script></body></html>"""


def _flag(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}


def resolve_bind() -> tuple[str, int]:
    host = str(os.environ.get("HERMES_HARNESS_HOST", "127.0.0.1")).strip() or "127.0.0.1"
    try:
        port = int(str(os.environ.get("HERMES_HARNESS_PORT", "8790")).strip())
    except ValueError as exc:
        raise RuntimeError("HERMES_HARNESS_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("HERMES_HARNESS_PORT must be between 1 and 65535")
    if host not in _LOOPBACK_HOSTS:
        if not _flag("HERMES_HARNESS_ALLOW_REMOTE"):
            raise RuntimeError("Non-loopback Harness bind requires HERMES_HARNESS_ALLOW_REMOTE=1")
        if not auth.password_configured():
            raise RuntimeError("Non-loopback Harness bind requires HERMES_HARNESS_PASSWORD")
    return host, port


class HarnessHandler(BaseHTTPRequestHandler):
    server_version = "HermesHarness/0.2"
    _login_lock = threading.Lock()
    _login_attempts: dict[str, list[float]] = {}

    def log_message(self, fmt: str, *args) -> None:
        if _flag("HERMES_HARNESS_ACCESS_LOG"):
            super().log_message(fmt, *args)

    def _secure_cookie(self) -> bool:
        return _flag("HERMES_HARNESS_SECURE_COOKIE")

    def _html(self, body: str, *, status: int = 200) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, target: str) -> None:
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _is_authenticated(self) -> bool:
        return auth.authenticated(self)

    def _require_auth(self, *, api: bool) -> bool:
        if self._is_authenticated():
            return True
        if api:
            json_response(self, {"error": "Authentication required", "code": "authentication_required"}, status=401)
        else:
            self._redirect("/login")
        return False

    def _read_login_password(self) -> str:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or "0")
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length <= 0 or length > _MAX_LOGIN_BODY:
            raise ValueError("Invalid login payload size")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise ValueError("Incomplete login payload")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Login payload must be an object")
        return str(payload.get("password") or "")

    def _login_allowed(self) -> bool:
        ip = str(self.client_address[0] if self.client_address else "unknown")
        now = time.time()
        with self._login_lock:
            attempts = [stamp for stamp in self._login_attempts.get(ip, []) if now - stamp < 60]
            self._login_attempts[ip] = attempts
            return len(attempts) < 5

    def _record_failed_login(self) -> None:
        ip = str(self.client_address[0] if self.client_address else "unknown")
        with self._login_lock:
            self._login_attempts.setdefault(ip, []).append(time.time())

    def _clear_login_attempts(self) -> None:
        ip = str(self.client_address[0] if self.client_address else "unknown")
        with self._login_lock:
            self._login_attempts.pop(ip, None)

    def _login(self) -> None:
        if not auth.password_configured():
            return json_response(self, {"ok": True, "authConfigured": False})
        if not self._login_allowed():
            return json_response(self, {"error": "Too many login attempts"}, status=429)
        try:
            password = self._read_login_password()
        except (ValueError, UnicodeError, json.JSONDecodeError):
            return json_response(self, {"error": "Invalid login payload"}, status=400)
        if not auth.verify_password(password):
            self._record_failed_login()
            return json_response(self, {"error": "Invalid password"}, status=401)
        self._clear_login_attempts()
        session = auth.create_session()
        self.send_response(200)
        self.send_header("Set-Cookie", auth.session_cookie_header(session, secure=self._secure_cookie()))
        data = json.dumps({"ok": True}, separators=(",", ":")).encode("utf-8")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _logout(self) -> None:
        cookie = auth.parse_cookie(self)
        if cookie:
            auth.delete_session(cookie)
        self.send_response(200)
        self.send_header("Set-Cookie", auth.session_cookie_header("", secure=self._secure_cookie(), clear=True))
        data = b'{"ok":true}'
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/health", "/healthz"}:
                return json_response(self, {"ok": True, "service": "hermes-harness"})
            if parsed.path == "/api/harness/auth/status":
                return json_response(self, {
                    "authConfigured": auth.password_configured(),
                    "authenticated": self._is_authenticated(),
                })
            if parsed.path == "/login":
                if not auth.password_configured() or self._is_authenticated():
                    return self._redirect("/harness")
                return self._html(_LOGIN_HTML)

            if not self._require_auth(api=parsed.path.startswith("/api/")):
                return
            if parsed.path == "/api/harness/csrf":
                cookie = auth.parse_cookie(self)
                token = auth.csrf_token(cookie) if cookie else ""
                return json_response(self, {"csrfToken": token})
            if serve_harness_asset(self, parsed.path):
                return
            if handle_harness_request(self, parsed, method="GET"):
                return
            return json_response(self, {"error": "Harness route not found"}, status=404)
        except CLIENT_DISCONNECT_ERRORS:
            return
        except Exception:
            traceback.print_exc()
            try:
                json_response(self, {"error": "Internal Harness server error"}, status=500)
            except CLIENT_DISCONNECT_ERRORS:
                pass

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/harness/auth/login":
                return self._login()
            if not self._require_auth(api=True):
                return
            if auth.password_configured() and not auth.check_csrf(self):
                return json_response(self, {"error": "Invalid CSRF token", "code": "csrf_rejected"}, status=403)
            if parsed.path == "/api/harness/auth/logout":
                return self._logout()
            if not parsed.path.startswith("/api/harness"):
                return json_response(self, {"error": "Harness route not found"}, status=404)
            if handle_harness_request(self, parsed, method="POST"):
                return
            return json_response(self, {"error": "Harness route not found"}, status=404)
        except CLIENT_DISCONNECT_ERRORS:
            return
        except Exception:
            traceback.print_exc()
            try:
                json_response(self, {"error": "Internal Harness server error"}, status=500)
            except CLIENT_DISCONNECT_ERRORS:
                pass


def main() -> None:
    host, port = resolve_bind()
    httpd = ThreadingHTTPServer((host, port), HarnessHandler)
    stop_requested = threading.Event()

    def request_shutdown(_signum, _frame):
        if stop_requested.is_set():
            return
        stop_requested.set()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    try:
        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)
    except (ValueError, OSError):
        pass

    print(f"Hermes Harness listening on http://{host}:{port}/harness", flush=True)
    print("Backend: canonical Hermes API (server-side authenticated BFF)", flush=True)
    print(f"Harness auth state: {auth.state_dir()}", flush=True)
    if host not in _LOOPBACK_HOSTS:
        print("Remote/LAN bind explicitly enabled with Harness password authentication", flush=True)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
