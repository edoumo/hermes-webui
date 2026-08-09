# Handoff — Hermes WebUI Rust R2 (état de reprise)

TIMESTAMP = 2026-08-09T23:55:00Z
BRANCHE = rust-port/r2-agent-bridge
BASELINE_UPSTREAM = 192df903 (exp-v0.52.192)
UPSTREAM_ACTUEL = bd91b649 (exp-v0.52.193) — delta NO_IMPACT (a11y frontend)
R1_CHECKPOINT = rust-port/r0-r1-bootstrap (intact)

## Architecture

```
Browser / client
  -> Rust Axum WebUI (rust-server/)
       -> Bridge Python localhost (bridge/server.py, protocole v1)
            -> Hermes Agent / AIAgent
```

## Routes Rust portées (R1 + R2)

```
GET  /health (+?deep=1)              GET  /static/* (cache mémoire)
GET  / , /index.html, /session/*     GET+POST /api/settings
GET  /api/sessions                   GET  /api/session?session_id
POST /api/session/new|rename|update|delete
GET  /api/workspace/list|read|metadata|download (read-only, sandbox)
POST /api/auth/login|logout          GET  /api/auth/status
GET  /api/bridge/health              POST /api/chat/proxy (SSE)
```

## Bridge (protocole v1, DONE + commit)

```
bridge/PROTOCOL.md — spec v1
bridge/server.py   — mock (CI) + real (AIAgent)
bridge/mock_llm.py — echo LLM OpenAI-compatible (vertical slice sans LLM payant)
Endpoints : /v1/health, /v1/version, /v1/chat (SSE), /v1/cancel, /v1/streams
Events : start, reasoning, token, tool_start, tool_result, approval_required,
         error, usage, done, cancelled
Vertical slice prouvée : AIAgent -> echo LLM -> bridge -> Rust -> client
```

## Qualité (gate d'intégration, re-collectée)

```
CARGO_FMT = PASS
CARGO_BUILD = PASS (debug + release 26.4s)
CARGO_CLIPPY = 0 warnings
CARGO_TEST = 55 passed (10 suites)
  - test_health 3, test_static 6 (dont cache), test_index 3, test_settings 4
  - test_sessions 16, test_workspace 14, test_auth 9
BRIDGE_NON_REGRESSION = PASS (mock : start→reasoning→token→tool_start→tool_result→token→done ; cancel OK)
```

## Performance release (docs/rust-port/r2-benchmark-release.md)

```
Rust release : RSS 6.1 Mo, /health 0.27 ms, boot.js warm 0.28 ms, démarrage <1 s
Python       : RSS 77 Mo, /health 0.92 ms, boot.js warm 0.69 ms, démarrage ~8 s
Concurrence 50/100 : PASS
```

## Dettes R2 (explicites)

```
AUTH_PASSWORD = R3_IMPLEMENT (PBKDF2 600k non porté ; login sans password sur state test)
AUTH_WEBAUTHN = R3_DEFER
UPLOADS = R3_DEFER (décision scope §16 du mandat de reprise)
AGENT_SESSIONS_STATE_DB = R3 (fusion state.db via bridge)
IMPORT_EXPORT_SESSIONS = R3
WORKSPACE_MUTATIONS = R3 (read-only R2 suffisant)
BRIDGE_AGENT_CACHE = R3 (SESSION_AGENT_CACHE upstream)
```

## Commandes reproductibles

```bash
# Tests
cd rust-server && cargo fmt --check && cargo clippy --all-targets && cargo test

# Bridge mock + Rust
cd bridge && python3 server.py --mode mock --port 8794 &
cd rust-server && HERMES_WEBUI_BRIDGE_URL=http://127.0.0.1:8794 \
  ./target/release/hermes-webui-rust --host 127.0.0.1 --port 8792 --repo-dir ..

# Vertical slice réelle (echo LLM)
cd bridge && python3 mock_llm.py --port 8796 &
# config.yaml isolé : model.provider=custom, base_url=http://127.0.0.1:8796/v1
PYTHONPATH=/usr/local/lib/hermes-agent HERMES_HOME=/tmp/hwui-bridge-home \
  /home/edou/hermes-webui/.venv/bin/python server.py --mode real --port 8795

# Compat harness (Python upstream vs Rust)
python3 tests/compat/compare.py --python-url http://127.0.0.1:8793 --rust-url http://127.0.0.1:8792
```

## Red lines

```
PUSH = NON | PR = NON | UPSTREAM = NON | PROD_WEBUI = NON_TOUCHÉ
DONNÉES RÉELLES = NON | BRIDGE = localhost uniquement | SECRETS = AUCUN
PROFESSEUR GPT-5.6 = NON INVOQUÉ
```

## Prochain macro-lot

```
NEXT = R3_RUST_PORT
PRIORITÉ_1 = Bridge agent cache + sessions state.db (fusion via bridge)
PRIORITÉ_2 = Auth password réel (PBKDF2) + WebAuthn
PRIORITÉ_3 = Uploads
PRIORITÉ_4 = Compat harness v2 étendu (>=40 scénarios) + SSE stateful
PRIORITÉ_5 = Sync upstream exp-v0.52.193 (NO_IMPACT, à re-vérifier)
```
