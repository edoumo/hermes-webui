# Module Migration Matrix — Hermes WebUI → Rust

Cartographie des responsabilités des modules backend Python (baseline 192df903)
et classification de portage. Chaque module est classé selon son comportement
réellement couvert, pas sa taille.

## Légende

- **RUST_DIRECT** : portable tel quel (pas de dépendance Hermes Agent)
- **RUST_AFTER_DEPENDENCY** : portable une fois une dépendance (ex. sessions) portée
- **HERMES_BRIDGE_REQUIRED** : nécessite le bridge Hermes Agent (Track G)
- **KEEP_PYTHON_INITIAL** : reste en Python au démarrage (couplage agent profond)
- **FRONTEND_ONLY** : pas de backend

## Matrice

| Module | Responsabilité | Routes | Hermes Agent | SQLite | SSE | Classement | Difficulté |
|---|---|---|---|---|---|---|---|
| server.py | Shell HTTP (ThreadingHTTPServer) | toutes | non | non | non | RUST_DIRECT (remplacé par axum) | facile |
| api/routes.py | Dispatch GET/POST/PUT/PATCH/DELETE | toutes | oui (imports) | oui | oui | KEEP_PYTHON_INITIAL (monolithe) | — |
| api/health.py (dans routes) | /health, /health?deep=1 | /health, /api/health/agent, /api/system/health | partiel (agent_health) | oui (state_db) | non | RUST_DIRECT (porté R1) | facile |
| api/static (dans routes) | assets statiques, index shell | /static/*, /, /session/* | non | non | non | RUST_DIRECT (porté R1) | facile |
| api/config.py | HOST/PORT/STATE_DIR, settings, models, env | /api/settings, /api/models | oui (get_available_models) | non | non | RUST_DIRECT (settings porté R1) / HERMES_BRIDGE pour models | moyen |
| api/settings (dans config.py) | load/save settings.json | /api/settings GET/POST | non | non | non | RUST_DIRECT (porté R1) | facile |
| api/updates.py | WEBUI_VERSION, channel badges, update checks | /api/update/*, /api/updates | oui (agent version) | non | non | RUST_AFTER_DEPENDENCY (version portée R1) | moyen |
| api/auth.py | cookies, HMAC, password, CSRF | /api/auth/*, login | non | non | non | RUST_DIRECT (R2) | moyen |
| api/passkeys.py | WebAuthn/passkeys | /api/passkeys/* | non | non | non | RUST_DIRECT (R2+) | difficile |
| api/auth_oidc.py | OIDC | /api/auth/oidc/* | non | non | non | RUST_DIRECT (R2+) | difficile |
| api/oauth.py | OAuth | /api/oauth/* | non | non | non | RUST_DIRECT (R2+) | difficile |
| api/onboarding.py | wizard premier run | /api/onboarding/* | oui (providers) | non | non | RUST_AFTER_DEPENDENCY | moyen |
| api/profiles.py | profils, env par requête | /api/profiles, /api/profile/* | oui (config.yaml) | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/providers.py | providers, auth status | /api/providers | oui (config.yaml, creds) | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/models.py | catalogue modèles | /api/models, /api/models/live | oui (hermes_cli.models) | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/agent_sessions.py | sessions agent | /api/sessions | oui (state.db) | oui | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/session_ops.py | opérations sessions | /api/session/* | oui | oui | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/session_lifecycle.py | lifecycle sessions | /api/session/* | oui | oui | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/session_events.py | events sessions | /api/session/events | oui | oui | oui | HERMES_BRIDGE_REQUIRED | moyen |
| api/session_discoverability.py | découverte sessions | /api/sessions | oui | oui | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/route_session_list_cache.py | cache liste sessions | /api/sessions | oui | oui | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/streaming.py | SSE chat, cancellation | /api/chat/*, /api/stream/* | oui (AIAgent) | oui | oui | HERMES_BRIDGE_REQUIRED | difficile |
| api/gateway_chat.py | chat gateway | /api/gateway/chat | oui (gateway) | non | oui | HERMES_BRIDGE_REQUIRED | difficile |
| api/workspace.py | workspace filesystem | /api/workspace/* | oui (chemins) | non | non | RUST_AFTER_DEPENDENCY (R2) | moyen |
| api/workspace_git.py | git ops workspace | /api/workspace/git/* | non | non | non | RUST_DIRECT (R2) | moyen |
| api/upload.py | uploads | /api/upload/* | non | non | non | RUST_DIRECT (R2) | facile |
| api/terminal.py | terminal | /api/terminal/* | oui (subprocess) | non | oui | HERMES_BRIDGE_REQUIRED | difficile |
| api/background_process.py | jobs background | /api/background/* | oui | non | oui | HERMES_BRIDGE_REQUIRED | difficile |
| api/kanban_bridge.py | kanban | /api/kanban/* | oui | oui | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/extensions.py | extensions | /api/extensions/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/route_approvals.py | approvals | /api/approval/* | oui | non | oui | HERMES_BRIDGE_REQUIRED | moyen |
| api/clarify.py | clarifications | /api/clarify/* | oui | non | oui | HERMES_BRIDGE_REQUIRED | moyen |
| api/commands.py | commandes | /api/commands/* | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/goals.py | goals | /api/goals/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/todo_state.py | todos | /api/todo/* | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/run_journal.py | journal runs | /api/run/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/turn_journal.py | journal turns | /api/turn/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/rollback.py | rollback | /api/rollback/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/metering.py | metering | /api/metering/* | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/usage.py | usage | /api/usage | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/skill_usage.py | usage skills | /api/skill_usage | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/request_diagnostics.py | diagnostics requêtes | middleware | non | non | non | RUST_DIRECT (R2) | facile |
| api/crash_visibility.py | visibilité crash | middleware | non | non | non | RUST_DIRECT (R2) | facile |
| api/dashboard_probe.py | dashboard status | /api/dashboard/* | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/gateway_watcher.py | watcher gateway | /api/gateway/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/gateway_restart.py | restart gateway | /api/gateway/restart | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/extension_sidecar_auth.py | auth sidecar extensions | /api/extensions/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/plugin_providers.py | providers plugins | /api/plugins/* | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/plugins.py | plugins | /api/plugins/* | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/background.py | background tasks | /api/background/* | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/runner_client.py | runner client | /api/runner/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/runtime_adapter.py | adapter runtime | /api/runtime/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/session_export_html.py | export HTML | /api/session/export | oui | non | non | RUST_AFTER_DEPENDENCY | moyen |
| api/office_documents.py | preview office | /api/workspace/* | non | non | non | RUST_DIRECT (R2) | moyen |
| api/system_health.py | health système | /api/system/health | non | non | non | RUST_DIRECT (R2) | facile |
| api/agent_health.py | health agent | /api/health/agent | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/agent_runtime.py | runtime agent | /api/agent/* | oui | non | non | HERMES_BRIDGE_REQUIRED | facile |
| api/compression_anchor.py | ancres compression | /api/session/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/compression_recovery.py | recovery compression | /api/session/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/shares.py | partages | /api/share/* | oui | non | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/state_sync.py | sync état | /api/state/* | oui | oui | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/webui_session_db.py | db sessions webui | /api/sessions | oui | oui | non | HERMES_BRIDGE_REQUIRED | moyen |
| api/sse_chunked.py | SSE chunked | /api/chat/* | non | non | oui | RUST_DIRECT (R2) | facile |
| api/startup.py | auto-install deps | — | oui | non | non | KEEP_PYTHON_INITIAL | facile |
| api/helpers.py | helpers | toutes | oui | non | non | RUST_AFTER_DEPENDENCY | moyen |
| api/paths.py | chemins | toutes | oui | non | non | RUST_DIRECT (R2) | facile |
| bootstrap.py | wizard + launcher | — | oui | non | non | KEEP_PYTHON_INITIAL | — |
| ctl.sh / start.sh | contrôle daemon | — | non | non | non | KEEP_PYTHON_INITIAL (scripts) | — |
| mcp_server.py | MCP server | — | oui | non | non | KEEP_PYTHON_INITIAL | — |

## Synthèse

- **RUST_DIRECT (porté R1)** : health, static, index shell, settings — 4 domaines, ~1 200 L Rust.
- **RUST_DIRECT (R2)** : auth, upload, workspace read-only, office preview, system_health, sse_chunked, request_diagnostics, crash_visibility, paths.
- **RUST_AFTER_DEPENDENCY** : updates (version), onboarding, session_export_html, helpers, workspace mutations.
- **HERMES_BRIDGE_REQUIRED** : ~35 modules — tout ce qui touche l'agent (chat, streaming, sessions, models, providers, profiles, memory, skills, cron, approvals, terminal, background, kanban, gateway).
- **KEEP_PYTHON_INITIAL** : routes.py (monolithe), startup.py, bootstrap.py, ctl.sh, mcp_server.py.

Le cœur du port R2+ est le **bridge Hermes Agent** (Track G) : sans lui, ~70 % des routes restent Python.
