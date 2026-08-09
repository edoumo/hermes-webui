# Compatibilité Python/Rust — Hermes WebUI

Ce dossier contient le harnais de compatibilité qui compare le backend
Python upstream (`nesquena/hermes-webui`) au port Rust (`rust-server/`)
sur les mêmes routes et fixtures.

## Principe

```
Backend Python upstream (baseline 192df903)
          vs
Backend Rust (rust-server/)
```

Pour chaque route : status code, content-type, corps JSON, headers
significatifs, ETag. Seules les différences explicitement documentées sont
tolérées (voir `TOLERATED_KEYS` / `TOLERATED_HEADERS` dans `compare.py`) :
timestamps, compteurs d'uptime, headers purement serveur, schéma de
transport (le serveur Python active TLS automatiquement si `tls.crt`/
`tls.key` existent dans le repo ; le port Rust est en HTTP simple en R0/R1).

## Lancer le harnais

1. Démarrer le backend Python upstream en isolation totale :

```bash
cd <repo>
python3 -m venv .venv-compat
.venv-compat/bin/pip install pyyaml cryptography
HERMES_HOME=/tmp/hwui-compat-home \
HERMES_WEBUI_STATE_DIR=/tmp/hwui-compat-state \
HERMES_WEBUI_PORT=8793 HERMES_WEBUI_HOST=127.0.0.1 \
HERMES_WEBUI_SKIP_ONBOARDING=1 \
HERMES_WEBUI_AGENT_DIR=<agent-dir> \
HERMES_WEBUI_PYTHON=$PWD/.venv-compat/bin/python \
.venv-compat/bin/python server.py
```

2. Démarrer le port Rust :

```bash
cd rust-server
cargo build
./target/debug/hermes-webui-rust --host 127.0.0.1 --port 8792 \
  --repo-dir .. --state-dir /tmp/hermes-webui-rust-state
```

3. Comparer :

```bash
python3 tests/compat/compare.py \
  --python-url https://127.0.0.1:8793 \
  --rust-url http://127.0.0.1:8792
```

## Règles

- `upstream behavior = référence` : ne jamais inventer une nouvelle API.
- Tout delta fonctionnel doit être visible — ne pas masquer les différences
  pour faire passer les tests.
- Les tests utilisent des HOME/state/workspace isolés (`/tmp/...`), jamais
  les données réelles de `~/.hermes`.
- Aucun secret dans les fixtures ni les rapports.

## Fixtures

- `fixtures/` : à compléter au fil des tracks (sessions JSON, workspace,
  settings) — chaque fixture est un échantillon réel extrait du repo upstream.
