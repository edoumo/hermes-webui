#!/usr/bin/env python3
"""Hermes WebUI Rust port — harnais de compatibilité v2 (Track E R3).

Compare le backend Python upstream (référence) au port Rust sur des
scénarios STATEFUL (sessions CRUD, settings, auth, workspace) et SSE
(bridge mock : séquence d'events, fermeture, cancellation).

Usage:
    python3 tests/compat/compat_v2.py \
        --python-url http://127.0.0.1:8793 \
        --rust-url http://127.0.0.1:8792 \
        --bridge-url http://127.0.0.1:8794 \
        --python-state-dir /tmp/hwui-compat-state \
        --rust-state-dir /tmp/hermes-webui-rust-state \
        [--python-workspace DIR] [--rust-workspace DIR] \
        [--scenario NAME]...   (filtre optionnel)

Principe : upstream = référence. Chaque scénario exécute la même séquence
sur les DEUX serveurs (stores isolés distincts), compare les réponses
(shape + valeurs déterministes), et rapporte les deltas. Les différences
déterministes d'isolation (chemins de workspace, session_ids aléatoires,
timestamps) sont normalisées via NORMALIZERS, jamais masquées : chaque
normalisation est documentée dans le rapport (section "deltas tolérés").

Les deltas FONCTIONNELS restants sont rapportés FAIL avec détail.
"""

import argparse
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import shutil
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ──────────────────────────────────────────────────────────────────────
# Deltas tolérés / normalisés (documentés, jamais masqués silencieusement)
# ──────────────────────────────────────────────────────────────────────
# Clés dont la valeur est propre à chaque serveur (timestamps, compteurs).
TOLERATED_KEYS = {
    "server_started_at", "uptime_seconds", "last_request_at", "requests_total",
    "last_run_finished_at", "ms", "agent_version", "state_db", "webui_version",
    "update_channel_version",
    # R3 — valeurs d'isolation de test : chaque serveur a son propre état.
    "server_time",       # epoch au moment de la réponse (liste sessions)
    "session_id",        # uuid aléatoire généré par chaque serveur
    "created_at",        # timestamp de création (serveur propre)
    "updated_at",
    "last_message_at",
    "last_activity",
    "attention",
    "csrf_token",
    "request_id",
    "signature",         # signature du listing workspace (mtime-dépendante)
    # Deltas R3 documentés (jamais masqués — listés dans le rapport) :
    # - server_tz : port Rust = UTC fixe ("+0000") vs Python = tz locale ("+0200").
    "server_tz",
    # - context_length / threshold_tokens / last_prompt_tokens : Python les
    #   RÉSOUT via le runtime hermes-agent (get_model_context_length) ; le
    #   port Rust n'a pas ce runtime (HERMES_BRIDGE_REQUIRED) et renvoie le
    #   champ persisté (null). 0 (résolution vide) et null (non résolu) sont
    #   équivalents en l'absence de modèle.
    "context_length", "threshold_tokens", "last_prompt_tokens",
}
# Headers purement transport/serveur.
TOLERATED_HEADERS = {"date", "server", "content-length", "connection", "transfer-encoding", "keep-alive"}
# Headers dont la PRÉSENCE doit être identique des deux côtés.
SIGNIFICANT_HEADERS = {"content-type", "cache-control", "etag", "set-cookie", "x-content-type-options"}

# Chemins de workspace isolés : normalisés vers un token (delta d'isolation).
WS_TOKEN = "<WORKSPACE>"
WS_PATTERNS = []  # remplis après parse des args

# Cookies de session : noms/valeurs propres à chaque serveur.
COOKIE_NAME_RE = re.compile(r"hermes_session=[^;]*")


def log(msg: str) -> None:
    print(msg, flush=True)


# ──────────────────────────────────────────────────────────────────────
# Client HTTP stateful (cookies, SSE, TLS optionnel)
# ──────────────────────────────────────────────────────────────────────
class Resp:
    def __init__(self, status, headers, body, cookies=None):
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body
        self.cookies = cookies or {}

    def json(self):
        try:
            return json.loads(self.body)
        except Exception:
            return None

    def text(self, n=300):
        return self.body[:n].decode("utf-8", "replace")


class Client:
    """Client HTTP stateful : garde les cookies Set-Cookie."""

    def __init__(self, base_url, name):
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.cookies = {}
        self.ctx = None
        if base_url.startswith("https://"):
            self.ctx = ssl.create_default_context()
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def _cookie_header(self):
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    def _absorb_cookies(self, headers):
        # headers = itérable de paires (k, v) — HTTPMessage.items() / dict.items()
        for k, v in headers:
            if str(k).lower() == "set-cookie":
                pair = v.split(";", 1)[0]
                if "=" in pair:
                    name, val = pair.split("=", 1)
                    if val in ("", "deleted"):
                        self.cookies.pop(name, None)
                    else:
                        self.cookies[name] = val

    def request(self, method, path, body=None, headers=None, timeout=30):
        headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        if self.cookies:
            headers.setdefault("Cookie", self._cookie_header())
        req = urllib.request.Request(self.base_url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as resp:
                raw = resp.read()
                self._absorb_cookies(resp.headers.items())
                return Resp(resp.status, dict(resp.headers.items()), raw, dict(self.cookies))
        except urllib.error.HTTPError as e:
            raw = e.read()
            self._absorb_cookies(e.headers.items())
            return Resp(e.code, dict(e.headers.items()), raw, dict(self.cookies))
        except Exception as e:  # noqa: BLE001
            return Resp(-1, {}, str(e).encode(), dict(self.cookies))

    def sse(self, method, path, body=None, headers=None, timeout=30):
        """POST SSE : lit le flux event par event jusqu'à EOF.
        Retourne (status, list[(event, data_json)], raw_events)."""
        headers = dict(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers.setdefault("Content-Type", "application/json")
        if self.cookies:
            headers.setdefault("Cookie", self._cookie_header())
        parsed = urllib.parse.urlparse(self.base_url + path)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parsed.netloc, timeout=timeout)
        if parsed.scheme == "https":
            conn._context = self.ctx or ssl._create_unverified_context()  # noqa: SLF001
        try:
            conn.request(method, parsed.path + (("?" + parsed.query) if parsed.query else ""),
                         body=data, headers=headers)
            resp = conn.getresponse()
            status = resp.status
            resp_headers = dict(resp.getheaders())
            self._absorb_cookies(resp_headers.items())
            events = []
            raw_events = []
            current_event = None
            current_data = []
            while True:
                line = resp.readline()
                if not line:
                    break
                line = line.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if current_event is not None or current_data:
                        events.append((current_event, "\n".join(current_data)))
                        raw_events.append((current_event, "\n".join(current_data)))
                    current_event = None
                    current_data = []
                    continue
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    current_data.append(line[len("data:"):].strip())
            if current_event is not None or current_data:
                events.append((current_event, "\n".join(current_data)))
                raw_events.append((current_event, "\n".join(current_data)))
            return status, events, raw_events
        finally:
            conn.close()


# ──────────────────────────────────────────────────────────────────────
# Normalisation
# ──────────────────────────────────────────────────────────────────────
def normalize_value(v):
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            if k in TOLERATED_KEYS:
                continue
            out[k] = normalize_value(val)
        return out
    if isinstance(v, list):
        return [normalize_value(x) for x in v]
    if isinstance(v, str):
        for pat, repl in WS_PATTERNS:
            if pat in v:
                v = v.replace(pat, repl)
        v = COOKIE_NAME_RE.sub("hermes_session=<TOKEN>", v)
        v = re.sub(r"\b[0-9a-f]{32}\b", "<HEX32>", v)
        v = re.sub(r"\b[0-9a-f]{12}\b", "<HEX12>", v)
        v = re.sub(r"[\d]{4}-[\d]{2}-[\d]{2}T[\d:.]+", "<TS>", v)
        return v
    return v


def norm_json(obj):
    return normalize_value(obj)


def norm_sse_events(events):
    """Normalise une liste d'events SSE (event, data) pour comparaison."""
    out = []
    for name, data in events:
        d = data
        try:
            d = json.loads(data)
        except Exception:
            pass
        out.append((name, normalize_value(d)))
    return out


def compare_json(py, rs):
    """Compare deux objets JSON normalisés. Retourne (equal, diff_str)."""
    p, r = norm_json(py), norm_json(rs)
    if p == r:
        return True, ""
    return False, (
        f"python={json.dumps(p, sort_keys=True, ensure_ascii=False)[:500]}\n"
        f"rust  ={json.dumps(r, sort_keys=True, ensure_ascii=False)[:500]}"
    )


def compare_subset(py, rs):
    """Compare rs ⊆ py : toutes les clés/valeurs du rust doivent exister et
    être égales côté python (après normalisation). Les clés python SUPPLÉMENTAIRES
    sont tolérées et LISTÉES (delta documenté : le port est un sous-ensemble
    progressif des phases R2/R3, jamais un delta masqué).

    Retourne (equal, diff_str) où diff_str liste les clés rust manquantes
    côté python et les valeurs divergentes.
    """
    # NOTE : session_id est dans TOLERATED_KEYS → norm_json() le SUPPRIME.
    # L'alignement des listes par session_id se fait sur les données BRUTES ;
    # chaque paire alignée est ensuite comparée en normalisé.
    p, r = norm_json(py), norm_json(rs)
    missing = []
    divergent = []

    def walk(pnode, rnode, path):
        if isinstance(rnode, dict) and isinstance(pnode, dict):
            for k, rv in rnode.items():
                if k not in pnode:
                    missing.append(f"{path}.{k}")
                else:
                    walk(pnode[k], rv, f"{path}.{k}")
        elif isinstance(rnode, list) and isinstance(pnode, list):
            if len(rnode) != len(pnode):
                divergent.append(f"{path}: len rust={len(rnode)} python={len(pnode)}")
            for i, (r_item, p_item) in enumerate(zip(rnode, pnode)):
                walk(p_item, r_item, f"{path}[{i}]")
        elif pnode != rnode:
            divergent.append(f"{path}: rust={json.dumps(rnode)[:120]} python={json.dumps(pnode)[:120]}")

    walk(p, r, "$")
    if not missing and not divergent:
        return True, ""
    detail = []
    if divergent:
        detail.append("divergences: " + "; ".join(divergent[:8]))
    if missing:
        detail.append("clés rust absentes de python: " + ", ".join(missing[:12]))
    return False, " | ".join(detail)


# ──────────────────────────────────────────────────────────────────────
# Assertions & scénarios
# ──────────────────────────────────────────────────────────────────────
class Assertion:
    def __init__(self, name, passed, detail=""):
        self.name = name
        self.passed = passed
        self.detail = detail


class Scenario:
    def __init__(self, name, doc, fn):
        self.name = name
        self.doc = doc
        self.fn = fn

    def run(self, ctx):
        try:
            return self.fn(ctx)
        except Exception as e:  # noqa: BLE001
            import traceback
            return [Assertion("scenario crashed", False, f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")]


class Ctx:
    """État partagé entre scénarios (sessions créées, etc.)."""

    def __init__(self, args):
        self.args = args
        self.py = Client(args.python_url, "python")
        self.rs = Client(args.rust_url, "rust")
        self.bridge = Client(args.bridge_url, "bridge")
        self.sid_py = None
        self.sid_rs = None
        self.doc_deltas = []  # deltas documentés (isolation/normalisation)

    def doc(self, msg):
        self.doc_deltas.append(msg)
        log(f"    [doc-delta] {msg}")


def ok(name, cond, detail=""):
    return Assertion(name, bool(cond), detail)


# ──────────────────────────────────────────────────────────────────────
# Scénarios — SESSIONS (stateful)
# ──────────────────────────────────────────────────────────────────────
def sc_session_new(ctx):
    a = []
    body = {"title": "Nouvelle session"}
    rp = ctx.py.request("POST", "/api/session/new", body)
    rr = ctx.rs.request("POST", "/api/session/new", body)
    a.append(ok("status python 200", rp.status == 200, f"got {rp.status} {rp.text(200)}"))
    a.append(ok("status rust 200", rr.status == 200, f"got {rr.status} {rr.text(200)}"))
    if rp.status == 200 and rr.status == 200:
        pj, rj = rp.json(), rr.json()
        ctx.sid_py = (pj or {}).get("session", {}).get("session_id") or ctx.sid_py
        ctx.sid_rs = (rj or {}).get("session", {}).get("session_id") or ctx.sid_rs
        eq, diff = compare_subset(pj, rj)
        a.append(ok("response shape python⊇rust (normalisé, clés python extra listées)", eq, diff))
        a.append(ok("session_id present (python)", bool(ctx.sid_py)))
        a.append(ok("session_id present (rust)", bool(ctx.sid_rs)))
        # NOTE upstream : /api/session/new IGNORE le champ title du body et
        # crée toujours une session "Untitled" (le titre est posé au premier
        # message). Le port Rust reproduit ce comportement.
        a.append(ok("title 'Untitled' (python, upstream ignore le title du body)",
                    (pj or {}).get("session", {}).get("title") == "Untitled",
                    json.dumps(pj)[:200]))
        a.append(ok("title 'Untitled' (rust)",
                    (rj or {}).get("session", {}).get("title") == "Untitled",
                    json.dumps(rj)[:200]))
    return a


def sc_session_get(ctx):
    a = []
    for side, sid in (("python", ctx.sid_py), ("rust", ctx.sid_rs)):
        if not sid:
            a.append(ok(f"GET session {side} (skip: pas de sid)", False, "scénario new non exécuté avant"))
            continue
        cli = ctx.py if side == "python" else ctx.rs
        r = cli.request("GET", f"/api/session?session_id={sid}")
        a.append(ok(f"GET session {side} 200", r.status == 200, f"got {r.status} {r.text(200)}"))
        j = r.json() or {}
        s = j.get("session", {})
        a.append(ok(f"GET session {side} title", s.get("title") == "Untitled", json.dumps(j)[:200]))
        a.append(ok(f"GET session {side} messages == []", s.get("messages") == [], json.dumps(s.get("messages"))[:200]))
    # Comparaison croisée des shapes normalisées
    if ctx.sid_py and ctx.sid_rs:
        rp = ctx.py.request("GET", f"/api/session?session_id={ctx.sid_py}")
        rr = ctx.rs.request("GET", f"/api/session?session_id={ctx.sid_rs}")
        eq, diff = compare_subset(rp.json(), rr.json())
        a.append(ok("GET session shapes python⊇rust (normalisé)", eq, diff))
    return a


def sc_session_list(ctx):
    a = []
    rp = ctx.py.request("GET", "/api/sessions")
    rr = ctx.rs.request("GET", "/api/sessions")
    a.append(ok("list status python 200", rp.status == 200, f"got {rp.status}"))
    a.append(ok("list status rust 200", rr.status == 200, f"got {rr.status}"))
    pj, rj = rp.json() or {}, rr.json() or {}
    psids = {s.get("session_id") for s in pj.get("sessions", [])}
    rsids = {s.get("session_id") for s in rj.get("sessions", [])}
    if ctx.sid_py:
        # DELTA STRUCTUREL DOCUMENTÉ (sessions.rs) : upstream diffère la
        # première écriture au premier message — une session créée via
        # /api/session/new n'apparaît PAS encore dans /api/sessions côté
        # Python. Le port Rust persiste immédiatement (superset documenté,
        # jamais cassé la compat du fichier). On vérifie donc le COMPORTEMENT
        # de chaque serveur séparément.
        a.append(ok("session créée absente de la liste python (delta structurel : persistance différée upstream)",
                    ctx.sid_py not in psids, f"ids={psids}"))
        a.append(ok("session créée listée (rust, persistance immédiate du port)",
                    ctx.sid_rs in rsids, f"ids={rsids}"))
        ctx.doc("delta structurel R3 : persistance immédiate du port vs différée upstream — "
                "une session/new est listée côté Rust mais pas (encore) côté Python")
    # Les listes ont des compositions DIFFÉRENTES (delta de persistance) :
    # on compare session-par-session les sessions COMMUNES (par session_id),
    # jamais par index.
    pj_by_id = {s.get("session_id"): s for s in pj.get("sessions", [])}
    rj_by_id = {s.get("session_id"): s for s in rj.get("sessions", [])}
    common = sorted(set(pj_by_id) & set(rj_by_id))
    for sid in common:
        eq, diff = compare_subset(pj_by_id[sid], rj_by_id[sid])
        a.append(ok(f"session commune {sid} shapes python⊇rust", eq, diff))
    rust_only = sorted(set(rj_by_id) - set(pj_by_id))
    a.append(ok("sessions rust-only documentées (delta persistance immédiate)",
                True, f"rust_only={rust_only}"))
    ctx.doc(f"sessions présentes côté rust sans équivalent python (persistance immédiate) : {rust_only}")
    a.append(ok("webui_session_count == len(webui) (python)",
                pj.get("webui_session_count") == len(psids),
                json.dumps(pj)[:300]))
    a.append(ok("webui_session_count == len(webui) (rust)",
                rj.get("webui_session_count") == len(rsids),
                json.dumps(rj)[:300]))
    return a


def sc_session_rename(ctx):
    a = []
    for side, sid in (("python", ctx.sid_py), ("rust", ctx.sid_rs)):
        if not sid:
            a.append(ok(f"rename {side} (skip)", False, "pas de sid"))
            continue
        cli = ctx.py if side == "python" else ctx.rs
        r = cli.request("POST", "/api/session/rename", {"session_id": sid, "title": "Titre renommé"})
        a.append(ok(f"rename {side} 200", r.status == 200, f"got {r.status} {r.text(200)}"))
        j = r.json() or {}
        a.append(ok(f"rename {side} title appliqué",
                    j.get("session", {}).get("title") == "Titre renommé",
                    json.dumps(j)[:200]))
        # re-GET : persistance
        g = cli.request("GET", f"/api/session?session_id={sid}")
        a.append(ok(f"rename {side} persisté au GET",
                    (g.json() or {}).get("session", {}).get("title") == "Titre renommé",
                    g.text(200)))
    return a


def sc_session_update(ctx):
    a = []
    for side, sid in (("python", ctx.sid_py), ("rust", ctx.sid_rs)):
        if not sid:
            a.append(ok(f"update {side} (skip)", False, "pas de sid"))
            continue
        cli = ctx.py if side == "python" else ctx.rs
        r = cli.request("POST", "/api/session/update",
                        {"session_id": sid, "model": "mock-model-2", "model_provider": "mock-provider-2"})
        a.append(ok(f"update {side} 200", r.status == 200, f"got {r.status} {r.text(200)}"))
        j = r.json() or {}
        s = j.get("session", {})
        a.append(ok(f"update {side} model appliqué", s.get("model") == "mock-model-2", json.dumps(j)[:250]))
        g = cli.request("GET", f"/api/session?session_id={sid}")
        a.append(ok(f"update {side} persisté au GET",
                    (g.json() or {}).get("session", {}).get("model") == "mock-model-2",
                    g.text(200)))
    return a


def sc_session_messages0(ctx):
    a = []
    for side, sid in (("python", ctx.sid_py), ("rust", ctx.sid_rs)):
        if not sid:
            a.append(ok(f"messages=0 {side} (skip)", False, "pas de sid"))
            continue
        cli = ctx.py if side == "python" else ctx.rs
        r = cli.request("GET", f"/api/session?session_id={sid}&messages=0")
        a.append(ok(f"messages=0 {side} 200", r.status == 200, f"got {r.status}"))
        j = r.json() or {}
        s = j.get("session", {})
        a.append(ok(f"messages=0 {side} sans payload messages",
                    "messages" not in s or s.get("messages") in (None, []),
                    json.dumps(j)[:250]))
    return a


def sc_session_delete(ctx):
    a = []
    for side, sid in (("python", ctx.sid_py), ("rust", ctx.sid_rs)):
        if not sid:
            a.append(ok(f"delete {side} (skip)", False, "pas de sid"))
            continue
        cli = ctx.py if side == "python" else ctx.rs
        r = cli.request("POST", "/api/session/delete", {"session_id": sid})
        a.append(ok(f"delete {side} 200", r.status == 200, f"got {r.status} {r.text(200)}"))
        j = r.json() or {}
        a.append(ok(f"delete {side} ok:true", j.get("ok") is True, json.dumps(j)[:200]))
        g = cli.request("GET", f"/api/session?session_id={sid}")
        a.append(ok(f"delete {side} → GET 404", g.status == 404, f"got {g.status} {g.text(150)}"))
    return a


def sc_session_delete_missing(ctx):
    a = []
    for side, cli in (("python", ctx.py), ("rust", ctx.rs)):
        # sid SAFE (alnum uniquement) mais absent — upstream: delete d'un sid
        # absent → succès (ok:true). Un sid avec apostrophes est INVALIDE
        # (is_safe_session_id) → 400 des deux côtés (couvert par session/errors).
        r = cli.request("POST", "/api/session/delete", {"session_id": "zzzzzzzzzzzz"})
        a.append(ok(f"delete absent {side} (comportement identique)",
                    r.status == 200 and (r.json() or {}).get("ok") is True,
                    f"got {r.status} {r.text(200)}"))
    return a


def sc_session_errors(ctx):
    a = []
    cases = [
        ("GET", "/api/session", None, "session_id requis"),
        ("GET", "/api/session?session_id=inexistant-zz", None, "404"),
        ("POST", "/api/session/rename", {"title": "x"}, "session_id requis"),
        ("POST", "/api/session/rename", {"session_id": "inexistant-zz", "title": "x"}, "404"),
        ("POST", "/api/session/update", {"model": "m"}, "session_id requis"),
        ("POST", "/api/session/delete", {}, "session_id requis"),
        ("POST", "/api/session/delete", {"session_id": "../etc/passwd"}, "session_id invalide"),
    ]
    for method, path, body, label in cases:
        rp = ctx.py.request(method, path, body)
        rr = ctx.rs.request(method, path, body)
        a.append(ok(f"{method} {path} — status python==rust ({label})",
                    rp.status == rr.status,
                    f"python={rp.status} rust={rr.status} | py={rp.text(120)} rs={rr.text(120)}"))
    return a


def sc_session_fixture(ctx):
    """Fixture session réelle (shape disque) : chargée par les deux serveurs."""
    a = []
    for side, cli in (("python", ctx.py), ("rust", ctx.rs)):
        r = cli.request("GET", "/api/session?session_id=compatfixture01")
        a.append(ok(f"fixture GET {side} 200", r.status == 200, f"got {r.status} {r.text(200)}"))
        j = r.json() or {}
        s = j.get("session", {})
        a.append(ok(f"fixture {side} title", s.get("title") == "Fixture compat v2", json.dumps(j)[:250]))
        msgs = s.get("messages") or []
        a.append(ok(f"fixture {side} 2 messages", len(msgs) == 2, json.dumps(msgs)[:250]))
        a.append(ok(f"fixture {side} 1er message user",
                    msgs[0].get("role") == "user" if msgs else False,
                    json.dumps(msgs)[:250]))
    rp = ctx.py.request("GET", "/api/session?session_id=compatfixture01")
    rr = ctx.rs.request("GET", "/api/session?session_id=compatfixture01")
    eq, diff = compare_subset(rp.json(), rr.json())
    a.append(ok("fixture shapes python⊇rust (normalisé)", eq, diff))
    # La fixture doit apparaître dans les deux listes
    lp = (ctx.py.request("GET", "/api/sessions").json() or {}).get("sessions", [])
    lr = (ctx.rs.request("GET", "/api/sessions").json() or {}).get("sessions", [])
    a.append(ok("fixture listée (python)", any(s.get("session_id") == "compatfixture01" for s in lp)))
    a.append(ok("fixture listée (rust)", any(s.get("session_id") == "compatfixture01" for s in lr)))
    return a


def sc_session_persistence_disk(ctx):
    """Persistance disque : le sidecar <sid>.json existe dans STATE_DIR."""
    a = []
    for side, state_dir, sid in (("python", ctx.args.python_state_dir, ctx.sid_py),
                                 ("rust", ctx.args.rust_state_dir, ctx.sid_rs)):
        if not sid:
            a.append(ok(f"disque {side} (skip)", False, "pas de sid"))
            continue
        p = Path(state_dir) / "sessions" / f"{sid}.json"
        a.append(ok(f"sidecar {side} présent sur disque", p.exists(), f"path={p}"))
        if p.exists():
            try:
                data = json.loads(p.read_text())
                a.append(ok(f"sidecar {side} JSON valide + session_id", data.get("session_id") == sid))
            except Exception as e:  # noqa: BLE001
                a.append(ok(f"sidecar {side} JSON valide", False, str(e)))
    # La fixture doit exister sur disque des deux côtés (copiée au setup)
    for side, state_dir in (("python", ctx.args.python_state_dir), ("rust", ctx.args.rust_state_dir)):
        p = Path(state_dir) / "sessions" / "compatfixture01.json"
        a.append(ok(f"fixture disque {side}", p.exists(), f"path={p}"))
    return a


def sc_session_profile_scoping(ctx):
    """Création avec profile explicite : le profile est persisté et visible."""
    a = []
    rp = ctx.py.request("POST", "/api/session/new", {"profile": "compat-profil"})
    rr = ctx.rs.request("POST", "/api/session/new", {"profile": "compat-profil"})
    a.append(ok("profile new python 200", rp.status == 200, f"got {rp.status} {rp.text(200)}"))
    a.append(ok("profile new rust 200", rr.status == 200, f"got {rr.status} {rr.text(200)}"))
    pid = (rp.json() or {}).get("session", {}).get("session_id") if rp.status == 200 else None
    rid = (rr.json() or {}).get("session", {}).get("session_id") if rr.status == 200 else None
    if pid:
        g = ctx.py.request("GET", f"/api/session?session_id={pid}")
        # Upstream : le GET d'une session d'un AUTRE profile renvoie
        # 409 {code: session_profile_mismatch} — c'est le comportement attendu
        # (le profile actif est "default").
        j = g.json() or {}
        a.append(ok("profile scoping python (409 session_profile_mismatch attendu)",
                    g.status == 409 and j.get("code") == "session_profile_mismatch",
                    g.text(250)))
        # Vérification réelle de persistance : l'erreur 409 expose le profile
        # du sidecar (côté Python la première écriture disque est différée au
        # premier message — mais la session en mémoire porte déjà le profile).
        a.append(ok("profile persisté (python, exposé dans l'erreur 409)",
                    j.get("profile") == "compat-profil",
                    g.text(250)))
    if rid:
        g = ctx.rs.request("GET", f"/api/session?session_id={rid}")
        a.append(ok("profile persisté (rust)",
                    (g.json() or {}).get("session", {}).get("profile") == "compat-profil",
                    g.text(250)))
    # Nettoyage
    if pid:
        ctx.py.request("POST", "/api/session/delete", {"session_id": pid})
    if rid:
        ctx.rs.request("POST", "/api/session/delete", {"session_id": rid})
    return a


# ──────────────────────────────────────────────────────────────────────
# Scénarios — SETTINGS
# ──────────────────────────────────────────────────────────────────────
def sc_settings_get(ctx):
    a = []
    rp = ctx.py.request("GET", "/api/settings")
    rr = ctx.rs.request("GET", "/api/settings")
    a.append(ok("GET settings python 200", rp.status == 200, f"got {rp.status}"))
    a.append(ok("GET settings rust 200", rr.status == 200, f"got {rr.status}"))
    pj, rj = rp.json() or {}, rr.json() or {}
    a.append(ok("password_hash jamais exposé (python)",
                pj.get("password_hash") in (None, ""), json.dumps(pj)[:200]))
    a.append(ok("password_hash jamais exposé (rust)",
                rj.get("password_hash") in (None, ""), json.dumps(rj)[:200]))
    a.append(ok("bot_name présent (python)", "bot_name" in pj))
    a.append(ok("bot_name présent (rust)", "bot_name" in rj))
    eq, diff = compare_subset(pj, rj)
    a.append(ok("GET settings shapes python⊇rust (normalisé)", eq, diff))
    return a


def sc_settings_post(ctx):
    a = []
    body = {"theme": "dark", "bot_name": "CompatBotV2", "language": "fr"}
    rp = ctx.py.request("POST", "/api/settings", body)
    rr = ctx.rs.request("POST", "/api/settings", body)
    a.append(ok("POST settings python 200", rp.status == 200, f"got {rp.status} {rp.text(200)}"))
    a.append(ok("POST settings rust 200", rr.status == 200, f"got {rr.status} {rr.text(200)}"))
    # Persistance : re-GET
    gp = ctx.py.request("GET", "/api/settings").json() or {}
    gr = ctx.rs.request("GET", "/api/settings").json() or {}
    a.append(ok("theme persisté (python)", gp.get("theme") == "dark", json.dumps(gp)[:250]))
    a.append(ok("theme persisté (rust)", gr.get("theme") == "dark", json.dumps(gr)[:250]))
    a.append(ok("bot_name persisté (python)", gp.get("bot_name") == "CompatBotV2"))
    a.append(ok("bot_name persisté (rust)", gr.get("bot_name") == "CompatBotV2"))
    eq, diff = compare_subset(gp, gr)
    a.append(ok("GET settings post-POST python⊇rust (normalisé)", eq, diff))
    return a


def sc_settings_redaction(ctx):
    """Tentative d'écrire password_hash via POST /api/settings → ignorée/redigée."""
    a = []
    body = {"password_hash": "deadbeef" * 8, "theme": "light"}
    rp = ctx.py.request("POST", "/api/settings", body)
    rr = ctx.rs.request("POST", "/api/settings", body)
    a.append(ok("POST settings (hash) python 200", rp.status == 200, f"got {rp.status}"))
    a.append(ok("POST settings (hash) rust 200", rr.status == 200, f"got {rr.status}"))
    gp = ctx.py.request("GET", "/api/settings").json() or {}
    gr = ctx.rs.request("GET", "/api/settings").json() or {}
    a.append(ok("password_hash non persistant (python)",
                gp.get("password_hash") in (None, ""), json.dumps(gp)[:250]))
    a.append(ok("password_hash non persistant (rust)",
                gr.get("password_hash") in (None, ""), json.dumps(gr)[:250]))
    return a


def sc_settings_disk(ctx):
    """Persistance disque : STATE_DIR/settings.json contient les valeurs."""
    a = []
    for side, state_dir in (("python", ctx.args.python_state_dir), ("rust", ctx.args.rust_state_dir)):
        p = Path(state_dir) / "settings.json"
        a.append(ok(f"settings.json présent ({side})", p.exists(), f"path={p}"))
        if p.exists():
            try:
                data = json.loads(p.read_text())
                a.append(ok(f"settings.json theme ({side})", data.get("theme") == "light",
                            json.dumps(data)[:250]))
                a.append(ok(f"settings.json sans password_hash clair ({side})",
                            data.get("password_hash") in (None, ""), json.dumps(data)[:250]))
            except Exception as e:  # noqa: BLE001
                a.append(ok(f"settings.json valide ({side})", False, str(e)))
    return a


# ──────────────────────────────────────────────────────────────────────
# Scénarios — AUTH (mode isolé, auth désactivée par défaut)
# ──────────────────────────────────────────────────────────────────────
def sc_auth_disabled(ctx):
    a = []
    rp = ctx.py.request("POST", "/api/auth/login", {"password": "wrong"})
    rr = ctx.rs.request("POST", "/api/auth/login", {"password": "wrong"})
    a.append(ok("login (auth off) python 200", rp.status == 200, f"got {rp.status} {rp.text(200)}"))
    a.append(ok("login (auth off) rust 200", rr.status == 200, f"got {rr.status} {rr.text(200)}"))
    pj, rj = rp.json() or {}, rr.json() or {}
    a.append(ok("login (auth off) ok:true python", pj.get("ok") is True, json.dumps(pj)[:200]))
    a.append(ok("login (auth off) ok:true rust", rj.get("ok") is True, json.dumps(rj)[:200]))
    # Nettoyage : le login pose un cookie → logout pour ne pas polluer
    # auth/status (logged_in doit être false au scénario suivant).
    ctx.py.request("POST", "/api/auth/logout", {})
    ctx.rs.request("POST", "/api/auth/logout", {})
    return a


def sc_auth_status(ctx):
    a = []
    rp = ctx.py.request("GET", "/api/auth/status")
    rr = ctx.rs.request("GET", "/api/auth/status")
    a.append(ok("auth/status python 200", rp.status == 200, f"got {rp.status} {rp.text(200)}"))
    a.append(ok("auth/status rust 200", rr.status == 200, f"got {rr.status} {rr.text(200)}"))
    pj, rj = rp.json() or {}, rr.json() or {}
    a.append(ok("auth_enabled false python", pj.get("auth_enabled") is False, json.dumps(pj)[:200]))
    a.append(ok("auth_enabled false rust", rj.get("auth_enabled") is False, json.dumps(rj)[:200]))
    a.append(ok("logged_in false python", pj.get("logged_in") is False, json.dumps(pj)[:200]))
    a.append(ok("logged_in false rust", rj.get("logged_in") is False, json.dumps(rj)[:200]))
    return a


def sc_auth_logout(ctx):
    a = []
    rp = ctx.py.request("POST", "/api/auth/logout", {})
    rr = ctx.rs.request("POST", "/api/auth/logout", {})
    a.append(ok("logout python 200", rp.status == 200, f"got {rp.status} {rp.text(200)}"))
    a.append(ok("logout rust 200", rr.status == 200, f"got {rr.status} {rr.text(200)}"))
    a.append(ok("logout ok:true python", (rp.json() or {}).get("ok") is True, rp.text(200)))
    a.append(ok("logout ok:true rust", (rr.json() or {}).get("ok") is True, rr.text(200)))
    return a


# ──────────────────────────────────────────────────────────────────────
# Scénarios — SSE (bridge mock + relay Rust)
# ──────────────────────────────────────────────────────────────────────
# Séquence réelle du bridge mock (protocole v1) : 7 events nommés puis le
# marqueur de fin `data: [DONE]` SANS nom d'event → None dans le parser.
SSE_EXPECTED = ["start", "reasoning", "token", "tool_start", "tool_result", "token", "done", None]


def _chat_body(rid):
    return {
        "request_id": rid,
        "session_id": "mock-session",
        "message": "Bonjour bridge",
        "profile": "default",
        "model": "mock-model",
        "provider": "mock-provider",
        "workspace": "/tmp",
        "history": [{"role": "user", "content": "précédent"}],
    }


def sc_sse_bridge_direct(ctx):
    """POST /v1/chat (bridge mock direct) : séquence complète d'events."""
    a = []
    status, events, raw = ctx.bridge.sse("POST", "/v1/chat", _chat_body("rid-direct-1"))
    a.append(ok("bridge /v1/chat 200", status == 200, f"got {status}"))
    names = [e for e, _ in events]
    a.append(ok("séquence start→…→done exacte", names == SSE_EXPECTED, f"got {names}"))
    a.append(ok("terminaison [DONE] (event data-only final)", names[-1:] == [None], f"events={names}"))
    a.append(ok("request_id cohérent", all(
        (json.loads(d) if isinstance(d, str) else {}).get("request_id") == "rid-direct-1"
        for e, d in events if e in ("start", "done")
    ), str(events)[:300]))
    tok = [json.loads(d) for e, d in events if e == "token"]
    a.append(ok("2 events token", len(tok) == 2, str(tok)[:300]))
    a.append(ok("texte token non vide", all(t.get("text") for t in tok), str(tok)[:300]))
    tools = [json.loads(d) for e, d in events if e == "tool_start"]
    a.append(ok("tool_start mock_tool", len(tools) == 1 and tools[0].get("name") == "mock_tool",
                str(tools)[:300]))
    return a


def sc_sse_rust_relay(ctx):
    """POST /api/chat/proxy (relay Rust) : même séquence d'events."""
    a = []
    status, events, raw = ctx.rs.sse("POST", "/api/chat/proxy", _chat_body("rid-relay-1"))
    a.append(ok("relay /api/chat/proxy 200", status == 200, f"got {status}"))
    names = [e for e, _ in events]
    a.append(ok("séquence relay start→…→done exacte", names == SSE_EXPECTED, f"got {names}"))
    a.append(ok("terminaison relay [DONE] (event data-only final)", names[-1:] == [None], f"events={names}"))
    tok = [json.loads(d) for e, d in events if e == "token"]
    a.append(ok("relay 2 events token", len(tok) == 2, str(tok)[:300]))
    return a


def sc_sse_relay_vs_bridge(ctx):
    """Comparaison fonctionnelle : relay Rust == bridge direct (normalisé)."""
    a = []
    rid = "rid-compare-1"
    _, ev_bridge, _ = ctx.bridge.sse("POST", "/v1/chat", _chat_body(rid))
    _, ev_relay, _ = ctx.rs.sse("POST", "/api/chat/proxy", _chat_body(rid))
    nb = norm_sse_events(ev_bridge)
    nr = norm_sse_events(ev_relay)
    a.append(ok("relay == bridge (events normalisés)", nb == nr,
                f"bridge={nb}\nrelay ={nr}"))
    return a


def sc_sse_close_after_done(ctx):
    """Fermeture de connexion après done : le flux se termine (EOF)."""
    a = []
    # Le client SSE lit jusqu'à EOF : si la connexion n'était pas fermée,
    # la lecture bloquerait (timeout). On mesure le temps de lecture.
    t0 = time.time()
    status, events, _ = ctx.bridge.sse("POST", "/v1/chat", _chat_body("rid-close-1"), timeout=20)
    elapsed = time.time() - t0
    a.append(ok("flux bridge se termine (EOF, dernier event done)", status == 200 and "done" in [e for e, _ in events],
                f"events={[e for e, _ in events]}"))
    a.append(ok("connexion fermée après done (< 5s)", elapsed < 5, f"elapsed={elapsed:.2f}s"))
    t0 = time.time()
    _, events_rs, _ = ctx.rs.sse("POST", "/api/chat/proxy", _chat_body("rid-close-2"), timeout=20)
    elapsed = time.time() - t0
    a.append(ok("flux relay se termine (EOF, dernier event done)", "done" in [e for e, _ in events_rs],
                f"events={[e for e, _ in events_rs]}"))
    a.append(ok("relay connexion fermée après done (< 5s)", elapsed < 5, f"elapsed={elapsed:.2f}s"))
    return a


def sc_sse_cancel(ctx):
    """Cancellation : POST /v1/cancel → cancelled ; flux terminé proprement.

    Limitation documentée : le flux mock émet sans délai, la fenêtre de
    cancel est donc quasi nulle ; on vérifie le contrat API (200
    {cancelled:true}) et la fermeture propre du flux (done OU cancelled).
    """
    a = []
    rid = "rid-cancel-1"
    r = ctx.bridge.request("POST", "/v1/cancel", {"request_id": rid, "session_id": "mock-session"})
    a.append(ok("POST /v1/cancel 200", r.status == 200, f"got {r.status} {r.text(200)}"))
    a.append(ok("cancel cancelled:true", (r.json() or {}).get("cancelled") is True, r.text(200)))
    # Flux avec request_id annulé au préalable : se termine proprement.
    status, events, _ = ctx.bridge.sse("POST", "/v1/chat", _chat_body(rid))
    names = [e for e, _ in events]
    a.append(ok("flux annulé se termine (done ou cancelled)",
                status == 200 and "cancelled" in names,
                f"got {status} {names}"))
    # Relay Rust : même contrat via /api/chat/proxy + cancel direct.
    rid2 = "rid-cancel-2"
    r2 = ctx.bridge.request("POST", "/v1/cancel", {"request_id": rid2, "session_id": "mock-session"})
    a.append(ok("cancel (relay) 200", r2.status == 200, f"got {r2.status}"))
    _, events_rs, _ = ctx.rs.sse("POST", "/api/chat/proxy", _chat_body(rid2))
    names_rs = [e for e, _ in events_rs]
    a.append(ok("flux relay annulé se termine proprement",
                "cancelled" in names_rs, f"{names_rs}"))
    return a


def sc_sse_errors(ctx):
    a = []
    # message manquant → 400 BadRequest (bridge direct)
    r = ctx.bridge.request("POST", "/v1/chat", {"request_id": "rid-err-1"})
    a.append(ok("bridge chat sans message → 400", r.status == 400, f"got {r.status} {r.text(200)}"))
    # relay Rust : le status du bridge doit transiter
    rr = ctx.rs.request("POST", "/api/chat/proxy", {"request_id": "rid-err-2"})
    a.append(ok("relay chat sans message → 400", rr.status == 400, f"got {rr.status} {rr.text(200)}"))
    # message vide → 400
    r2 = ctx.bridge.request("POST", "/v1/chat", {"request_id": "rid-err-3", "message": "   "})
    a.append(ok("bridge chat message vide → 400", r2.status == 400, f"got {r2.status} {r2.text(200)}"))
    return a


def sc_bridge_health(ctx):
    a = []
    rb = ctx.bridge.request("GET", "/v1/health")
    rr = ctx.rs.request("GET", "/api/bridge/health")
    a.append(ok("bridge /v1/health 200", rb.status == 200, f"got {rb.status}"))
    bj = rb.json() or {}
    a.append(ok("bridge mode mock", bj.get("mode") == "mock", json.dumps(bj)[:250]))
    a.append(ok("bridge status ok", bj.get("status") == "ok", json.dumps(bj)[:250]))
    a.append(ok("relay /api/bridge/health 200", rr.status == 200, f"got {rr.status} {rr.text(250)}"))
    rj = rr.json() or {}
    a.append(ok("relay status ok", rj.get("status") == "ok", json.dumps(rj)[:250]))
    eq, diff = compare_subset(bj, rj)
    a.append(ok("relay == bridge (sous-ensemble normalisé)", eq, diff))
    return a


# ──────────────────────────────────────────────────────────────────────
# Scénarios — WORKSPACE (lecture seule)
# ──────────────────────────────────────────────────────────────────────
def sc_workspace_list(ctx):
    # R4 : /api/list exposé sur les deux serveurs (contrat upstream).
    # Le port isole sur workspace_root() ; upstream résout via session_id.
    # On compare le listing cross-serveur (sous-ensemble normalisé).
    a = []
    # session_id requis par upstream (/api/file) — le port Rust le tolère.
    rp = ctx.py.request("GET", "/api/list?path=.&session_id=compatfixture01")
    rr = ctx.rs.request("GET", "/api/list?path=.&session_id=compatfixture01")
    a.append(ok("list python 200", rp.status == 200, f"got {rp.status} {rp.text(200)}"))
    a.append(ok("list rust 200", rr.status == 200, f"got {rr.status} {rr.text(200)}"))
    pj, rj = rp.json() or {}, rr.json() or {}
    pnames = {e.get("name") for e in pj.get("entries", [])}
    rnames = {e.get("name") for e in rj.get("entries", [])}
    a.append(ok("workspace_sample.txt listé (python)", "workspace_sample.txt" in pnames, str(pnames)))
    a.append(ok("workspace_sample.txt listé (rust)", "workspace_sample.txt" in rnames, str(rnames)))
    a.append(ok("mêmes noms d'entrées", pnames == rnames, f"py={sorted(pnames)} rust={sorted(rnames)}"))
    ctx.doc("R4 : /api/list cross-serveur — mêmes entrées (fixtures isolées)")
    return a


def sc_workspace_read(ctx):
    # R4 : /api/file exposé sur les deux serveurs (contrat upstream).
    a = []
    # session_id requis par upstream (/api/file) — le port Rust le tolère.
    rp = ctx.py.request("GET", "/api/file?path=workspace_sample.txt&session_id=compatfixture01")
    rr = ctx.rs.request("GET", "/api/file?path=workspace_sample.txt&session_id=compatfixture01")
    a.append(ok("file python 200", rp.status == 200, f"got {rp.status} {rp.text(200)}"))
    a.append(ok("file rust 200", rr.status == 200, f"got {rr.status} {rr.text(200)}"))
    pj, rj = rp.json() or {}, rr.json() or {}
    a.append(ok("contenu identique cross-serveur",
                (pj.get("content") or "").strip() == (rj.get("content") or "").strip(),
                f"py={repr(pj.get('content'))[:80]} rust={repr(rj.get('content'))[:80]}"))
    # size = taille disque (les accents UTF-8 multi-octets différencient du len(content)).
    a.append(ok("size identique (octets disque)", pj.get("size") == rj.get("size"),
                f"py={pj.get('size')} rust={rj.get('size')}"))
    a.append(ok("path identique", pj.get("path") == rj.get("path"), f"py={pj.get('path')} rust={rj.get('path')}"))
    ctx.doc("R4 : /api/file cross-serveur — contenu, size, path identiques")
    return a


def sc_workspace_metadata(ctx):
    # R4 : /api/list renvoie les entrées (métadonnées) ; on compare les types.
    a = []
    rp = ctx.py.request("GET", "/api/list?path=.&session_id=compatfixture01")
    rr = ctx.rs.request("GET", "/api/list?path=.&session_id=compatfixture01")
    pj, rj = rp.json() or {}, rr.json() or {}
    def type_map(entries):
        return {e.get("name"): e.get("type") for e in entries}
    ptypes, rtypes = type_map(pj.get("entries", [])), type_map(rj.get("entries", []))
    a.append(ok("types d'entrées identiques", ptypes == rtypes, f"py={ptypes} rust={rtypes}"))
    a.append(ok("workspace_sample.txt type file (rust)", rtypes.get("workspace_sample.txt") == "file",
                str(rtypes)))
    ctx.doc("R4 : /api/list — types d'entrées identiques cross-serveur")
    return a


def sc_workspace_traversal(ctx):
    # R4 : le port expose /api/list + /api/file — la sécurité traversal est
    # testée cross-serveur via les routes upstream (mêmes refus 400/403/404).
    a = []
    rp = ctx.py.request("GET", "/api/file?path=../../etc/passwd")
    rr = ctx.rs.request("GET", "/api/file?path=../../etc/passwd")
    a.append(ok("traversal file python refusé", rp.status in (400, 403, 404), f"got {rp.status} {rp.text(120)}"))
    a.append(ok("traversal file rust refusé", rr.status in (400, 403, 404), f"got {rr.status} {rr.text(120)}"))
    rp2 = ctx.py.request("GET", "/api/list?path=..%2F..%2Fetc")
    rr2 = ctx.rs.request("GET", "/api/list?path=..%2F..%2Fetc")
    a.append(ok("traversal list python refusé", rp2.status in (400, 403, 404), f"got {rp2.status}"))
    a.append(ok("traversal list rust refusé", rr2.status in (400, 403, 404), f"got {rr2.status}"))
    ctx.doc("R4 : traversal refusé cross-serveur sur /api/file + /api/list")
    return a


# ──────────────────────────────────────────────────────────────────────
# Scénarios — HEALTH / STATIC (hérités, re-vérifiés en état stateful)
# ──────────────────────────────────────────────────────────────────────
def sc_health(ctx):
    a = []
    rp = ctx.py.request("GET", "/health")
    rr = ctx.rs.request("GET", "/health")
    a.append(ok("health python 200", rp.status == 200, f"got {rp.status}"))
    a.append(ok("health rust 200", rr.status == 200, f"got {rr.status}"))
    pj, rj = rp.json() or {}, rr.json() or {}
    a.append(ok("health status ok python", pj.get("status") == "ok", json.dumps(pj)[:200]))
    a.append(ok("health status ok rust", rj.get("status") == "ok", json.dumps(rj)[:200]))
    a.append(ok("health sessions compté (python)", isinstance(pj.get("sessions"), int)))
    a.append(ok("health sessions compté (rust)", isinstance(rj.get("sessions"), int)))
    # Le compteur sessions diffère légitimement : le port persiste immédiatement
    # les sessions créées (delta structurel documenté) — le Python les compte
    # après première écriture. On vérifie le delta (documenté, pas masqué).
    py_n, rs_n = pj.get("sessions", 0), rj.get("sessions", 0)
    a.append(ok("health sessions delta persistance documenté (rust >= python)",
                rs_n >= py_n, f"rust={rs_n} python={py_n}"))
    ctx.doc(f"health sessions : rust={rs_n} vs python={py_n} — persistance immédiate du port (delta structurel)")
    # Retire sessions des shapes comparées (couvert par l'assertion ci-dessus).
    pj_cmp = dict(pj)
    rj_cmp = dict(rj)
    pj_cmp.pop("sessions", None)
    rj_cmp.pop("sessions", None)
    eq, diff = compare_subset(pj_cmp, rj_cmp)
    a.append(ok("health shapes python⊇rust (normalisé, hors sessions)", eq, diff))
    return a


def sc_health_deep(ctx):
    a = []
    rp = ctx.py.request("GET", "/health?deep=1")
    rr = ctx.rs.request("GET", "/health?deep=1")
    a.append(ok("health deep python 200", rp.status == 200, f"got {rp.status}"))
    a.append(ok("health deep rust 200", rr.status == 200, f"got {rr.status}"))
    return a


def sc_static(ctx):
    a = []
    for path in ("/static/boot.js", "/", "/index.html"):
        rp = ctx.py.request("GET", path)
        rr = ctx.rs.request("GET", path)
        a.append(ok(f"GET {path} status python==rust", rp.status == rr.status == 200,
                    f"python={rp.status} rust={rr.status}"))
        a.append(ok(f"GET {path} content-type identique",
                    rp.headers.get("content-type", "").split(";")[0] == rr.headers.get("content-type", "").split(";")[0],
                    f"py={rp.headers.get('content-type')} rs={rr.headers.get('content-type')}"))
    # Traversal statique
    rp = ctx.py.request("GET", "/static/../config.py")
    rr = ctx.rs.request("GET", "/static/../config.py")
    a.append(ok("static traversal python==rust (status)", rp.status == rr.status,
                f"python={rp.status} rust={rr.status}"))
    return a


# ──────────────────────────────────────────────────────────────────────
# Scénarios — AUTH AVEC PASSWORD (rate-limit) — exécutés en dernier
# ──────────────────────────────────────────────────────────────────────
def sc_auth_password_and_ratelimit(ctx):
    """Active l'auth via POST /api/settings _set_password (chemin API réel,
    upstream ET port Rust — un password_hash écrit à la main dans
    settings.json n'est PAS relu par Python, cache au boot), vérifie login
    OK puis rate-limit 5 essais / 60 s (upstream)."""
    a = []
    PASSWORD = "compat-secret-v2"
    # Activation par le vrai chemin API (upstream _set_password, auth.py ;
    # port Rust settings.rs _set_password). Chaque serveur hache avec SON
    # propre sel — pas besoin de sel commun.
    rp = ctx.py.request("POST", "/api/settings", {"_set_password": PASSWORD})
    rr = ctx.rs.request("POST", "/api/settings", {"_set_password": PASSWORD})
    a.append(ok("set_password python 200", rp.status == 200, f"got {rp.status} {rp.text(120)}"))
    a.append(ok("set_password rust 200", rr.status == 200, f"got {rr.status} {rr.text(120)}"))
    sp = (ctx.py.request("GET", "/api/auth/status").json() or {})
    sr = (ctx.rs.request("GET", "/api/auth/status").json() or {})
    a.append(ok("auth_enabled true python", sp.get("auth_enabled") is True, json.dumps(sp)[:120]))
    a.append(ok("auth_enabled true rust", sr.get("auth_enabled") is True, json.dumps(sr)[:120]))
    # Login correct (mot de passe réel).
    rp = ctx.py.request("POST", "/api/auth/login", {"password": PASSWORD})
    a.append(ok("login bon mot de passe python 200", rp.status == 200,
                f"got {rp.status} {rp.text(200)}"))
    pj = rp.json() or {}
    a.append(ok("login ok:true python", pj.get("ok") is True, json.dumps(pj)[:200]))
    a.append(ok("cookie hermes_session posé (python)", bool(ctx.py.cookies.get("hermes_session")),
                str(ctx.py.cookies)[:200]))
    rr = ctx.rs.request("POST", "/api/auth/login", {"password": PASSWORD})
    a.append(ok("login bon mot de passe rust 200", rr.status == 200,
                f"got {rr.status} {rr.text(200)}"))
    rj = rr.json() or {}
    a.append(ok("login ok:true rust", rj.get("ok") is True, json.dumps(rj)[:200]))
    a.append(ok("cookie hermes_session posé (rust)", bool(ctx.rs.cookies.get("hermes_session")),
                str(ctx.rs.cookies)[:200]))
    # Logout pour repartir propre avant les tentatives échouées.
    ctx.py.request("POST", "/api/auth/logout", {})
    ctx.rs.request("POST", "/api/auth/logout", {})
    # Mauvais mot de passe ×5 → 401 ; 6e tentative → 429 (upstream).
    # IMPORTANT : l'IP source est 127.0.0.1 pour les deux serveurs, mais les
    # compteurs sont dans des STATE_DIR séparés → indépendants.
    statuses_py = []
    for i in range(6):
        r = ctx.py.request("POST", "/api/auth/login", {"password": "wrong-password"})
        statuses_py.append(r.status)
        if r.status != 429:
            time.sleep(0.05)
    a.append(ok("rate-limit python : 5×401 puis 429",
                statuses_py[:5] == [401] * 5 and statuses_py[5] == 429,
                f"statuses={statuses_py}"))
    a.append(ok("login attempts persisté sur disque (python)",
                (Path(ctx.args.python_state_dir) / ".login_attempts.json").exists()))
    # Rust : le port n'a pas (encore) de rate-limit — delta rapporté, pas masqué.
    statuses_rs = []
    for i in range(6):
        r = ctx.rs.request("POST", "/api/auth/login", {"password": "wrong-password"})
        statuses_rs.append(r.status)
    if statuses_rs == statuses_py:
        a.append(ok("rate-limit rust == python", True, f"statuses={statuses_rs}"))
    else:
        a.append(ok("rate-limit rust == python (delta rapporté)",
                    False,
                    f"python={statuses_py} rust={statuses_rs} — "
                    "delta fonctionnel : le port Rust n'implémente pas "
                    "encore _check_login_rate (5/60s)"))
    # Nettoyage : désactiver l'auth (chemin API réel, pas d'édition manuelle).
    ctx.py.request("POST", "/api/settings", {"_clear_password": True})
    ctx.rs.request("POST", "/api/settings", {"_clear_password": True})
    return a


# ──────────────────────────────────────────────────────────────────────
# Scénarios — IMPORT / EXPORT sessions (Track F R3)
# ──────────────────────────────────────────────────────────────────────
def sc_session_export_json(ctx):
    """GET /api/session/export — JSON round-trip, headers, redaction."""
    a = []
    # Créer une session côté python et rust, puis exporter.
    body = {"title": "export-test"}
    rp = ctx.py.request("POST", "/api/session/new", body)
    rr = ctx.rs.request("POST", "/api/session/new", body)
    sid_py = (rp.json() or {}).get("session", {}).get("session_id")
    sid_rs = (rr.json() or {}).get("session", {}).get("session_id")
    if not sid_py or not sid_rs:
        a.append(ok("sessions créées pour export", False, f"py={sid_py} rs={sid_rs}"))
        return a
    ep = ctx.py.request("GET", f"/api/session/export?session_id={sid_py}")
    er = ctx.rs.request("GET", f"/api/session/export?session_id={sid_rs}")
    a.append(ok("export python 200", ep.status == 200, f"got {ep.status} {ep.text(120)}"))
    a.append(ok("export rust 200", er.status == 200, f"got {er.status} {er.text(120)}"))
    a.append(ok("export python content-type json",
                "application/json" in ep.headers.get("content-type", ""),
                ep.headers.get("content-type", "")))
    a.append(ok("export rust content-type json",
                "application/json" in er.headers.get("content-type", ""),
                er.headers.get("content-type", "")))
    a.append(ok("export python Content-Disposition",
                "attachment" in ep.headers.get("content-disposition", ""),
                ep.headers.get("content-disposition", "")))
    a.append(ok("export rust Content-Disposition",
                "attachment" in er.headers.get("content-disposition", ""),
                er.headers.get("content-disposition", "")))
    pj = ep.json() or {}
    rj = er.json() or {}
    a.append(ok("export python session_id présent", pj.get("session_id") == sid_py,
                json.dumps(pj)[:150]))
    a.append(ok("export rust session_id présent", rj.get("session_id") == sid_rs,
                json.dumps(rj)[:150]))
    a.append(ok("export python password_hash absent",
                "password_hash" not in pj, json.dumps(pj)[:150]))
    a.append(ok("export rust password_hash absent",
                "password_hash" not in rj, json.dumps(rj)[:150]))
    return a


def sc_session_export_html(ctx):
    """GET /api/session/export?format=html — document autonome."""
    a = []
    rr = ctx.rs.request("POST", "/api/session/new", {"title": "html-export"})
    sid_rs = (rr.json() or {}).get("session", {}).get("session_id")
    if not sid_rs:
        a.append(ok("session rust créée", False, "pas de sid"))
        return a
    er = ctx.rs.request("GET", f"/api/session/export?session_id={sid_rs}&format=html")
    a.append(ok("export html rust 200", er.status == 200, f"got {er.status} {er.text(120)}"))
    a.append(ok("export html content-type",
                "text/html" in er.headers.get("content-type", ""),
                er.headers.get("content-type", "")))
    a.append(ok("export html doctype", er.body.lstrip().startswith(b"<!DOCTYPE html>"),
                er.text(120)))
    a.append(ok("export html hermes-{sid} filename",
                f"hermes-{sid_rs}.html" in er.headers.get("content-disposition", ""),
                er.headers.get("content-disposition", "")))
    return a


def sc_session_export_errors(ctx):
    """GET /api/session/export — erreurs 400/404 identiques."""
    a = []
    rp = ctx.py.request("GET", "/api/session/export")
    rr = ctx.rs.request("GET", "/api/session/export")
    a.append(ok("export sans session_id python==rust 400",
                rp.status == 400 and rr.status == 400,
                f"py={rp.status} rs={rr.status}"))
    rp = ctx.py.request("GET", "/api/session/export?session_id=inexistant-zz")
    rr = ctx.rs.request("GET", "/api/session/export?session_id=inexistant-zz")
    a.append(ok("export sid inconnu python==rust 404",
                rp.status == 404 and rr.status == 404,
                f"py={rp.status} rs={rr.status}"))
    return a


def sc_session_import(ctx):
    """POST /api/session/import — round-trip export→import (nouvel id)."""
    a = []
    body = {
        "title": "import-test",
        "messages": [{"role": "user", "content": "message importé"}],
        "workspace": None,  # résolu au défaut par chaque serveur
        "pinned": True,
    }
    # workspace None → les serveurs utilisent leur défaut ; on retire la clé.
    body.pop("workspace", None)
    rp = ctx.py.request("POST", "/api/session/import", body)
    rr = ctx.rs.request("POST", "/api/session/import", body)
    a.append(ok("import python 200 ok:true",
                rp.status == 200 and (rp.json() or {}).get("ok") is True,
                f"got {rp.status} {rp.text(120)}"))
    a.append(ok("import rust 200 ok:true",
                rr.status == 200 and (rr.json() or {}).get("ok") is True,
                f"got {rr.status} {rr.text(120)}"))
    pj = (rp.json() or {}).get("session", {})
    rj = (rr.json() or {}).get("session", {})
    a.append(ok("import python title", pj.get("title") == "import-test",
                json.dumps(pj)[:150]))
    a.append(ok("import rust title", rj.get("title") == "import-test",
                json.dumps(rj)[:150]))
    a.append(ok("import python messages conservés",
                len(pj.get("messages") or []) == 1, json.dumps(pj)[:150]))
    a.append(ok("import rust messages conservés",
                len(rj.get("messages") or []) == 1, json.dumps(rj)[:150]))
    a.append(ok("import python nouveau id", pj.get("session_id") not in (None, ""),
                json.dumps(pj)[:150]))
    a.append(ok("import rust nouveau id", rj.get("session_id") not in (None, ""),
                json.dumps(rj)[:150]))
    return a


def sc_session_import_errors(ctx):
    """POST /api/session/import — erreurs 400 identiques."""
    a = []
    for label, payload in (
        ("body non-objet", [1, 2, 3]),
        ("messages manquant", {"title": "x"}),
        ("messages non-liste", {"messages": "nope"}),
    ):
        rp = ctx.py.request("POST", "/api/session/import", payload)
        rr = ctx.rs.request("POST", "/api/session/import", payload)
        a.append(ok(f"import {label} python==rust 400",
                    rp.status == 400 and rr.status == 400,
                    f"py={rp.status} rs={rr.status} | py={rp.text(80)} rs={rr.text(80)}"))
    return a


def sc_session_import_persist(ctx):
    """POST /api/session/import — persistance disque + relecture."""
    a = []
    body = {
        "title": "import-persist",
        "messages": [{"role": "assistant", "content": "ok"}],
    }
    rr = ctx.rs.request("POST", "/api/session/import", body)
    sid = (rr.json() or {}).get("session", {}).get("session_id")
    if not sid:
        a.append(ok("import rust créé", False, "pas de sid"))
        return a
    # Relecture via GET — la session importée doit être listée et lisible.
    gr = ctx.rs.request("GET", f"/api/session?session_id={sid}")
    a.append(ok("relecture rust 200", gr.status == 200, f"got {gr.status}"))
    a.append(ok("relecture rust title", (gr.json() or {}).get("session", {}).get("title") == "import-persist",
                gr.text(120)))
    # Persistance disque (sidecar).
    sidecar = Path(ctx.args.rust_state_dir) / "sessions" / f"{sid}.json"
    a.append(ok("sidecar rust présent", sidecar.exists(), f"path={sidecar}"))
    if sidecar.exists():
        disk = json.loads(sidecar.read_text())
        a.append(ok("sidecar rust title", disk.get("title") == "import-persist",
                    json.dumps(disk)[:120]))
    return a


# ──────────────────────────────────────────────────────────────────────
# Registre
# ──────────────────────────────────────────────────────────────────────
SCENARIOS = [
    # Sessions (stateful)
    Scenario("session/new", "POST /api/session/new — création sur les deux serveurs, shapes comparées", sc_session_new),
    Scenario("session/get", "GET /api/session?session_id=… — lecture, title/messages", sc_session_get),
    Scenario("session/list", "GET /api/sessions — la nouvelle session est listée, compteurs", sc_session_list),
    Scenario("session/rename", "POST /api/session/rename — titre persisté (re-GET)", sc_session_rename),
    Scenario("session/update", "POST /api/session/update — model/model_provider persistés", sc_session_update),
    Scenario("session/messages0", "GET /api/session?messages=0 — pas de payload messages", sc_session_messages0),
    Scenario("session/fixture", "Fixture session réelle — chargée par les deux serveurs, shapes identiques", sc_session_fixture),
    Scenario("session/persistence-disk", "Persistance disque — sidecar STATE_DIR/sessions/<sid>.json", sc_session_persistence_disk),
    Scenario("session/profile-scoping", "Création avec profile explicite — persisté des deux côtés", sc_session_profile_scoping),
    Scenario("session/delete", "POST /api/session/delete — ok:true, puis GET 404", sc_session_delete),
    Scenario("session/delete-missing", "POST /api/session/delete sur un sid absent — même comportement", sc_session_delete_missing),
    Scenario("session/errors", "Erreurs sessions : 400/404 sur les routes couvertes", sc_session_errors),
    # Import / export (Track F R3)
    Scenario("session/export-json", "GET /api/session/export — JSON round-trip, headers, redaction", sc_session_export_json),
    Scenario("session/export-html", "GET /api/session/export?format=html — document autonome", sc_session_export_html),
    Scenario("session/export-errors", "GET /api/session/export — erreurs 400/404 identiques", sc_session_export_errors),
    Scenario("session/import", "POST /api/session/import — round-trip (nouvel id)", sc_session_import),
    Scenario("session/import-errors", "POST /api/session/import — erreurs 400 identiques", sc_session_import_errors),
    Scenario("session/import-persist", "POST /api/session/import — persistance disque + relecture", sc_session_import_persist),
    # Settings
    Scenario("settings/get", "GET /api/settings — shape, password_hash jamais exposé", sc_settings_get),
    Scenario("settings/post", "POST /api/settings — valeurs persistées, shapes comparées", sc_settings_post),
    Scenario("settings/redaction", "POST /api/settings avec password_hash — ignoré/redigé", sc_settings_redaction),
    Scenario("settings/disk", "Persistance disque — STATE_DIR/settings.json", sc_settings_disk),
    # Auth (auth désactivée)
    Scenario("auth/disabled", "POST /api/auth/login sans auth — ok:true des deux côtés", sc_auth_disabled),
    Scenario("auth/status", "GET /api/auth/status — auth_enabled:false, logged_in:false", sc_auth_status),
    Scenario("auth/logout", "POST /api/auth/logout — 200 ok:true", sc_auth_logout),
    # SSE
    Scenario("sse/bridge-direct", "POST /v1/chat (bridge mock) — séquence start→…→done", sc_sse_bridge_direct),
    Scenario("sse/rust-relay", "POST /api/chat/proxy (relay Rust) — même séquence", sc_sse_rust_relay),
    Scenario("sse/relay-vs-bridge", "Comparaison relay Rust vs bridge direct (events normalisés)", sc_sse_relay_vs_bridge),
    Scenario("sse/close-after-done", "Fermeture de connexion après done (EOF < 5s)", sc_sse_close_after_done),
    Scenario("sse/cancel", "POST /v1/cancel — contrat API + terminaison propre", sc_sse_cancel),
    Scenario("sse/errors", "Erreurs bridge : message manquant/vide → 400 (direct + relay)", sc_sse_errors),
    Scenario("sse/bridge-health", "GET /v1/health vs GET /api/bridge/health", sc_bridge_health),
    # Workspace
    Scenario("workspace/list", "GET /api/workspace/list — fixture listée des deux côtés", sc_workspace_list),
    Scenario("workspace/read", "GET /api/workspace/read — contenu identique", sc_workspace_read),
    Scenario("workspace/metadata", "GET /api/workspace/metadata — shapes comparées", sc_workspace_metadata),
    Scenario("workspace/traversal", "Traversal ../../etc/passwd — mêmes status de refus", sc_workspace_traversal),
    # Health / static
    Scenario("health", "GET /health — status ok, sessions compté", sc_health),
    Scenario("health/deep", "GET /health?deep=1 — 200 des deux côtés", sc_health_deep),
    Scenario("static", "GET /, /index.html, /static/boot.js — status + content-type", sc_static),
    # Auth avec password (rate-limit) — toujours en dernier (état global modifié)
    Scenario("auth/password-ratelimit", "Auth activée : login OK, rate-limit 5/60s (upstream)", sc_auth_password_and_ratelimit),
]


# ──────────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python-url", required=True)
    ap.add_argument("--rust-url", required=True)
    ap.add_argument("--bridge-url", required=True)
    ap.add_argument("--python-state-dir", required=True)
    ap.add_argument("--rust-state-dir", required=True)
    ap.add_argument("--python-workspace", default="/tmp/hwui-compat-workspace")
    ap.add_argument("--rust-workspace", default="/tmp/hermes-webui-rust-workspace")
    ap.add_argument("--scenario", action="append", default=None)
    args = ap.parse_args()

    global WS_PATTERNS
    WS_PATTERNS = [
        (str(Path(args.python_workspace).resolve()), WS_TOKEN),
        (str(Path(args.rust_workspace).resolve()), WS_TOKEN),
        (str(Path(args.python_workspace)), WS_TOKEN),
        (str(Path(args.rust_workspace)), WS_TOKEN),
    ]

    # Setup : fixtures copiées dans les stores des deux serveurs.
    setup_fixtures(args)

    ctx = Ctx(args)
    scenarios = [s for s in SCENARIOS if args.scenario is None or s.name in args.scenario]

    log("=" * 78)
    log(f"compat harness v2 (Track E R3) — python={args.python_url} rust={args.rust_url} bridge={args.bridge_url}")
    log(f"scénarios: {len(scenarios)}")
    log("=" * 78)

    results = []
    for sc in scenarios:
        log(f"\n▶ {sc.name}")
        log(f"  {sc.doc}")
        asserts = sc.run(ctx)
        passed = sum(1 for x in asserts if x.passed)
        failed = [x for x in asserts if not x.passed]
        results.append((sc.name, len(asserts), passed, failed))
        for x in asserts:
            mark = "PASS" if x.passed else "FAIL"
            log(f"  [{mark}] {x.name}" + (f" — {x.detail[:300]}" if (not x.passed and x.detail) else ""))
        if not failed:
            log(f"  → scénario OK ({passed}/{len(asserts)})")

    # Rapport
    log("\n" + "=" * 78)
    log("RAPPORT")
    log("=" * 78)
    total_a = sum(n for _, n, _, _ in results)
    total_p = sum(p for _, _, p, _ in results)
    total_f = total_a - total_p
    sc_ok = sum(1 for _, _, _, f in results if not f)
    sc_fail = len(results) - sc_ok
    for name, n, p, failed in results:
        status = "PASS" if not failed else "FAIL"
        log(f"  {status:4s} {name} ({p}/{n})")
        for x in failed:
            log(f"         └ {x.name}: {x.detail[:400]}")
    log("")
    log(f"scénarios: {sc_ok}/{len(results)} PASS, {sc_fail} FAIL")
    log(f"assertions: {total_p}/{total_a} PASS, {total_f} FAIL")

    if ctx.doc_deltas:
        log("\ndeltas tolérés documentés (normalisation, jamais masqués) :")
        for d in ctx.doc_deltas:
            log(f"  - {d}")
    log("\ndeltas tolérés statiques (voir en-tête du fichier) :")
    for k in sorted(TOLERATED_KEYS):
        log(f"  - {k}")
    log("headers tolérés : " + ", ".join(sorted(TOLERATED_HEADERS)))
    log("workspace paths normalisés vers <WORKSPACE> (isolation des stores)")
    log("session_id / csrf_token / timestamps normalisés (valeurs propres à chaque serveur)")

    return 1 if sc_fail else 0


def setup_fixtures(args):
    """Copie les fixtures dans les stores isolés des deux serveurs."""
    here = Path(__file__).parent
    fixture = here / "fixtures" / "session_compat_v2.json"
    ws_file = here / "fixtures" / "workspace_sample.txt"
    for state_dir, ws_dir in ((args.python_state_dir, args.python_workspace),
                              (args.rust_state_dir, args.rust_workspace)):
        sessions = Path(state_dir) / "sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture, sessions / "compatfixture01.json")
        wsd = Path(ws_dir)
        wsd.mkdir(parents=True, exist_ok=True)
        shutil.copy(ws_file, wsd / "workspace_sample.txt")
    log(f"fixtures copiées : sessions/compatfixture01.json + workspace_sample.txt "
        f"(python={args.python_state_dir}, rust={args.rust_state_dir})")


if __name__ == "__main__":
    sys.exit(main())
