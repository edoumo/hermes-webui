# R2 — Benchmark Release (Python upstream vs Rust release)

Benchmark comparatif final R2, conditions identiques, même hôte.

## Conditions

```
HOTE = srv-hermes (Linux, même machine)
PYTHON = upstream nesquena/hermes-webui @ 192df903, venv isolé /tmp, HTTP, auth off
RUST = rust-server/ build RELEASE (cargo build --release, 26.4s, 154 crates)
PY_URL = http://127.0.0.1:8793
RUST_URL = http://127.0.0.1:8797 (release) / 8792 (debug)
DATE = 2026-08-09
MÉTHODE = curl -w %{time_total}, ps -o rss, 100 itérations /health, cold+warm boot.js
```

## Résultats

| Métrique | Python | Rust debug | Rust release | Note |
|---|---|---|---|---|
| RSS idle | 77.0 Mo | 9.5 Mo | **6.1 Mo** | Rust release ~12.6x plus léger |
| Démarrage serveur | ~8 s | <1 s | **<1 s** | Rust quasi instantané |
| /health (100 req, avg) | ~0.92 ms | ~2.1 ms | **~0.27 ms** | Rust release ~3.4x plus rapide |
| static boot.js cold | ~5.4 ms | ~23 ms | **~5.9 ms** | cache Rust (1er hit) ≈ Python |
| static boot.js warm | ~0.69 ms | ~23 ms | **~0.28 ms** | cache Rust warm ~2.5x plus rapide |
| Concurrence 50 connexions | — | — | **PASS** | 50 requêtes parallèles OK |
| Concurrence 100 connexions | — | — | **PASS** | 100 requêtes parallèles OK |

## Interprétation honnête

- **RSS** : le gap est structurel (Python charge l'agent + venv ; Rust est un
  binaire natif). Le gap se réduira quand le bridge Hermes (R3) ajoutera de la
  mémoire côté Rust, mais reste net.
- **/health** : Rust release est ~3.4x plus rapide que Python (0.27 vs 0.92 ms).
- **static** : le cache mémoire Rust (port de `_STATIC_CACHE`) élimine la dette
  R1 — cold ≈ Python, warm ~2.5x plus rapide. La lecture disque n'a plus lieu
  sur hit (vérifié : warm 0.28 ms vs cold 5.9 ms).
- **Concurrence** : 50 et 100 connexions parallèles passent sans erreur.
- **Démarrage** : Rust <1 s vs ~8 s Python (imports agent lourds).

## Améliorations R3 possibles

1. Cache des agents par session (SESSION_AGENT_CACHE upstream) côté bridge.
2. Benchmark SSE synthétique via le bridge (mock) — non mesuré en release ici
   car le flux mock est dominé par le délai Python du bridge, pas par Rust.
3. p95 sur les routes workspace/sessions.

## Commandes de reproduction

```bash
# RSS
ps -o rss= -p <pid>
# /health (100 req)
for i in $(seq 1 100); do curl -s -o /dev/null http://127.0.0.1:<port>/health; done
# static cold/warm
curl -s -o /dev/null -w "%{time_total}\n" http://127.0.0.1:<port>/static/boot.js
curl -s -o /dev/null -w "%{time_total}\n" http://127.0.0.1:<port>/static/boot.js
# concurrence
seq 1 100 | xargs -P 100 -I{} curl -s -o /dev/null http://127.0.0.1:<port>/health
```
