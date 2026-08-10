# Handoff — Hermes WebUI Rust R3 (état de reprise)

TIMESTAMP = 2026-08-10T18:30:00Z
BRANCHE = rust-port/r3-functional-parity
HEAD = 43984711 (docs R3 upstream-delta + stability + handoff)
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
| C | WebAuthn/passkeys | PENDING (vague 2) | worker (src/auth/webauthn.rs) |
| D | Uploads/attachments | WORKER_EN_COURS | worker (src/routes/uploads.rs) |
| E | Compat harness v2 | PENDING (vague 2) | worker (tests/compat/) |
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

## Découverte worker B (PBKDF2)

PBKDF2 600k itérations en Rust pur (hmac crate) = ~7.75s par hash — trop lent
pour un login. Le worker doit utiliser une implémentation optimisée
(crate pbkdf2 avec HMAC accéléré, ou ring). Vérifier le nombre d'itérations
au HEAD upstream bd91b649 (ne pas figer 600k sans vérification).

## Red lines

```
PUSH = NON | PR = NON | UPSTREAM = NON | PROD_WEBUI = NON_TOUCHÉ
DONNÉES RÉELLES = NON (lecture state.db en mode=ro uniquement)
BRIDGE = localhost uniquement | SECRETS = AUCUN | PROFESSEUR = NON
```

## Prochaines actions

1. Attendre les 3 workers R3 (A/B/D) — vague 1
2. Lancer vague 2 (C WebAuthn, E Compat v2) si périmètres libres
3. Intégrer les snippets de wiring dans les fichiers partagés (une seule fois)
4. Gate d'intégration complète (fmt/build/clippy/test/release)
5. Soak tests (r3-stability.md)
6. Docs R3 restantes (r3-agent-state, r3-agent-cache, r3-auth-password,
   r3-webauthn, r3-uploads, r3-compat-v2)
7. Commits locaux atomiques (aucun push)
8. Archive sanitizée + SHA256
9. Rapport final §28 + verdict + décision R4 (options A/B/C/D)
