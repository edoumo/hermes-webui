# R3 — Import / Export de sessions (Track F)

## Objectif

Porter les routes d'import/export de sessions du WebUI upstream en Rust,
avec le même contrat (upstream behavior = référence).

## Routes portées

| Route | Méthode | Contrat upstream | Port Rust |
|---|---|---|---|
| `/api/session/export` | GET | `_handle_session_export` (api/routes.py:17019) | ✅ `routes/import_export.rs` |
| `/api/session/import` | POST | `_handle_session_import` (api/routes.py:26390) | ✅ `routes/import_export.rs` |
| `/api/session/import_cli` | POST | `_handle_session_import_cli` (api/routes.py:26145) | ⛔ HERMES_BRIDGE_REQUIRED |

## Contrats vérifiés upstream

### GET /api/session/export
- Query : `session_id` (requis → 400 "session_id is required"), `format` (json|html,
  défaut json, lowercase), `theme` (défaut dark, HTML), `palette` (base64 JSON,
  cap 64 clés → sinon ignoré).
- 404 "Session not found" si session inconnue OU profil mismatch.
- JSON : `json.dumps(redact_session_data(s.__dict__), ensure_ascii=False, indent=2)`.
- HTML : `render_session_html` (api/session_export_html.py:220) — document
  autonome, pas d'assets externes, images distantes neutralisées.
- Headers : Content-Type (json | text/html), Content-Disposition
  `attachment; filename="hermes-{sid}.{ext}"`, Cache-Control no-store.

### POST /api/session/import
- Body JSON objet (sinon 400 "Request body must be a JSON object").
- `messages` requis, liste (sinon 400 'JSON must contain a "messages" array').
- `title` défaut "Imported session" · `workspace` résolu par
  resolve_trusted_workspace (hors racine → 400) · `model` défaut DEFAULT_MODEL ·
  `tool_calls` défaut [] · `pinned` défaut false.
- Crée une NOUVELLE session (nouvel id uuid4().hex[:12]), persistée immédiatement
  (écriture atomique), réponse `{ok: true, session: compact() | {messages}}`.

### POST /api/session/import_cli — CLASSÉ HERMES_BRIDGE_REQUIRED
Dépend du CLI store hermes-agent (`get_cli_session_messages`,
`_resolve_cli_import_metadata`, refresh préfixe, métadonnées CLI, subagent
read-only). Pas portable en Rust pur sans bridge → NON implémenté (pas de
store fabriqué). Track R4 candidate via bridge v2 (endpoint `/v1/sessions`
existant + extension import_cli).

## Implémentation Rust

```
Fichier  : rust-server/src/routes/import_export.rs
Router   : pub fn router() -> axum::Router<AppState>
Routes   : GET /api/session/export, POST /api/session/import
```

- Réutilise `crate::sessions::{Session, Store}` (store JSON unique, pas de
  second store) : génération d'id, atomic save (tmp+fsync+rename), load.
- Redaction réponse-layer : port du fallback upstream `_CRED_RE` + `_ENV_RE` +
  `_PRIVKEY_RE` (masque `sk-***`, env KEY=secret, clés privées PEM). Le
  sidecar disque n'est JAMAIS modifié.
- Workspace : `resolve_trusted_workspace` (canonicalize + starts_with root).
- HTML : document autonome minimal (pas de dépendance markdown_it côté Rust) —
  **dette documentée** : rendu Markdown simplifié (code blocks, inline code,
  gras) vs CommonMark complet upstream. Contenu toujours HTML-escaped (safe
  par construction), images distantes neutralisées (même frontière de
  confidentialité qu'upstream).

## Wiring (déjà appliqué par l'intégrateur)

```rust
// rust-server/src/routes/mod.rs
pub mod import_export;

// rust-server/src/app.rs — dans build_router()
.merge(crate::routes::import_export::router())
```

## Tests

```
rust-server/tests/test_import_export.rs — 14 tests PASS
  export : 400 sans session_id, 404 inconnu, JSON round-trip + headers
           (content-type, Content-Disposition hermes-{sid}.json, no-store),
           redaction credentials, HTML content-type + CD hermes-{sid}.html,
           palette capée, format invalide → JSON.
  import : 400 body non-objet, 400 messages manquant/non-liste, workspace
           hors racine → 400, round-trip export→import (nouvel id, messages
           présents, persisté disque), pinned/tool_calls conservés (sur
           disque — compact() upstream n'expose pas tool_calls), défauts
           title/model, messages vide OK.
```

## Deltas documentés (jamais masqués)

1. `import_cli` non porté (HERMES_BRIDGE_REQUIRED) — aucune route bidon.
2. HTML export : markdown simplifié (pas de markdown_it) — safe, autonome.
3. Redaction : port du fallback local (patterns principaux), pas du pipeline
   hermes-agent complet (~15 regex) — fail-closed sur les marqueurs connus.

## Statut

```
TRACK_F = DONE (14/14 tests, build 0 err, wiring appliqué)
```
