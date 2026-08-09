# Frontière Hermes Agent — Track G

Analyse de ce qui empêche un backend non-Python de parler à Hermes Agent, et
recommandation de bridge pour le port Rust.

## Graphe réel (vérifié dans le code upstream)

```
Browser
  -> WebUI Python (server.py + api/routes.py)
       -> imports directs hermes-agent :
            from run_agent import AIAgent            (routes.py, streaming.py)
            from hermes_cli.plugins import ...       (routes.py)
            from hermes_cli.models import ...        (models.py)
            from api.profiles import ...             (config.yaml)
       -> subprocess : hermes gateway, hermes update, hermes doctor
       -> state.db (SQLite) : sessions CLI/gateway
       -> ~/.hermes : config.yaml, .env, auth.json, memories/, skills/, sessions/
```

## Dépendances identifiées (ce qui bloque un backend non-Python)

1. **`from run_agent import AIAgent`** — le cœur agentique (chat, streaming, tools,
   approvals, memory, skills). Imports directs dans routes.py et streaming.py.
2. **`hermes_cli.*`** — models, plugins, providers, codex_models. Catalogue modèles,
   plugins, auth status.
3. **`api.profiles`** — lit config.yaml par profil, env par requête (thread-local).
4. **`state.db`** — SQLite : sessions CLI/gateway, usage, insights.
5. **`~/.hermes`** — config.yaml, .env, auth.json, memories/, skills/, sessions/.
6. **Subprocess** — `hermes gateway`, `hermes update`, `hermes doctor`, `hermes mcp`.
7. **`sys.path` / venv** — le WebUI importe l'agent depuis son venv (PYTHONPATH).

## Options de bridge (comparaison)

| Option | Fidélité upstream | Effort | Perf | Streaming | Cancellation | Tools | Approvals | Sécurité | Debug | Compat future |
|---|---|---|---|---|---|---|---|---|---|---|
| 1. subprocess JSONL stdin/stdout | moyenne | faible | moyenne | limité | limité | limité | limité | bonne (isolation) | moyenne | moyenne |
| 2. Unix socket | bonne | moyen | bonne | bonne | bonne | bonne | bonne | bonne (local) | bonne | bonne |
| 3. HTTP/SSE local bridge | excellente | moyen | bonne | excellente | excellente | bonne | bonne | bonne (auth local) | excellente | excellente |
| 4. Hermes Gateway/API existante | bonne | faible | bonne | bonne | bonne | bonne | bonne | bonne | bonne | bonne |

## Recommandation

**OPTION 3 — HTTP/SSE local bridge** est la plus fidèle au contrat upstream
(le WebUI upstream parle déjà en HTTP/SSE au frontend ; un bridge HTTP/SSE local
vers l'agent réutilise exactement les mêmes primitives : streaming, cancellation,
approvals, tools). Elle est recommandée pour R2.

**OPTION 4 — Hermes Gateway/API existante** est un bon point de départ rapide
(le WebUI upstream a déjà `gateway_chat.py` qui parle au gateway) mais dépend de
la disponibilité du gateway et de son contrat.

**OPTION 1 (subprocess JSONL)** est la plus simple mais la moins fidèle pour le
streaming/cancellation — à éviter pour le cœur agentique.

## Blocker R1

Aucun blocker : le port R1 ne touche pas au cœur agentique (health, static, index,
settings sont sans couplage Hermes). Le bridge est requis pour R2 (~70 % des routes).

## Découverte utile (R1)

- TLS et auth du WebUI sont pilotés par env : `HERMES_WEBUI_TLS_CERT`/`HERMES_WEBUI_TLS_KEY`
  (config.py:72) et `HERMES_WEBUI_PASSWORD` (auth). Un port Rust peut répliquer ce
  contrat sans bridge.
- `agent_version` (settings) nécessite de sonder l'agent (git describe du agent_dir
  ou gateway health) — delta toléré en R1, résolu par le bridge en R2.
