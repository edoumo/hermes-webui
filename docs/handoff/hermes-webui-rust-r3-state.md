# Handoff — Hermes WebUI Rust R3 (état de reprise)

TIMESTAMP = 2026-08-10T17:15:00Z
BRANCHE = rust-port/r3-functional-parity
HEAD = 15f42c52 (docs R3 upstream-delta + stability)
BASELINE_UPSTREAM = 192df903 (exp-v0.52.192)
UPSTREAM_ACTUEL = bd91b649 (exp-v0.52.193) — delta NO_IMPACT (a11y frontend)
R2_CHECKPOINT = rust-port/r2-agent-bridge @ 16145f75 (intact)

## Architecture

```
Browser / client
  -> Rust Axum WebUI (rust-server/)
       -> Bridge Python localhost (bridge/server.py, protocole v1)
            -> Hermes Agent / AIAgent
```

## Tracks R3 (état)

| Track | Nom | Statut | Owner |
|---|---|---|---|
| A | Agent sessions/state.db/cache | WORKER_EN_COURS | worker (bridge/ exclusif) |
| B | Password PBKDF2 | WORKER_EN_COURS | worker (src/auth/password.rs) |
| C | WebAuthn/passkeys | WORKER_EN_COURS | worker (src/auth/webauthn.rs) |
| D | Uploads/attachments | WORKER_EN_COURS | worker (src/routes/uploads.rs) |
| E | Compat harness v2 | WORKER_EN_COURS | worker (tests/compat/) |
| F | Import/export sessions | PENDING | — |
| G | Workspace mutations | PREPARE_ONLY | — |
| H | Upstream sync | DONE (NO_IMPACT) | intégrateur |
| I | Integration/qualification/docs | EN_COURS | intégrateur |

## Doctrine (décidée après R2)

UN SEUL INTÉGRATEUR possède les fichiers partagés :
app.rs, state.rs, config.rs, main.rs, lib.rs, Cargo.toml (dépendances
transversales). Les workers ont des périmètres exclusifs et ne touchent PAS
aux fichiers partagés. Ils fournissent des snippets de wiring à l'intégrateur.

## Gate R2 (non-régression, re-vérifiée)

```
CARGO_FMT = PASS | CARGO_BUILD = PASS | CARGO_CLIPPY = 0 warnings
CARGO_TEST = 55 passed (10 suites)
```

## Red lines

```
PUSH = NON | PR = NON | UPSTREAM = NON | PROD_WEBUI = NON_TOUCHÉ
DONNÉES RÉELLES = NON (lecture state.db en mode=ro uniquement)
BRIDGE = localhost uniquement | SECRETS = AUCUN | PROFESSEUR = NON
```

## Prochaines actions

1. Attendre les 5 workers R3 (A/B/C/D/E)
2. Intégrer les snippets de wiring dans les fichiers partagés (une seule fois)
3. Gate d'intégration complète (fmt/build/clippy/test/release)
4. Soak tests (r3-stability.md)
5. Docs R3 restantes (r3-agent-state, r3-agent-cache, r3-auth-password,
   r3-webauthn, r3-uploads, r3-compat-v2)
6. Commits locaux atomiques (aucun push)
7. Archive sanitizée + SHA256
8. Rapport final §28 + verdict + décision R4 (options A/B/C/D)
