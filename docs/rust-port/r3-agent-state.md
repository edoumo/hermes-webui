# R3 — Agent State (Track A, partie sessions)

## Objectif

Exposer les sessions agent (state.db Hermes) au WebUI Rust via le bridge,
en lecture seule, sans jamais écrire dans un state.db réel.

## Contrat upstream vérifié

- `api/agent_sessions.py` : liste des sessions agent depuis `state.db`
  (table `sessions`), classification CLI vs WebUI vs Agent.
- `api/streaming.py` : `SESSION_AGENT_CACHE` (dict session_id → AIAgent),
  invalidation sur changement de profile/model, eviction LRU.
- Le WebUI upstream lit `state.db` en mode lecture seule pour lister les
  sessions agent ; la fusion WebUI/CLI/Agent est faite côté affichage, jamais
  dans le stockage.

## Implémentation (bridge/)

- `bridge/agent_sessions.py` : accès `state.db` en `mode=ro` uniquement,
  fixtures `/tmp` pour les tests, aucun chemin réel codé en dur.
- Endpoints bridge v1+ :
  - `GET /v1/sessions` — liste des sessions agent (métadonnées, pas de messages)
  - `GET /v1/sessions/{id}` — détail d'une session agent
  - `POST /v1/sessions/{id}/activate` — active une session dans le cache agent
- `bridge/agent_cache.py` : `AgentCache` LRU thread-safe, clé = session_id,
  signature = (model, provider, base_url, profile_home, ...), invalidation
  sur changement de signature, eviction LRU, `release()` pour libérer.

## Isolation testée

- Session A ne fuit jamais vers session B (tests dédiés).
- Changement profile/model → invalidation.
- Eviction LRU avec session active protégée.
- Cancellation.
- Concurrence A+B (threads).

## Résultats

```
TESTS = 19/19 PASS (bridge/tests/test_agent_cache.py)
PY_COMPILE = PASS (agent_cache.py, agent_sessions.py, server.py)
NON-REGRESSION = v1 health + chat SSE + cache alimenté (mock)
```

## Red lines

- `state.db` réel : lecture `mode=ro` uniquement, jamais d'écriture.
- Aucun secret réel dans le code ou les logs.
- Bridge localhost uniquement.
