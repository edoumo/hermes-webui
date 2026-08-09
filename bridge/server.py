#!/usr/bin/env python3
"""Hermes Agent Bridge — Mince adaptateur Python entre Hermes WebUI Rust
et Hermes Agent (AIAgent).

Transport : HTTP sur 127.0.0.1 + SSE pour le streaming (protocole v1, voir
PROTOCOL.md). Deux modes :
  - real : wrappe AIAgent (import depuis le chemin Hermes Agent).
  - mock : flux déterministe pour tests CI (aucun LLM payant/Internet).

Le bridge ne manipule aucun secret et ne s'expose jamais hors localhost.
"""

import json
import os
import queue
import signal
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROTOCOL_VERSION = "1.0"
BRIDGE_VERSION = "0.1.0"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8794


class BridgeError(Exception):
    def __init__(self, message, error_class="Internal"):
        super().__init__(message)
        self.error_class = error_class


# ---------------------------------------------------------------------------
# Mock agent — déterministe, sans réseau/LLM, pour les tests CI.
# ---------------------------------------------------------------------------
class MockAgent:
    """Émule un flux agentique structurellement fidèle : start → reasoning →
    token(s) → tool_start → tool_result → token → done. Aucun appel LLM."""

    def stream(self, request_id, session_id, message, history, callbacks):
        emit = callbacks
        emit("start", {"request_id": request_id})
        emit("reasoning", {"request_id": request_id, "text": "[mock] reasoning..."})
        emit("token", {"request_id": request_id, "text": "Bonjour depuis le bridge mock. "})
        emit("tool_start", {
            "request_id": request_id,
            "name": "mock_tool",
            "arguments": {"input": message},
        })
        emit("tool_result", {
            "request_id": request_id,
            "name": "mock_tool",
            "result": {"ok": True},
        })
        emit("token", {"request_id": request_id, "text": "Tool exécuté."})
        final = "Réponse mock complète (déterministe)."
        emit("done", {"request_id": request_id, "final_response": final})
        return final


# ---------------------------------------------------------------------------
# Real agent wrapper — AIAgent (Hermes Agent Python).
# ---------------------------------------------------------------------------
class RealAgent:
    """Wrappe AIAgent via les callbacks de streaming. Import paresseux du
    chemin Hermes Agent pour ne pas dépendre d'un venv particulier ici."""

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

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/v1/health":
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
            })
        elif path == "/v1/version":
            self._send_json(200, {
                "protocol_version": PROTOCOL_VERSION,
                "bridge_version": BRIDGE_VERSION,
            })
        elif path == "/v1/streams":
            with self.streams_lock:
                active = list(self.streams.keys())
            self._send_json(200, {"active": active})
        else:
            self._send_error_json(404, "not found", "NotFound")

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        try:
            if path == "/v1/chat":
                self._handle_chat()
            elif path == "/v1/cancel":
                self._handle_cancel()
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
        }.get(error_class, 500)

    def _handle_cancel(self):
        body = self._read_json_body()
        request_id = body.get("request_id")
        if not request_id:
            raise BridgeError("request_id required", "BadRequest")
        # L'annulation est gérée par un flag partagé consulté par le stream.
        self.server.cancelled.add(request_id)
        self._send_json(200, {"cancelled": True})

    def _handle_chat(self):
        body = self._read_json_body()
        request_id = body.get("request_id") or str(uuid.uuid4())
        session_id = body.get("session_id") or "mock-session"
        message = body.get("message", "").strip()
        if not message:
            raise BridgeError("message required", "BadRequest")
        history = body.get("history") or []

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
                agent = self.server.agent
                if isinstance(agent, MockAgent):
                    agent.stream(request_id, session_id, message, history, emit)
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

    def __init__(self, addr, agent, mode):
        super().__init__(addr, BridgeHandler)
        self.agent = agent
        self.mode = mode
        self.cancelled = set()
        self.cancelled_lock = threading.Lock()


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
    args = ap.parse_args()

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("bridge must bind to localhost only (refusing --host %s)" % args.host)

    agent = build_agent(args.mode, args.agent_dir)
    server = BridgeServer((args.host, args.port), agent, args.mode)
    print(f"bridge listening on http://{args.host}:{args.port} mode={args.mode}", flush=True)

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
