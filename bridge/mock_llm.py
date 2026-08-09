#!/usr/bin/env python3
"""Mini serveur LLM 'echo' — OpenAI-compatible /v1/chat/completions, déterministe,
sans réseau externe. Utilisé pour prouver la vertical slice réelle du bridge
(Hermes Agent -> Bridge -> Rust -> client) sans LLM payant/Internet."""

import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "echo-model"


class EchoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/v1/chat/completions"):
            self._chat_completions()
        elif self.path.rstrip("/").endswith("/v1/responses"):
            self._responses()
        else:
            self._send({"error": "not found"}, 404)

    def do_GET(self):  # noqa: N802
        if self.path.rstrip("/").endswith("/v1/models"):
            self._send({"object": "list", "data": [{
                "id": MODEL, "object": "model", "owned_by": "echo"
            }]})
        else:
            self._send({"error": "not found"}, 404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            return {}

    def _chat_completions(self):
        body = self._read_body()
        messages = body.get("messages", [])
        stream = body.get("stream", False)
        user_text = ""
        for m in messages:
            if m.get("role") == "user":
                user_text = str(m.get("content", ""))
        echo = f"[echo:{MODEL}] {user_text}"

        if stream:
            self._send_sse_chunks(echo)
            return

        self._send({
            "id": "chatcmpl-echo-1",
            "object": "chat.completion",
            "model": MODEL,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": echo},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
        })

    def _send_sse_chunks(self, echo):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.flush_headers()

        def chunk(text_delta, finish=None, index=0):
            obj = {
                "id": "chatcmpl-echo-1",
                "object": "chat.completion.chunk",
                "model": MODEL,
                "choices": [{
                    "index": index,
                    "delta": {"role": "assistant", "content": text_delta} if text_delta else {},
                    "finish_reason": finish,
                }],
            }
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode("utf-8"))
            self.wfile.flush()

        # Simule un vrai streaming token par token.
        for token in echo.split(" "):
            chunk(token + " ")
            time.sleep(0.03)
        chunk("", "stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def _responses(self):
        body = self._read_body()
        inp = body.get("input", "")
        if isinstance(inp, list):
            inp = " ".join(str(x.get("content", "")) for x in inp if isinstance(x, dict))
        echo = f"[echo:{MODEL}] {inp}"
        self._send({
            "id": "resp-echo-1",
            "object": "response",
            "model": MODEL,
            "output": [{
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": echo}],
            }],
        })


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8796)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), EchoHandler)
    print(f"echo-llm listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
