# Revue du projet Rust de référence — shaoliang123456/hermes-desktop-rust

Analyse de `shaoliang123456/hermes-desktop-rust` comme **référence de patterns**
pour le port Rust de Hermes WebUI. Ce dépôt N'EST PAS notre base fonctionnelle.

## Identité

```
REPO = https://github.com/shaoliang123456/hermes-desktop-rust
LICENSE = MIT (vérifiée)
STACK = Electron + TypeScript (frontend) + module natif Rust (napi-rs cdylib)
NATIVE_CRATE = hermes-native (native/Cargo.toml)
```

## Cargo.toml (native/Cargo.toml) — dépendances

```
napi = { version = "2", features = ["async", "serde-json", "tokio_rt"] }
napi-derive = "2"
serde / serde_json = "1"
rusqlite = { version = "0.31", features = ["bundled"] }
tokio = { version = "1", features = ["full"] }
reqwest = { version = "0.12", features = ["json", "stream", "blocking"] }
serde_yaml = "0.9"
dirs = "5"
uuid = { version = "1", features = ["v4"] }
chrono = { version = "0.4", features = ["serde"] }
futures-util = "0.3"
lazy_static = "1"
crate-type = ["cdylib"]   # module natif chargé par Electron via napi
```

## Modules natifs (native/src/)

```
common.rs   832 B    helpers
config.rs   717 B    config
hermes.rs   829 B    wrapper agent
kanban.rs   4.4 Ko   kanban
lib.rs      113 B    racine napi
models.rs   557 B    modèles
profiles.rs 899 B    profils
sessions.rs 9.2 Ko   sessions (rusqlite)
sse.rs      3.8 Ko   SSE
ssh_tunnel.rs 7.1 Ko SSH tunnel
```

## Patterns analysés

| Pattern | Verdict | Justification |
|---|---|---|
| napi-rs cdylib (module natif dans Electron) | NOT_APPLICABLE | Notre cible est un serveur HTTP autonome (axum), pas un module natif embarqué dans Electron. |
| rusqlite bundled pour sessions | REUSE_IDEA | SQLite est pertinent pour les sessions/state.db ; `bundled` évite la dépendance système. À choisir rusqlite vs sqlx selon le modèle réel (le WebUI upstream utilise sqlite3 + fichiers JSON de sessions). |
| tokio full + reqwest (json/stream/blocking) | REUSE_IDEA | Même stack async que notre port (tokio) ; reqwest utile pour le bridge Hermes (HTTP/SSE). |
| serde_yaml pour config | REUSE_IDEA | Le WebUI lit config.yaml (Hermes Agent) — serde_yaml sera utile pour le bridge. |
| SSE (sse.rs) | REIMPLEMENT | Le SSE upstream du WebUI est riche (events, cancellation, run lifecycle) ; il faut le porter fidèlement, pas copier le SSE simplifié de ce projet. |
| Sessions via rusqlite | REIMPLEMENT | Le WebUI upstream stocke les sessions en JSON (STATE_DIR/sessions/*.json) + state.db SQLite ; il faut répliquer CE modèle, pas celui du projet de référence. |
| SSH tunnel (ssh_tunnel.rs) | NOT_APPLICABLE | Le WebUI upstream n'a pas de tunnel SSH intégré (accès via tunnel externe). |
| Kanban (kanban.rs) | NOT_APPLICABLE | Le WebUI upstream a un kanban_bridge.py mais c'est un pont vers l'agent, pas un kanban natif. |
| Performances mesurées (README) | REUSE_IDEA | Le README documente des gains (SSE plus fluide, ~20% mémoire en moins, démarrage plus rapide) — utile comme hypothèse de benchmark, à re-mesurer sur notre port. |
| lazy_static | AVOID | Préférer `OnceLock`/`Arc` moderne (Rust 1.70+) ; lazy_static est déprécié. |
| Portion Rust vs TS | NOT_APPLICABLE | Ce projet est surtout TS/Electron avec un petit noyau Rust natif ; notre port est l'inverse (backend Rust complet, frontend upstream conservé). |

## Leçons pour notre port

1. **rusqlite bundled** est un bon choix pour le state.db Hermes (pas de dépendance système).
2. **tokio + reqwest** est la bonne base pour le futur bridge Hermes Agent (HTTP/SSE local).
3. **serde_yaml** sera nécessaire pour lire config.yaml du bridge.
4. Le SSE upstream est un contrat riche à porter fidèlement (pas le SSE simplifié de ce projet).
5. Les gains de performance annoncés (SSE, mémoire) sont des hypothèses à re-mesurer sur notre port — pas des promesses.
6. Licence MIT compatible avec notre port (même licence que le WebUI upstream).

## Verdict global

Ce projet est une **référence de patterns de bas niveau** (rusqlite, tokio, reqwest,
serde_yaml) mais **pas une référence d'architecture** : il est Electron+napi centré,
alors que notre port est un serveur HTTP autonome. On réutilise les idées de stack,
on réimplémente les contrats upstream.
