"""Standalone password/session/CSRF authentication for Hermes Harness.

Harness intentionally owns this small surface instead of importing the much
larger legacy WebUI auth stack. Password configuration is Harness-specific and
may come from HERMES_HARNESS_PASSWORD. Session/signing artifacts live only in
HERMES_HARNESS_STATE_DIR.
"""
from __future__ import annotations

import hashlib
import hmac
import http.cookies
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

COOKIE_NAME = "hermes_harness_session"
CSRF_HEADER = "X-Hermes-CSRF-Token"
SESSION_TTL = 86400 * 30
_LOCK = threading.Lock()


def state_dir() -> Path:
    raw = str(os.environ.get("HERMES_HARNESS_STATE_DIR", "")).strip()
    return Path(raw).expanduser() if raw else Path.home() / ".hermes" / "harness"


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _signing_key() -> bytes:
    path = state_dir() / ".signing_key"
    try:
        raw = path.read_bytes()
        if len(raw) >= 32:
            return raw[:32]
    except OSError:
        pass
    key = secrets.token_bytes(32)
    _atomic_write(path, key)
    return key


def _session_file() -> Path:
    return state_dir() / ".sessions.json"


def _load_sessions() -> dict[str, float]:
    try:
        data = json.loads(_session_file().read_text(encoding="utf-8"))
    except Exception:
        return {}
    now = time.time()
    if not isinstance(data, dict):
        return {}
    result: dict[str, float] = {}
    for token, expiry in data.items():
        try:
            expiry_f = float(expiry)
        except (TypeError, ValueError):
            continue
        if isinstance(token, str) and token and expiry_f > now:
            result[token] = expiry_f
    return result


def _save_sessions(sessions: dict[str, float]) -> None:
    _atomic_write(
        _session_file(),
        json.dumps(sessions, separators=(",", ":")).encode("utf-8"),
    )


def password_configured() -> bool:
    return bool(str(os.environ.get("HERMES_HARNESS_PASSWORD", "")).strip())


def verify_password(password: str) -> bool:
    expected = str(os.environ.get("HERMES_HARNESS_PASSWORD", ""))
    return bool(expected) and hmac.compare_digest(str(password), expected)


def create_session() -> str:
    token = secrets.token_hex(32)
    expiry = time.time() + SESSION_TTL
    with _LOCK:
        sessions = _load_sessions()
        sessions[token] = expiry
        _save_sessions(sessions)
    signature = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{signature}"


def verify_session(value: str) -> bool:
    if not value or "." not in value:
        return False
    token, signature = value.rsplit(".", 1)
    expected = hmac.new(_signing_key(), token.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False
    with _LOCK:
        sessions = _load_sessions()
        expiry = sessions.get(token)
        if expiry is None or expiry <= time.time():
            sessions.pop(token, None)
            _save_sessions(sessions)
            return False
    return True


def delete_session(value: str) -> None:
    token = value.rsplit(".", 1)[0] if value and "." in value else ""
    if not token:
        return
    with _LOCK:
        sessions = _load_sessions()
        if token in sessions:
            sessions.pop(token, None)
            _save_sessions(sessions)


def parse_cookie(handler) -> str:
    raw = str(handler.headers.get("Cookie") or "")
    if not raw:
        return ""
    cookie = http.cookies.SimpleCookie()
    try:
        cookie.load(raw)
    except http.cookies.CookieError:
        return ""
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else ""


def csrf_token(session_cookie: str) -> str:
    if not session_cookie:
        return ""
    return hmac.new(
        _signing_key(),
        ("csrf:" + session_cookie).encode(),
        hashlib.sha256,
    ).hexdigest()


def check_csrf(handler) -> bool:
    cookie = parse_cookie(handler)
    supplied = str(handler.headers.get(CSRF_HEADER) or "")
    expected = csrf_token(cookie)
    return bool(cookie and expected and hmac.compare_digest(supplied, expected))


def authenticated(handler) -> bool:
    if not password_configured():
        return True
    return verify_session(parse_cookie(handler))


def session_cookie_header(value: str, *, secure: bool = False, clear: bool = False) -> str:
    cookie = http.cookies.SimpleCookie()
    cookie[COOKIE_NAME] = "" if clear else value
    morsel = cookie[COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Strict"
    if secure:
        morsel["secure"] = True
    if clear:
        morsel["max-age"] = 0
    else:
        morsel["max-age"] = SESSION_TTL
    return morsel.OutputString()
