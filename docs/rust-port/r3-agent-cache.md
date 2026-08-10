# R3 — Agent Cache (Track A, partie cache)

## Objectif

Implémenter l'équivalent Rust/bridge de `SESSION_AGENT_CACHE` upstream :
un cache d'agents par session avec isolation stricte multi-session.

## Contrat upstream vérifié

- `api/streaming.py` : `SESSION_AGENT_CACHE[session_id] = AIAgent(...)`,
  clé = session_id, invalidation sur changement de (model, provider,
  base_url, profile_home), eviction quand le cache dépasse une taille max.
- Le cache est un détail d'implémentation : le contrat public est que deux
  sessions concurrentes ne partagent jamais d'état d'agent.

## Implémentation (bridge/agent_cache.py)

```
AgentCache(max_entries, factory)
  get_or_create(session_id, identity) -> agent
  release(session_id)                  -> libère (évincable)
  invalidate(session_id)               -> invalidation forcée
  snapshot()                           -> état (pour tests/observabilité)
```

- LRU thread-safe (lock + OrderedDict).
- Signature d'identité : (model, provider, base_url, profile_home, ...).
- `get_or_create` avec signature différente → invalidation + recréation.
- Session active (en cours de streaming) jamais évincée.
- Factory injectable (mock en test, `SessionAgent` en real).

## Isolation testée (bridge/tests/test_agent_cache.py)

- Session A ne fuit jamais vers session B.
- Changement profile/model → invalidation.
- Eviction LRU (max_entries).
- Session active protégée de l'eviction.
- Cancellation.
- Concurrence A+B.

## Résultats

```
TESTS = 19/19 PASS
```

## Intégration serveur

- `bridge/server.py` : `AgentCache` branché sur `/v1/chat` (factory
  `SessionAgent` en mode real, `MockAgent` en mode mock), endpoints
  `GET /v1/agent-cache`, `DELETE /v1/agent-cache/{id}`.
- Fix intégrateur : la factory était absente à l'initialisation (500 sur
  `/v1/chat`) — corrigé, non-régression v1 prouvée.
