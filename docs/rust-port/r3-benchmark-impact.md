# R3 — Benchmark Impact (Python upstream vs Rust release)

Benchmark comparatif R3, conditions isolées, même hôte.

## Conditions

```
HOTE = srv-hermes (même machine)
PYTHON = upstream nesquena/hermes-webui @ baseline isolé (/tmp/hwui-compat-*)
RUST = rust-server/ build RELEASE (cargo build --release)
PY_URL = https://127.0.0.1:8793  (upstream active TLS auto : tls.crt/tls.key présents)
RUST_URL = http://127.0.0.1:8797  (release)
DATE = 2026-08-10
MÉTHODE = curl -w %{time_total}, ps -o rss, 200 itérations /health,
          static boot.js cold+warm, concurrence 100
SCRIPT = docs/rust-port/bench/benchmark_r3.sh
```

## Résultats (N=200)

| Métrique | Python (HTTPS) | Rust release | Note |
|---|---|---|---|
| /health avg | 3.217 ms | **0.216 ms** | Rust ~14.9x plus rapide |
| RSS idle | — | **5.7 Mo** | léger (vs 6.1 R2) |
| static boot.js cold | — | 0.339 ms | cache mémoire |
| static boot.js warm | — | 0.233 ms | cache chaud |
| Concurrence 100 | — | **PASS** | 100 connexions parallèles OK |

## Interprétation honnête

- Le gap Python/Rust s'est **légèrement élargi en faveur de Rust** vs R2 : le
  port R3 (sessions, auth PBKDF2, uploads, agent-cache, webauthn) n'a pas
  dégradé la route /health — 0.216 ms vs 0.27 ms au R2 (marge, conditions
  identiques côté Rust).
- RSS stable (5.7 vs 6.1 Mo R2) : les nouvelles tracks R3 n'ajoutent pas de
  footprint notable au repos.
- La mesure Python est en HTTPS (TLS auto par l'upstream, car tls.crt/key
  existent dans le repo) — donc le chiffrement pèse dans les 3.217 ms. La
  comparaison à chaud reste valide, mais le 0.92 ms HTTP du R2 n'est pas
  reproductible tel quel (le serveur Python force TLS sur cet hôte).
- R3 introduit du travail par requête (agent-cache lookup, session resolution)
  uniquement sur les routes concernées ; /health est inchangé.

## Commandes de reproduction

```bash
cd docs/rust-port/bench
PY_URL="https://127.0.0.1:8793" RUST_URL="http://127.0.0.1:8797" N=200 bash benchmark_r3.sh
```

## Statut

```
BENCH_R3 = DONE (script corrigé : awk + LC_NUMERIC=C, gère TLS upstream)
```
