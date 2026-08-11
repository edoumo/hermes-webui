# R3 — Stabilité / Soak Tests (résultats)

## Objectif

Détecter les régressions évidentes (crash, panic, fd leak, task leak,
croissance mémoire grossière) sur les parcours R3, après intégration des
tracks A/B/C/D/E/F.

## Exécution

```
DATE = 2026-08-11
BINAIRE = target/release/hermes-webui-rust (rebuild avec correctifs E/F)
SCRIPT = docs/rust-port/bench/soak_r3.sh
SERVEURS = rust release 8792 + bridge mock 8794 (état isolé /tmp)
```

## Résultats (7/7 PASS)

| # | Scénario | Volume | Résultat |
|---|---|---|---|
| 1 | Créations/lectures/renames/suppressions sessions | 100 itérations | PASS |
| 2 | Streams mock séquentiels (bridge direct) | 100 itérations | PASS |
| 3 | Streams concurrents (relay Rust) | 10 × 10 | PASS |
| 4 | Cancel répétée | 50 itérations | PASS |
| 5 | Uploads temporaires | 50 fichiers | PASS |
| 6 | Login/logout répété | 50 itérations | PASS |
| 7 | Agent cache A/B alterné | 30 itérations | PASS |

## Mesures

```
CRASH = 0 (process vivant en fin de soak)
PANIC = 0 (aucune trace panic dans le log serveur)
FD_LEAK = 10 → 10 (delta 0, tolérance ±5) — aucun fd fuyé
TASK_LEAK = aucun task résiduel (process stable, pas de croissance)
MEMOIRE = RSS 6.8 MB → 9.7 MB (+2.9 MB sur ~500 requêtes + 100 uploads,
          tolérance documentée < 50 MB) — pas de fuite grossière
```

## Interprétation

- Le delta RSS de ~3 MB sur l'ensemble du soak est cohérent avec les caches
  runtime (static cache, session store en mémoire) — pas de croissance
  continue (FD stable, process stable).
- Aucun panic sur les parcours R3 : rename multi-octets (fix af515e2d),
  uploads multipart, streams SSE concurrents, cancel, auth.
- Le bridge mock n'a montré aucune dégradation (health OK tout du long,
  streams séquentiels et concurrents servis sans erreur).

## Statut

```
SOAK_TESTS = DONE — 7/7 PASS, mesures stables
```
