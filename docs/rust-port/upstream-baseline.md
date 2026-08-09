# Upstream Baseline — nesquena/hermes-webui

Photographie exacte de l'upstream public utilisé comme référence du port Rust.

## Identité

```
UPSTREAM_REPO = https://github.com/nesquena/hermes-webui
FORK_ED = edoumo/hermes-webui
BASELINE_SHA = 192df903
BASELINE_VERSION = exp-v0.52.192
BASELINE_TAG_DESCRIBE = v0.52.106-383-g192df903 (git describe --tags --match 'v*')
FORK_MASTER_LOCAL = 502f806d (Merge PR #4352)
BRANCHE_PORT = rust-port/r0-r1-bootstrap (créée depuis upstream/master)
DATE_BASELINE = 2026-08-09 (fetch)
```

## Structure

```
server.py            ~750 L  ThreadingHTTPServer shell (dispatch vers api/routes.py)
api/routes.py        1.17 Mo  27 305 L  handle_get/post/put/patch/delete + _serve_static
api/streaming.py     588 Ko   SSE (chat streaming, cancellation, run lifecycle)
api/config.py        469 Ko   HOST/PORT/STATE_DIR/SESSION_DIR/DEFAULT_WORKSPACE, settings, models
api/models.py        431 Ko   catalogue modèles, providers
api/profiles.py      108 Ko   profils, env par requête
api/providers.py     116 Ko   providers, auth status
api/updates.py       106 Ko   WEBUI_VERSION, channel badges, update checks
api/workspace.py      69 Ko   workspace filesystem
api/workspace_git.py  62 Ko   git ops workspace
api/agent_sessions.py 51 Ko   sessions agent
api/auth.py           46 Ko   auth cookies/HMAC/password
api/background_process.py 78 Ko  jobs background
api/kanban_bridge.py  54 Ko   kanban
api/extensions.py     82 Ko   extensions
api/gateway_chat.py   63 Ko   gateway chat
api/route_approvals.py 26 Ko  approvals
api/onboarding.py     46 Ko   onboarding
api/passkeys.py       14 Ko   WebAuthn/passkeys
api/auth_oidc.py      24 Ko   OIDC
api/oauth.py          33 Ko   OAuth
api/upload.py         34 Ko   uploads
api/terminal.py       29 Ko   terminal
api/session_ops.py    26 Ko   opérations sessions
api/helpers.py        43 Ko   helpers
api/paths.py          13 Ko   chemins
api/startup.py         5 Ko   auto-install deps
api/sse_chunked.py     2.5 Ko  SSE chunked
api/webui_session_db.py 9 Ko   session db
api/state_sync.py      8 Ko   sync state
api/session_events.py  4.8 Ko  events
api/session_lifecycle.py 13 Ko lifecycle
api/session_discoverability.py 26 Ko
api/route_session_list_cache.py 23 Ko
api/run_journal.py    31 Ko
api/turn_journal.py   10 Ko
api/todo_state.py     13 Ko
api/goals.py          20 Ko
api/clarify.py         9 Ko
api/commands.py       16 Ko
api/rollback.py       15 Ko
api/metering.py        8 Ko
api/usage.py           1 Ko
api/skill_usage.py     1 Ko
api/request_diagnostics.py 9 Ko
api/crash_visibility.py 11 Ko
api/dashboard_probe.py 10 Ko
api/gateway_watcher.py 20 Ko
api/gateway_restart.py  6 Ko
api/extension_sidecar_auth.py 10 Ko
api/plugin_providers.py 4.8 Ko
api/plugins.py         7 Ko
api/background.py      2.9 Ko
api/runner_client.py   6.6 Ko
api/runtime_adapter.py 18 Ko
api/session_export_html.py 13 Ko
api/office_documents.py 26 Ko
api/system_health.py    6 Ko
api/agent_health.py    31 Ko
api/agent_runtime.py    5 Ko
api/compression_anchor.py 5.4 Ko
api/compression_recovery.py 3.8 Ko
api/updates.py        103 Ko
api/__init__.py        36 B
static/               frontend vanilla JS (index.html 215 Ko, boot.js 172 Ko, messages.js 421 Ko, i18n.js 1.7 Mo, vendor/)
tests/                pytest (conftest.py 61 Ko, ~200 test_*.py, fixtures/, manual/)
bootstrap.py          28 Ko  first-run wizard + launcher
ctl.sh                30 Ko  daemon control
start.sh               7.5 Ko
Dockerfile             4 Ko
docker-compose*.yml    3 fichiers
pyproject.toml         3.3 Ko
requirements.txt       18 L  (pyyaml, cryptography; edge-tts/psutil/office en option)
package.json           622 B
mcp_server.py          23 Ko
flake.nix              4.8 Ko
.github/               CI workflows
docs/                  ARCHITECTURE.md 81 Ko, CONTRACTS.md, rfcs/, onboarding.md, troubleshooting.md
```

## Dépendances runtime

- Python 3.11-3.13 (ThreadingHTTPServer, sqlite3, subprocess)
- Hermes Agent (imports `run_agent.AIAgent`, `hermes_cli.*`) — le WebUI est un shell autour de l'agent
- pyyaml, cryptography (optionnel: edge-tts, psutil, python-docx/openpyxl/python-pptx, websockets)
- TLS optionnel via `HERMES_WEBUI_TLS_CERT`/`HERMES_WEBUI_TLS_KEY` (auto-détecté)
- Auth optionnelle via `HERMES_WEBUI_PASSWORD` (PBKDF2-HMAC-SHA256, 600k itérations)

## Points d'architecture clés (vérifiés dans le code)

1. `server.py` = shell `ThreadingHTTPServer` ; tout le routage est dans `api/routes.py` (dispatch par `parsed.path`).
2. `/health` (routes.py:11785) : JSON `{status, sessions, active_streams, active_runs, runs, last_run_finished_at, server_started_at, uptime_seconds, accept_loop}` ; 503 si dégradé ; `?deep=1` ajoute `checks` (streams_lock, stream_runtime, sessions, projects, state_db).
3. `/static/*` (routes.py:16945) : sandbox `resolve()+relative_to()`, ETag faible `W/"<size:hex>-<mtime_ns:hex>"`, gzip si compressible et >1024 o, `Cache-Control: immutable` si `?v=`, 304 sur If-None-Match.
4. `/` et `/session/<id>` (routes.py:12219) : `static/index.html` avec substitutions `__WEBUI_VERSION__` (URL-encodé), `__MAX_UPLOAD_BYTES__`, `__CSRF_TOKEN_JSON__` ; cache par (size, mtime_ns).
5. `/api/settings` GET (routes.py:12649) : defaults + settings.json, `password_hash` jamais exposé, injections `webui_version`/`agent_version`/`update_channel_version`/`password_env_var`/auth fields.
6. `/api/settings` POST (routes.py:15910) : `save_settings()` (allowlist `_SETTINGS_ALLOWED_KEYS`, enums, ranges), contrôle `_set_password`/`_clear_password`/`_passwordless`, 409 si `HERMES_WEBUI_PASSWORD` posée, réponse SANS les champs version/env du GET.
7. `SETTINGS_FILE = STATE_DIR/settings.json` (config.py:89) ; `DEFAULT_WORKSPACE` = env → ~/workspace → ~/work → STATE_DIR/workspace (config.py:857).
8. `WEBUI_VERSION` = `git describe` du repo (updates.py:606) ; `channel_version_badge` = `git describe --tags --match <glob canal>` (stable=`v*`, experimental=`exp-v*`).
9. TLS auto : `TLS_ENABLED = bool(HERMES_WEBUI_TLS_CERT) and bool(HERMES_WEBUI_TLS_KEY)` (config.py:72-74).
10. Auth : `is_auth_enabled()` = password_hash présent (settings.json ou env) ; cookies de session ; CSRF token par session injecté dans index.html.

## CI / tests

- `.github/workflows/` : pytest (Linux), lint, tests navigateur (Playwright) — vérifié par présence des workflows.
- `tests/` : pytest avec `conftest.py` (isolation réseau `HERMES_WEBUI_TEST_NETWORK_BLOCK`, HOME/state isolés), helpers navigateur.
- `scripts/test.sh` : wrapper pytest (venv repo, Python 3.11-3.13).

## Classification rapide (détail dans module-migration-matrix.md)

- RUST_DIRECT : health, static, index shell, settings (GET/POST), config pure, workspace read-only
- HERMES_BRIDGE_REQUIRED : chat/start, streaming, sessions agent, models, providers, memory, skills, cron, approvals, terminal, background_process, kanban, gateway_chat
- KEEP_PYTHON_INITIAL : tout ce qui importe `run_agent`/`hermes_cli` en profondeur tant que le bridge n'existe pas
- FRONTEND_ONLY : static/*, i18n, thèmes
