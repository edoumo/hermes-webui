# R2 — Hermes Agent Bridge

## Objectif atteint

R2 débloque la communication réelle entre Hermes WebUI Rust et Hermes Agent
via un bridge local HTTP/SSE. La vertical slice complète est prouvée :

```
Client (curl)
  -> Rust Axum WebUI (/api/chat/proxy)
       -> Bridge Python localhost (/v1/chat, SSE)
            -> Hermes Agent / AIAgent
                 -> (provider LLM, ou echo LLM mock pour tests)
```

## Architecture

```
Browser / client
   |  POST /api/chat/proxy {request_id, session_id, message, ...}
   v
Rust Axum (rust-server/src/hermes/bridge.rs)
   |  HTTP POST http://127.0.0.1:8794/v1/chat (SSE)
   v
Bridge Python (bridge/server.py) — MINCE, localhost uniquement
   |  wrappe AIAgent via callbacks
   v
Hermes Agent / AIAgent (run_agent)
```

Le bridge est mince : il adapte les APIs Python natives Hermes vers un
contrat transportable (protocole v1). Il ne devient pas un deuxième backend.

## Protocole (voir bridge/PROTOCOL.md)

- Transport : HTTP sur 127.0.0.1 + SSE pour le streaming.
- Endpoints : `GET /v1/health`, `GET /v1/version`, `POST /v1/chat` (SSE),
  `POST /v1/cancel`, `GET /v1/streams`.
- Events SSE : `start`, `token`, `reasoning`, `tool_start`, `tool_result`,
  `approval_required`, `error`, `usage`, `done`, `cancelled`.
- `protocol_version` = "1.0".
- `request_id` strictement associé à un stream ; cancellation ciblée.

## Modes

| Mode | Description | Usage |
|---|---|---|
| `mock` | Flux déterministe structurellement fidèle (start→reasoning→token→tool→done) | Tests CI, aucun LLM payant |
| `real` | Wrappe AIAgent via callbacks (stream_delta, reasoning, tool) | Vertical slice réelle |

Pour la vertical slice réelle sans LLM payant, un **echo LLM** local
(bridge/mock_llm.py, OpenAI-compatible `/v1/chat/completions`) est utilisé
avec un HERMES_HOME isolé pointant dessus.

## Lifecycle & sécurité

- Écoute uniquement sur 127.0.0.1 (refuse --host non-localhost).
- `request_id` → un seul stream actif.
- `POST /v1/cancel` marque l'annulation ; le stream émet `cancelled`.
- Shutdown propre SIGTERM/SIGINT.
- Aucun secret dans les events/logs.
- Aucune exposition sur 0.0.0.0.

## Preuve de vertical slice

### Mock (CI, déterministe)
```
event: start → reasoning → token → tool_start → tool_result → token → done → [DONE]
```

### Real (echo LLM local, via AIAgent)
```
event: start
event: token {text: "[echo:echo-model] "}
event: token {text: "dis "}
event: token {text: "bonjour "}
event: done  {final_response: "[echo:echo-model] dis bonjour"}
```

Les deux flux traversent `AIAgent -> bridge -> Rust -> client` avec
streaming observable.

## Commandes de lancement dev

```bash
# Bridge mock (tests CI)
cd bridge && python3 server.py --mode mock --port 8794

# Echo LLM + bridge real (vertical slice sans LLM payant)
cd bridge && python3 mock_llm.py --port 8796
mkdir -p /tmp/hwui-bridge-home
# config.yaml isolé pointant sur http://127.0.0.1:8796/v1
PYTHONPATH=/usr/local/lib/hermes-agent HERMES_HOME=/tmp/hwui-bridge-home \
  /home/edou/hermes-webui/.venv/bin/python server.py --mode real --port 8795

# Rust (pointe sur le bridge)
cd rust-server && cargo build
HERMES_WEBUI_BRIDGE_URL=http://127.0.0.1:8794 \
  ./target/debug/hermes-webui-rust --host 127.0.0.1 --port 8792 --repo-dir ..
```

## Dette / limites R2

- Le bridge real instancie un `AIAgent` par requête ; la mise en cache des
  agents par session (comme upstream SESSION_AGENT_CACHE) est un travail R3.
- Les outils externes sont désactivés côté bridge (`enabled_toolsets=[]`) pour
  éviter de déclencher des effets de bord pendant les tests ; leur activation
  contrôlée est un travail R3.
- Le password réel (PBKDF2 600k) n'est pas porté en R2 (fondation auth
  uniquement) ; voir r2-auth-security.md.
