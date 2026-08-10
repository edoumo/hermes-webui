#!/usr/bin/env python3
"""Hermes Agent Bridge — Mince adaptateur Python entre Hermes WebUI Rust
et Hermes Agent (AIAgent).

Transport : HTTP sur 127.0.0.1 + SSE pour le streaming (protocole v1, voir
PROTOCOL.md). Deux modes :
  - real : wrappe AIAgent (import depuis le chemin Hermes Agent).
  - mock : flux déterministe pour tests CI (aucun LLM payant/Internet).

Protocole v1+ (R3, additif — v1 intact) :
  - GET  /v1/sessions            : sessions agent depuis state.db (mode=ro)
  - GET  /v1/sessions/{id}       : une session agent
  - POST /v1/sessions/{id}/activate : warm l'agent cache pour la session
  - GET  /v1/agent-cache         : snapshot + stats du cache
  - DELETE /v1/agent-cache/{id}  : éviction explicite d'une session
  - DELETE /v1/agent-cache       : purge totale (diagnostic)

Le bridge ne manipule aucun secret (les clés API ne servent qu'à une
signature sha256 tronquée, jamais stockées ni loggées) et ne s'expose jamais
hors localhost. state.db n'est JAMAIS ouvert en écriture (mode=ro strict).
"""

import json
import os
import queue  # noqa: F401 — conservé pour compat imports externes
import signal
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agent_cache import AgentCache, build_agent_signature
from agent_sessions import StateDbUnavailable, get_agent_session, list_agent_sessions

PROTOCOL_VERSION = "1.1"  # v1+ : endpoints additifs, v1 intact
BRIDGE_VERSION = "0.2.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8794
DEFAULT_MOCK_STATE_DB = "/tmp/hermes-bridge-mock/state.db"


class BridgeError(Exception):
    def __init__(self, message, error_class="Internal"):
        super().__init__(message)
        self.error_class = error_class


# ---------------------------------------------------------------------------
# Mock agent — déterministe, sans réseau/LLM, pour les tests CI.
# ---------------------------------------------------------------------------
class MockAgent:
    """Émule un flux agentique structurellement fidèle : start → reasoning →
    token(s) → tool_start → tool_result → token → done. Aucun appel LLM.

    ``cancel_check`` (optionnel) : callable consulté entre chaque emission ;
    s'il renvoie vrai, le flux émet ``cancelled`` et s'arrête proprement.
    """

    def stream(self, request_id, session_id, message, history, callbacks,
               cancel_check=None):
        emit = callbacks

        def cancelled():
            return bool(cancel_check and cancel_check())

        emit("start", {"request_id": request_id})
        if cancelled():
            emit("cancelled", {"request_id": request_id})
            return ""
        emit("reasoning", {"request_id": request_id, "text": "[mock] reasoning..."})
        if cancelled():
            emit("cancelled", {"request_id": request_id})
            return ""
        emit("token", {"request_id": request_id, "text": "Bonjour depuis le bridge mock. "})
        if cancelled():
            emit("cancelled", {"request_id": request_id})
            return ""
        emit("tool_start", {
            "request_id": request_id,
            "name": "mock_tool",
            "arguments": {"input": message},
        })
        if cancelled():
            emit("cancelled", {"request_id": request_id})
            return ""
        emit("tool_result", {
            "request_id": request_id,
            "name": "mock_tool",
            "result": {"ok": True},
        })
        if cancelled():
            emit("cancelled", {"request_id": request_id})
            return ""
        emit("token", {"request_id": request_id, "text": "Tool exécuté."})
        if cancelled():
            emit("cancelled", {"request_id": request_id})
            return ""
        final = "Réponse mock complète (déterministe)."
        emit("done", {"request_id": request_id, "final_response": final})
        return final


# ---------------------------------------------------------------------------
# Real agent wrapper — AIAgent (Hermes Agent Python).
# ---------------------------------------------------------------------------
class RealAgent:
    """Wrappe AIAgent via les callbacks de streaming. Import paresseux du
    chemin Hermes Agent pour ne pas dépendre d'un venv particulier ici.

    Mode v1 : un AIAgent est construit PAR APPEL (aucun état partagé entre
    sessions). Pour le cache v1+ (agent persistant par session), voir
    SessionAgent.
    """

    def __init__(self, agent_dir):
        self._agent_dir = agent_dir
        self._AIAgent = None
        self._import_lock = threading.Lock()

    def _import(self):
        if self._AIAgent is not None:
            return self._AIAgent
        with self._import_lock:
            if self._AIAgent is not None:
                return self._AIAgent
            if self._agent_dir and self._agent_dir not in sys.path:
                sys.path.insert(0, self._agent_dir)
            # noqa: E402 — import conditionné au chemin agent
            from run_agent import AIAgent
            self._AIAgent = AIAgent
            return self._AIAgent

    def stream(self, request_id, session_id, message, history, callbacks,
               profile=None, model=None, provider=None):
        AIAgent = self._import()
        emit = callbacks
        emit("start", {"request_id": request_id})

        # Wrappers de callbacks AIAgent -> events bridge.
        def on_token(text):
            emit("token", {"request_id": request_id, "text": text})

        def on_reasoning(text):
            emit("reasoning", {"request_id": request_id, "text": text})

        def on_tool(progress):
            emit("tool_result", {"request_id": request_id, "name": "tool", "result": progress})

        def on_tool_start(name, args):
            emit("tool_start", {"request_id": request_id, "name": name, "arguments": args})

        kwargs = dict(
            model=model or "",
            provider=provider or None,
            platform="webui",
            quiet_mode=True,
            session_id=session_id,
            stream_delta_callback=on_token,
            reasoning_callback=on_reasoning,
            tool_progress_callback=on_tool,
        )
        if self._agent_dir:
            kwargs["enabled_toolsets"] = []  # bridge ne déclenche pas de tools externes par défaut
        agent = AIAgent(**kwargs)
        result = agent.run_conversation(message, conversation_history=history or None)
        final = result.get("final_response", "")
        emit("done", {"request_id": request_id, "final_response": final})
        return final


class SessionAgent:
    """Agent runtime persistant pour UNE session (miroir SESSION_AGENT_CACHE
    upstream : l'AIAgent est construit une fois et réutilisé entre turns).

    Isolation stricte : l'instance est créée par la fabrique du cache avec un
    ``session_id`` figé ; elle n'est JAMAIS partagée entre sessions. Les
    callbacks (qui ferment sur des objets request-scoped) sont rafraîchis à
    chaque turn, comme upstream (api/streaming.py:9502-9518).
    """

    def __init__(self, agent_dir, session_id, identity):
        self._agent_dir = agent_dir
        self._session_id = session_id
        self._identity = identity or {}
        self._agent = None
        self._import_lock = threading.Lock()

    def _import(self):
        if self._agent is not None:
            return self._agent
        with self._import_lock:
            if self._agent is not None:
                return self._agent
            if self._agent_dir and self._agent_dir not in sys.path:
                sys.path.insert(0, self._agent_dir)
            # noqa: E402 — import conditionné au chemin agent
            from run_agent import AIAgent
            kwargs = dict(
                model=self._identity.get("model") or "",
                provider=self._identity.get("provider") or None,
                platform="webui",
                quiet_mode=True,
                session_id=self._session_id,
            )
            if self._agent_dir:
                kwargs["enabled_toolsets"] = []  # pas de tools externes par défaut
            self._agent = AIAgent(**kwargs)
            return self._agent

    def stream(self, request_id, session_id, message, history, callbacks,
               profile=None, model=None, provider=None):
        agent = self._import()
        emit = callbacks
        emit("start", {"request_id": request_id})

        def on_token(text):
            emit("token", {"request_id": request_id, "text": text})

        def on_reasoning(text):
            emit("reasoning", {"request_id": request_id, "text": text})

        def on_tool(progress):
            emit("tool_result", {"request_id": request_id, "name": "tool", "result": progress})

        def on_tool_start(name, args):
            emit("tool_start", {"request_id": request_id, "name": name, "arguments": args})

        # Rafraîchissement des callbacks per-turn (objets request-scoped).
        agent.stream_delta_callback = on_token
        agent.reasoning_callback = on_reasoning
        agent.tool_progress_callback = on_tool
        if hasattr(agent, "tool_start_callback"):
            agent.tool_start_callback = on_tool_start
        if hasattr(agent, "_interrupted"):
            agent._interrupted = False
        if hasattr(agent, "_interrupt_message"):
            agent._interrupt_message = None

        result = agent.run_conversation(message, conversation_history=history or None)
        final = result.get("final_response", "")
        emit("done", {"request_id": request_id, "final_response": final})
        return final


# ---------------------------------------------------------------------------
# Bridge handler
# ---------------------------------------------------------------------------
class BridgeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # (request_id -> thread de stream)
    streams = {}
    streams_lock = threading.Lock()

    def log_message(self, fmt, *args):  # logs structurés légers
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f'[{ts}] bridge {self.client_address[0]} {fmt % args}', flush=True)

    def _read_json_body(self, max_bytes=1 << 20):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > max_bytes:
            raise BridgeError("payload too large", "PayloadTooLarge")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BridgeError("invalid JSON body", "BadRequest")

    def _send_json(self, status, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status, msg, error_class):
        self._send_json(status, {"error": msg, "error_class": error_class})

    # -- routing -------------------------------------------------------------
    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path == "/v1/health":
                self._handle_health()
            elif path == "/v1/version":
                self._handle_version()
            elif path == "/v1/streams":
                self._handle_streams()
            elif path == "/v1/sessions":
                self._handle_sessions_list()
            elif path == "/v1/agent-cache":
                self._handle_agent_cache_get()
            elif path.startswith("/v1/sessions/"):
                self._handle_session_get(path)
            else:
                self._send_error_json(404, "not found", "NotFound")
        except BridgeError as e:
            self._send_error_json(self._status_for(e.error_class), str(e), e.error_class)
        except StateDbUnavailable as e:
            self._send_error_json(503, str(e), "StateDbUnavailable")
        except Exception as e:  # noqa: BLE001
            self._send_error_json(500, "internal error", "Internal")

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path == "/v1/chat":
                self._handle_chat()
            elif path == "/v1/cancel":
                self._handle_cancel()
            elif path.startswith("/v1/sessions/") and path.endswith("/activate"):
                self._handle_session_activate(path)
            else:
                self._send_error_json(404, "not found", "NotFound")
        except BridgeError as e:
            self._send_error_json(self._status_for(e.error_class), str(e), e.error_class)
        except StateDbUnavailable as e:
            self._send_error_json(503, str(e), "StateDbUnavailable")
        except Exception as e:  # noqa: BLE001
            self._send_error_json(500, "internal error", "Internal")

    def do_DELETE(self):  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path == "/v1/agent-cache":
                self._handle_agent_cache_clear()
            elif path.startswith("/v1/agent-cache/"):
                self._handle_agent_cache_evict(path)
            else:
                self._send_error_json(404, "not found", "NotFound")
        except BridgeError as e:
            self._send_error_json(self._status_for(e.error_class), str(e), e.error_class)
        except Exception as e:  # noqa: BLE001
            self._send_error_json(500, "internal error", "Internal")

    def _status_for(self, error_class):
        return {
            "BadRequest": 400, "Unauthorized": 401, "Forbidden": 403,
            "NotFound": 404, "Conflict": 409, "PayloadTooLarge": 413,
            "HermesUnavailable": 503, "HermesProtocolError": 502,
            "StateDbUnavailable": 503,
        }.get(error_class, 500)

    # -- endpoints v1 (inchangés) --------------------------------------------
    def _handle_health(self):
        try:
            self.server.agent  # noqa: B018
            available = True
        except Exception:
            available = False
        self._send_json(200, {
            "status": "ok",
            "service": "hermes-bridge",
            "protocol_version": PROTOCOL_VERSION,
            "agent_available": available,
            "mode": self.server.mode,
            "state_db_available": self.server.state_db_available(),
        })

    def _handle_version(self):
        self._send_json(200, {
            "protocol_version": PROTOCOL_VERSION,
            "bridge_version": BRIDGE_VERSION,
        })

    def _handle_streams(self):
        with self.streams_lock:
            active = list(self.streams.keys())
        self._send_json(200, {"active": active})

    def _handle_cancel(self):
        body = self._read_json_body()
        request_id = body.get("request_id")
        if not request_id:
            raise BridgeError("request_id required", "BadRequest")
        # L'annulation est gérée par un flag partagé consulté par le stream.
        self.server.cancelled.add(request_id)
        self._send_json(200, {"cancelled": True})

    # -- endpoints v1+ : sessions agent (state.db, lecture seule) -------------
    def _handle_sessions_list(self):
        from urllib.parse import parse_qs
        qs = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        try:
            limit = int(qs.get("limit", ["200"])[0])
        except ValueError:
            raise BridgeError("invalid limit", "BadRequest")
        source = qs.get("source", [None])[0]
        rows = list_agent_sessions(self.server.state_db_path, limit=limit, source=source)
        self._send_json(200, {
            "sessions": rows,
            "count": len(rows),
            "state_db": str(self.server.state_db_path),
        })

    def _handle_session_get(self, path):
        session_id = self._session_id_from_path(path, prefix="/v1/sessions/")
        row = get_agent_session(self.server.state_db_path, session_id)
        if row is None:
            self._send_error_json(404, f"session not found: {session_id}", "NotFound")
            return
        self._send_json(200, {"session": row})

    def _handle_session_activate(self, path):
        session_id = self._session_id_from_path(
            path, prefix="/v1/sessions/", suffix="/activate"
        )
        body = self._read_json_body()
        # L'activation exige une session connue de state.db (fail closed).
        row = get_agent_session(self.server.state_db_path, session_id)
        if row is None:
            self._send_error_json(404, f"session not found: {session_id}", "NotFound")
            return
        identity = self._identity_from_body(body)
        agent, created = self.server.agent_cache.get_or_create(session_id, identity)
        signature = build_agent_signature(identity)
        self._send_json(200, {
            "session_id": session_id,
            "signature": signature,
            "cached": not created,
            "cache": self.server.agent_cache.stats(),
        })

    # -- endpoints v1+ : agent cache ------------------------------------------
    def _handle_agent_cache_get(self):
        self._send_json(200, {
            "entries": self.server.agent_cache.snapshot(),
            "stats": self.server.agent_cache.stats(),
        })

    def _handle_agent_cache_evict(self, path):
        session_id = self._session_id_from_path(path, prefix="/v1/agent-cache/")
        self.server.agent_cache.evict(session_id)
        self._send_json(200, {
            "evicted": True,
            "session_id": session_id,
            "cache": self.server.agent_cache.stats(),
        })

    def _handle_agent_cache_clear(self):
        self.server.agent_cache.clear()
        self._send_json(200, {
            "cleared": True,
            "cache": self.server.agent_cache.stats(),
        })

    # -- helpers --------------------------------------------------------------
    @staticmethod
    def _session_id_from_path(path, prefix, suffix=None):
        rest = path[len(prefix):]
        if suffix:
            if not rest.endswith(suffix):
                raise BridgeError("not found", "NotFound")
            rest = rest[: -len(suffix)]
        if not rest or "/" in rest:
            raise BridgeError("not found", "NotFound")
        return rest

    def _identity_from_body(self, body):
        """Identité d'agent depuis le body — sert UNIQUEMENT à la signature.

        La clé API n'est jamais stockée ni loggée : seule sa signature
        sha256 tronquée entre dans le cache (voir agent_cache._api_key_sig).
        """
        profile = body.get("profile")
        hermes_home = os.environ.get("HERMES_HOME", "")
        profile_home = body.get("profile_home")
        if not profile_home:
            if profile and hermes_home:
                profile_home = str(Path(hermes_home) / "profiles" / profile)
            else:
                profile_home = hermes_home
        return {
            "model": body.get("model"),
            "provider": body.get("provider"),
            "base_url": body.get("base_url"),
            "api_key": body.get("api_key"),
            "api_mode": body.get("api_mode"),
            "command": body.get("command"),
            "args": body.get("args") or [],
            "credential_pool": body.get("credential_pool"),
            "max_iterations": body.get("max_iterations"),
            "max_tokens": body.get("max_tokens"),
            "fallback": body.get("fallback") or {},
            "toolsets": body.get("toolsets") or [],
            "reasoning_config": body.get("reasoning_config") or {},
            "profile_home": profile_home,
        }

    def _cache_factory(self, session_id, identity):
        """Fabrique d'agents pour le cache : un agent persistant PAR session."""
        if isinstance(self.server.agent, MockAgent):
            return MockAgent()
        return SessionAgent(self.server.agent_dir, session_id, identity)

    # -- chat SSE (v1, enrichi cache v1+) --------------------------------------
    def _handle_chat(self):
        body = self._read_json_body()
        request_id = body.get("request_id") or str(uuid.uuid4())
        session_id = body.get("session_id") or "mock-session"
        message = body.get("message", "").strip()
        if not message:
            raise BridgeError("message required", "BadRequest")
        history = body.get("history") or []

        # Agent cache v1+ : un agent persistant par session, signature
        # d'identité stricte (isolation A/B garantie par la clé session_id).
        identity = self._identity_from_body(body)
        agent, created = self.server.agent_cache.get_or_create(session_id, identity)
        self.server.agent_cache.touch(session_id)

        # SSE headers
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.flush_headers()

        emit = lambda name, data: self._emit_sse(name, data)  # noqa: E731

        def run():
            try:
                if isinstance(agent, MockAgent):
                    agent.stream(
                        request_id, session_id, message, history, emit,
                        cancel_check=lambda: request_id in self.server.cancelled,
                    )
                else:
                    agent.stream(
                        request_id, session_id, message, history, emit,
                        profile=body.get("profile"), model=body.get("model"),
                        provider=body.get("provider"),
                    )
            except Exception as e:  # noqa: BLE001
                try:
                    emit("error", {"request_id": request_id,
                                   "error": "agent stream failed",
                                   "error_class": "HermesUnavailable"})
                except Exception:
                    pass

        # Thread de stream, annulable via flag.
        def cancellable_run():
            try:
                run()
            finally:
                with self.streams_lock:
                    self.streams.pop(request_id, None)
                    self.server.cancelled.discard(request_id)
                self.server.agent_cache.release(session_id)

        with self.streams_lock:
            self.streams[request_id] = threading.current_thread()
        t = threading.Thread(target=cancellable_run, daemon=True)
        t.start()

        # Attend la fin du stream avant de clore la connexion.
        t.join()
        # Fermeture propre du SSE : marque la fin et ferme la connexion.
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        try:
            self.wfile.close()
        except Exception:
            pass

    def _emit_sse(self, name, data):
        payload = json.dumps(data)
        self.wfile.write(f"event: {name}\ndata: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, agent, mode, agent_dir="", state_db_path=None,
                 cache_max=25):
        super().__init__(addr, BridgeHandler)
        self.agent = agent
        self.mode = mode
        self.agent_dir = agent_dir
        self.state_db_path = str(state_db_path or DEFAULT_MOCK_STATE_DB)
        self.cancelled = set()
        self.cancelled_lock = threading.Lock()

        # Fabrique d'agents pour le cache : construit un agent par
        # (session_id, identity). En mode mock, MockAgent est stateless ;
        # en mode real, SessionAgent persiste l'AIAgent par session.
        def agent_factory(session_id, identity):
            if mode == "mock":
                return MockAgent()
            return SessionAgent(agent_dir, session_id, identity)

        self.agent_cache = AgentCache(max_entries=cache_max, factory=agent_factory)

    def state_db_available(self):
        return Path(self.state_db_path).exists()


def build_agent(mode, agent_dir):
    if mode == "mock":
        return MockAgent()
    if mode == "real":
        return RealAgent(agent_dir)
    raise BridgeError(f"unknown mode: {mode}", "BadRequest")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--mode", choices=["mock", "real"], default="mock")
    ap.add_argument("--agent-dir", default=os.environ.get("HERMES_WEBUI_AGENT_DIR", ""))
    ap.add_argument("--state-db", default=os.environ.get("HERMES_WEBUI_STATE_DB", ""))
    ap.add_argument("--cache-max", type=int, default=25)
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("bridge must bind to localhost only (refusing --host %s)" % args.host)

    if args.state_db:
        state_db_path = args.state_db
    elif args.mode == "mock":
        state_db_path = DEFAULT_MOCK_STATE_DB
    else:
        hermes_home = os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
        state_db_path = str(Path(hermes_home) / "state.db")

    agent = build_agent(args.mode, args.agent_dir)
    server = BridgeServer(
        (args.host, args.port), agent, args.mode,
        agent_dir=args.agent_dir, state_db_path=state_db_path,
        cache_max=args.cache_max,
    )
    print(f"bridge listening on http://{args.host}:{args.port} mode={args.mode} "
          f"state_db={state_db_path}", flush=True)

    def shutdown(sig, frame):  # noqa: ARG001
        server.shutdown()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
