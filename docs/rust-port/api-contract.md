# API Contract — Hermes WebUI (baseline 192df903)

Spécification comportementale du backend, extraite du code source (routes.py,
config.py, updates.py). Chaque route est référencée par fichier:ligne.
Règle R0/R1 : **upstream behavior = référence** — aucune API inventée.

## Conventions générales

- Serveur : `ThreadingHTTPServer` (server.py), dispatch par `parsed.path` dans
  `api/routes.py` (`handle_get` L12197, `handle_post` L15910+).
- Réponses JSON : `j(handler, payload)` → `application/json` (charset utf-8).
- Erreurs : `bad(handler, msg, status)` → `{"error": "<msg>"}`.
- Auth : si `HERMES_WEBUI_PASSWORD` posée ou `password_hash` dans settings.json,
  les routes `/api/*` (hors /health, /static, /, /login) répondent 401
  `{"error": "Authentication required"}` sans cookie valide.
- TLS : auto si `HERMES_WEBUI_TLS_CERT` + `HERMES_WEBUI_TLS_KEY` (config.py:72).

## Routes documentées (portées en Rust R1)

### GET /health — routes.py:11785

```
QUERY = deep (1|true|yes|on)
200 = {"status":"ok","sessions":N,"active_streams":N,"active_runs":N,"runs":[],
       "last_run_finished_at":null,"server_started_at":<epoch float>,
       "uptime_seconds":<float>,"accept_loop":{"requests_total":N,"last_request_at":<float>}}
503 = même shape avec status != "ok" (streams lock bloqué / run lifecycle)
DEEP = ajoute "checks": {streams_lock, stream_runtime, sessions, projects, state_db}
       (routes.py:11704 _deep_health_checks) ; 503 si un check n'est pas ok/missing
```

### GET /static/* — routes.py:16945

```
SANDBOX = resolve()+relative_to(static_root) sinon 404 {"error":"not found"}
MIME = _STATIC_MIME (css/js/html/svg/png/jpg/jpeg/ico/gif/webp/woff/woff2)
TEXT = + "; charset=utf-8" (css/js/html/svg/plain)
ETAG = W/"<size:hex>-<mtime_ns:hex>" ; 304 si If-None-Match == etag
GZIP = si MIME compressible ET taille > 1024 ET Accept-Encoding contient gzip
       → Content-Encoding: gzip + Vary: Accept-Encoding
CACHE = "public, max-age=31536000, immutable" si ?v= présent, sinon "public, max-age=300"
```

### GET / , /index.html, /session/<id> — routes.py:12219

```
BODY = static/index.html avec substitutions :
       __WEBUI_VERSION__ → quote(WEBUI_VERSION, safe="")
       __MAX_UPLOAD_BYTES__ → str(MAX_UPLOAD_BYTES) (défaut 20 MiB, env HERMES_WEBUI_MAX_UPLOAD_MB)
       __CSRF_TOKEN_JSON__ → json.dumps(csrf_token) ("" si auth désactivée)
CT = text/html; charset=utf-8
/session/static/* → _serve_static (préfixe /session retiré)
/session/manifest.json|webmanifest → _serve_manifest
```

### GET /api/settings — routes.py:12649

```
200 = _SETTINGS_DEFAULTS (config.py:9260) fusionnés avec settings.json (STATE_DIR/settings.json),
      PUIS :
      - password_hash JAMAIS exposé (pop)
      - max_tokens/max_tokens_effective/max_tokens_fallback = null
      - password_env_var = bool(HERMES_WEBUI_PASSWORD)
      - auth_enabled / password_auth_enabled / passkeys_enabled / passwordless_enabled
      - webui_version = WEBUI_VERSION ; agent_version = AGENT_VERSION
      - update_channel = canal lu des settings ; update_channel_version = channel_version_badge()
      - persisted_speech_keys = clés speech présentes dans le fichier stocké
      - default_workspace = env HERMES_WEBUI_DEFAULT_WORKSPACE → ~/workspace → ~/work → STATE_DIR/workspace
```

### POST /api/settings — routes.py:15910

```
BODY = n'importe quelles clés ; validation par allowlist _SETTINGS_ALLOWED_KEYS,
       enums (_SETTINGS_ENUM_VALUES), int/float ranges, bool keys, language regex,
       listes (hidden_tabs/tab_order/composer_control_order) nettoyées
BOT_NAME = strip, fallback "Hermes"
CONTROL = _set_password/_clear_password/_passwordless/_current_password/max_tokens
          (jamais persistés ; max_tokens → set_max_tokens)
409 = si _set_password/_clear_password ET HERMES_WEBUI_PASSWORD posée
403 = si auth activée et changement password sans _current_password valide
200 = saved (settings fusionnés) + persisted_speech_keys + max_tokens status
      + auth_enabled/password_auth_enabled/logged_in/auth_just_enabled
      SANS webui_version/agent_version/update_channel_version/password_env_var
```

## Routes identifiées (non portées R1 — inventaire pour R2)

| Domaine | Routes (échantillon vérifié) | Fichier |
|---|---|---|
| Sessions | /api/sessions, /api/session?session_id=, /api/session/new, /api/session/update, /api/session/delete, /api/session/branch, /api/session/export, /api/session/import, /api/session/draft | routes.py |
| Chat/streaming | /api/chat/start, /api/chat/cancel, /api/chat/stream (SSE), /api/chat/approve, /api/chat/reject | routes.py, streaming.py |
| Models | /api/models, /api/models/live, /api/model/auxiliary, /api/reasoning | routes.py |
| Providers | /api/providers, /api/providers/auth/status | routes.py |
| Profiles | /api/profiles, /api/profile/active, /api/profile/switch | routes.py |
| Workspace | /api/workspace/*, /api/workspace/git/* | routes.py, workspace.py |
| Uploads | /api/upload/* | upload.py |
| Auth | /api/auth/login, /api/auth/logout, /api/auth/status, /api/auth/password | auth.py |
| Passkeys | /api/passkeys/* | passkeys.py |
| Cron | /api/cron/* | routes.py (cron/jobs) |
| Memory | /api/memory | routes.py |
| Skills | /api/skills | routes.py |
| Approvals | /api/approval/pending, /api/approval/respond | route_approvals.py |
| TTS | /api/tts, /api/transcribe, /api/transcribe/capability | routes.py |
| Updates | /api/update/*, /api/updates | updates.py |
| Divers | /api/commands, /api/commands/bundles, /api/background/status, /api/logs, /api/dashboard/status, /api/dashboard/config, /api/system/health, /api/health/agent, /api/shutdown | routes.py |

## Fixtures

- `tests/compat/fixtures/` : à compléter au fil des tracks. Les fixtures de
  référence R1 sont les réponses réelles capturées des deux serveurs pendant
  les runs du harnais (voir `tests/compat/compare.py`).
