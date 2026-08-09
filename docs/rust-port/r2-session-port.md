# R2 — Session Port (Track B)

## Objectif

Porter le domaine Sessions en Rust, fidèle à l'upstream (baseline 192df903),
en préservant la compatibilité du format de session existant.

## Format on-disk (compatibilité critique)

```
STATE_DIR/sessions/<sid>.json = objet JSON plat
  - métadonnées en tête (title, workspace, model, created_at, updated_at, ...)
  - puis messages[], tool_calls[]
  - id = uuid4().hex[:12] (12 hex minuscules)
  - sauvegarde atomique (tmp + fsync + rename)
```

Le port Rust lit/écrit **exactement cette shape** via `serde_json::Map`
round-trip verbatim : un sidecar écrit par Rust reste lisible par le Python
upstream et vice-versa. Aucun format Rust incompatible créé.

## Classification de propriété (sans fusion artificielle)

| Type | Source | Traitement Rust |
|---|---|---|
| WebUI-owned | source=webui, ou défaut | store JSON, lisible/écrivable |
| CLI-owned | source=cli/tui/acp, is_cli_session | classé `SessionOwnership::Cli`, refusé en delete |
| Agent-owned | state.db hermes | JAMAIS fusionné dans le store JSON (hors scope R2) |
| Imported | source_label/source_tag legacy | classé via `classify_ownership()` |

## Routes portées

```
GET  /api/sessions            → liste métadonnées (sidebar rows)
GET  /api/session?session_id  → session complète (messages=0 → sans messages)
POST /api/session/new         → crée (persiste immédiatement, superset sûr)
POST /api/session/rename      → rename titre (apply_title_rename)
POST /api/session/update      → met à jour métadonnées
POST /api/session/delete      → supprime sidecar (+ .bak), refuse CLI/read-only
```

## Fichiers

```
rust-server/src/sessions/mod.rs   (766 L) — Session, SessionOwnership, Store,
  classify_ownership, is_safe_session_id, helpers
rust-server/src/routes/sessions.rs (344 L) — handlers HTTP
rust-server/tests/test_sessions.rs  (16 tests) — couverture obligatoire
```

## Tests (16, tous verts)

empty store, create/get roundtrip, list one/multiple, rename, update metadata,
delete, invalid id, path tampering (traversal rejeté), corrupted metadata,
persistence après reconstruction du store, concurrent reads (8 threads),
classification WebUI/CLI/messaging, routes HTTP (list empty, new+get, 404
invalid, delete).

## Dettes R3

- `state.db` agent (sessions CLI/gateway) non fusionné — nécessite le bridge.
- Import/export de sessions non porté.
- Pinning/archivage partiel (champs présents, UI complète R3).
