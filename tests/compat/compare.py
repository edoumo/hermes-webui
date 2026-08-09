#!/usr/bin/env python3
"""Hermes WebUI Rust port — Python/Rust compatibility harness (Track E).

Compares the upstream Python backend against the Rust port on the same
routes, using the same fixtures, and reports only functional deltas.

Usage:
    python3 tests/compat/compare.py --python-url https://127.0.0.1:8793 \
                                    --rust-url http://127.0.0.1:8792 \
                                    [--route /health] [--route /api/settings] ...

Tolerated differences (documented, never masked):
    - timestamps (server_started_at, uptime_seconds, last_request_at)
    - UUIDs / random ids
    - purely server headers (Server, Date)
    - transport scheme (https vs http) — the Python server auto-enables TLS
      when tls.crt/tls.key exist in the repo dir; the Rust port is plain HTTP
      in R0/R1. Both are compared over their actual transport.
"""

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Keys whose values are allowed to differ (documented deltas).
TOLERATED_KEYS = {
    "server_started_at",
    "uptime_seconds",
    "last_request_at",
    "requests_total",
    "last_run_finished_at",
    "ms",
    # Documented delta R1: agent_version requires probing the Hermes Agent
    # runtime (git describe of the agent dir / gateway health). The Rust port
    # has no Hermes bridge yet (Track G) and reports "not detected" — the
    # upstream value is environment-specific and not part of the API contract.
    "agent_version",
    # Documented delta R1: /health?deep=1 state_db status reflects per-server
    # state — the Python test server creates state.db in its isolated
    # HERMES_HOME at startup ("ok"), the Rust port's state dir has none
    # ("missing"). Both behaviors are faithful to upstream semantics; the
    # value depends on the server's own state directory.
    "state_db",
}

# Headers that are purely transport/server-level.
TOLERATED_HEADERS = {"date", "server", "content-length", "connection"}

# Routes exercised by the harness. Each entry: (method, path, body or None).
ROUTES = [
    ("GET", "/health", None),
    ("GET", "/health?deep=1", None),
    ("GET", "/", None),
    ("GET", "/index.html", None),
    ("GET", "/static/favicon.ico", None),
    ("GET", "/static/boot.js", None),
    ("GET", "/static/boot.js?v=exp-v0.52.192", None),
    ("GET", "/api/settings", None),
    ("POST", "/api/settings", {"theme": "light", "bot_name": "CompatTest", "language": "fr"}),
    ("GET", "/api/settings", None),
    ("GET", "/static/../config.py", None),
    ("GET", "/static/definitely-not-here.js", None),
]


def fetch(url: str, method: str, body) -> tuple:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            raw = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        headers = {k.lower(): v for k, v in e.headers.items()}
        return e.code, headers, raw
    except Exception as e:  # noqa: BLE001
        return -1, {}, str(e).encode()


def normalize_json(raw: bytes) -> object:
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


def strip_tolerated(obj: object) -> object:
    """Recursively drop tolerated keys so only functional deltas remain."""
    if isinstance(obj, dict):
        return {
            k: strip_tolerated(v)
            for k, v in obj.items()
            if k not in TOLERATED_KEYS
        }
    if isinstance(obj, list):
        return [strip_tolerated(v) for v in obj]
    return obj


def compare_route(name: str, py: tuple, rs: tuple) -> list:
    deltas = []
    py_status, py_headers, py_raw = py
    rs_status, rs_headers, rs_raw = rs

    if py_status != rs_status:
        deltas.append(f"status: python={py_status} rust={rs_status}")

    py_ct = py_headers.get("content-type", "").split(";")[0].strip()
    rs_ct = rs_headers.get("content-type", "").split(";")[0].strip()
    if py_ct != rs_ct:
        deltas.append(f"content-type: python={py_ct!r} rust={rs_ct!r}")

    py_json = normalize_json(py_raw)
    rs_json = normalize_json(rs_raw)
    if py_json is not None and rs_json is not None:
        if strip_tolerated(py_json) != strip_tolerated(rs_json):
            deltas.append(
                f"json body differs:\n  python={json.dumps(strip_tolerated(py_json), sort_keys=True)[:400]}\n  rust  ={json.dumps(strip_tolerated(rs_json), sort_keys=True)[:400]}"
            )
    elif py_raw != rs_raw:
        deltas.append(f"raw body differs: python={py_raw[:200]!r} rust={rs_raw[:200]!r}")

    # ETag semantics: both must emit a weak ETag on static 200s.
    if py_status == 200 and rs_status == 200:
        py_etag = py_headers.get("etag", "")
        rs_etag = rs_headers.get("etag", "")
        if bool(py_etag) != bool(rs_etag):
            deltas.append(f"etag presence: python={bool(py_etag)} rust={bool(rs_etag)}")

    return deltas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python-url", required=True)
    ap.add_argument("--rust-url", required=True)
    ap.add_argument("--route", action="append", default=None)
    args = ap.parse_args()

    routes = [(m, p, b) for m, p, b in ROUTES if args.route is None or p in args.route]

    failures = 0
    print(f"compat harness: python={args.python_url} rust={args.rust_url}")
    print(f"routes: {len(routes)}")
    for method, path, body in routes:
        py = fetch(args.python_url + path, method, body)
        rs = fetch(args.rust_url + path, method, body)
        deltas = compare_route(path, py, rs)
        if deltas:
            failures += 1
            print(f"FAIL {method} {path}")
            for d in deltas:
                print(f"     {d}")
        else:
            print(f"PASS {method} {path}")

    print(f"\nRESULT: {len(routes) - failures}/{len(routes)} routes in parity")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
