# R2 — Workspace Port (Track C)

## Objectif

Porter le workspace read-only en Rust : navigateur de fichiers réellement
utilisable, avec sécurité absolue.

## Routes portées (read-only)

```
GET /api/workspace/list      → {entries, signature, path, workspace, workspace_recovered}
GET /api/workspace/read      → {path, content, size, lines} (limite 400 000 octets)
GET /api/workspace/metadata  → {name, path, type, is_dir, size, mtime_ns, workspace}
GET /api/workspace/download  → octets bruts + Content-Disposition: attachment
```

## Racine isolée

Upstream utilise `~/workspace` ; le port Rust isole vers
`state_dir/workspace` (ou env `HERMES_WEBUI_WORKSPACE_ROOT`). Aucun accès
hors racine possible.

## Sécurité (priorité absolue, testée)

| Attaque | Comportement | Test |
|---|---|---|
| traversal `../` | 404 | ✓ |
| traversal encodé `%2e%2e`, `..%2f` | 404 | ✓ |
| absolute paths | rejeté | ✓ |
| symlink escape (symlink sortant) | 404 | ✓ |
| Unicode/path normalization | résolu + sandbox | ✓ |
| fichiers hors workspace | 404 | ✓ |
| limites de taille (read) | 400 000 octets max | ✓ |
| fichiers non réguliers (socket/fifo) | refusé | ✓ |

Le comportement suit upstream (`safe_resolve_ws`, `stat.S_ISREG`/`S_ISDIR`) —
aucune amélioration silencieuse de la sandbox au prix d'une incompatibilité.

## Fichiers

```
rust-server/src/routes/workspace.rs  (14.9K) — handlers + sandbox
rust-server/tests/test_workspace.rs  (14 tests) — couverture sécurité
rust-server/src/config.rs            — workspace_root()
```

## Dettes R3

- Mutations (create/update/rename/delete) non portées — read-only R2 suffisant.
- Preview office (.docx/.xlsx/.pptx) non portée.
