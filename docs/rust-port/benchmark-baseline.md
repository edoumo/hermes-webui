# Benchmark Baseline — Hermes WebUI Python vs Rust (R1)

Baseline légère sur opérations déterministes. **Aucune extrapolation abusive** :
le port Rust est en build **debug** (non optimisé) ; le serveur Python upstream
utilise un cache mémoire pour les assets statiques. Les performances ne bloquent
pas R1 (priorité : compatibilité > fiabilité > sécurité > maintenabilité > perf).

## Conditions de test

```
HOTE = srv-hermes (Linux, même machine)
PYTHON = upstream nesquena/hermes-webui @ 192df903, venv isolé /tmp, HTTP, auth off
RUST = rust-server/ build debug (cargo build, pas --release), state /tmp
PY_URL = http://127.0.0.1:8793
RUST_URL = http://127.0.0.1:8792
DATE = 2026-08-09
```

## Résultats

| Métrique | Python | Rust (debug) | Note |
|---|---|---|---|
| RSS idle | 77.5 Mo | 9.5 Mo | Rust debug ~8x plus léger ; à re-mesurer en release |
| /health latence (100 req) | ~0.9 ms | ~2.1 ms | Rust debug ; release attendu plus rapide |
| /static/boot.js (176 Ko) | ~5.3 ms | ~23 ms | Python a un cache mémoire ; Rust relit le disque à chaque requête (pas de cache en R1) |
| Démarrage serveur | ~8 s (venv + imports agent) | <1 s | Rust démarre quasi instantanément |

## Interprétation honnête

- **RSS** : le gap est réel et structurel (Python charge l'agent + venv ; Rust est
  un binaire natif). Mais le WebUI Python charge aussi l'agent en mémoire pour le
  chat — le gap se réduira quand le bridge Hermes (R2) ajoutera de la mémoire.
- **/health** : les deux sont < 3 ms — négligeable. Le Rust debug est plus lent
  que Python ici, ce qui est attendu sans `--release`.
- **/static** : le Python gagne grâce à son cache mémoire (raw+gzip+etag en RAM).
  Le Rust R1 relit le disque à chaque requête. **Amélioration R2** : ajouter un
  cache mémoire statique (même stratégie que `_STATIC_CACHE` upstream).
- **Démarrage** : Rust est nettement plus rapide (pas d'imports Python lourds).

## Recommandations

1. Re-mesurer en `cargo build --release` (R2) — le debug sous-estime Rust.
2. Ajouter un cache mémoire statique côté Rust (parité avec `_STATIC_CACHE`).
3. Ne pas benchmarker le LLM pour comparer les serveurs (le temps modèle masque tout).
4. Ces chiffres sont une baseline, pas une promesse de performance.

## Commandes de reproduction

```bash
# RSS
ps -o rss= -p <pid>
# Latence /health (100 req)
for i in $(seq 1 100); do curl -s -o /dev/null http://127.0.0.1:<port>/health; done
# Latence static
curl -s -o /dev/null -w "%{time_total}\n" http://127.0.0.1:<port>/static/boot.js
```
