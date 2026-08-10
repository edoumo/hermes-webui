#!/usr/bin/env python3
"""AgentCache — cache LRU d'agents par session, sémantique SESSION_AGENT_CACHE
upstream (api/streaming.py:9415-9600, api/config.py:9125-9134).

Contrat d'isolation STRICT :
  - la clé de cache est le session_id ;
  - chaque entrée porte la signature complète de l'identité de l'agent
    (model, provider, base_url, api_key sig, api_mode, command, args,
    credential_pool, max_iterations, max_tokens, fallback, toolsets,
    reasoning_config, profile_home, ...) ;
  - un hit n'est valide que si (session_id, signature) correspondent ;
  - tout changement de signature (profile, model, provider, ...) invalide
    l'entrée : l'ancien agent est évincé et un nouvel agent est construit ;
  - un agent de la session A n'est JAMAIS retourné pour la session B.

Le cache ne construit pas lui-même les agents : il reçoit une fabrique
``factory(session_id, identity) -> agent`` et ne stocke que des objets
opaques. Le bridge reste mince.
"""

import hashlib
import json
import threading
import time
from collections import OrderedDict


def build_agent_signature(identity: dict) -> str:
    """Signature sha256 (16 hex) de l'identité complète de l'agent.

    Miroir de la liste upstream (api/streaming.py:9426-9449) : model,
    provider, base_url, api_key sig, api_mode, command, args,
    credential_pool, max_iterations, max_tokens, fallback, toolsets,
    reasoning_config, profile_home. ``sort_keys=True`` pour un blob
    déterministe.
    """
    blob = json.dumps(
        [
            str(identity.get("model") or ""),
            _api_key_sig(identity.get("api_key"), identity.get("credential_pool")),
            str(identity.get("base_url") or ""),
            str(identity.get("provider") or ""),
            str(identity.get("api_mode") or ""),
            str(identity.get("command") or ""),
            list(identity.get("args") or []),
            bool(identity.get("credential_pool")),
            str(identity.get("max_iterations") or ""),
            str(identity.get("max_tokens") or ""),
            identity.get("fallback") or {},
            sorted(identity.get("toolsets") or []),
            identity.get("reasoning_config") or {},
            str(identity.get("profile_home") or ""),
        ],
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _api_key_sig(api_key, credential_pool) -> str:
    """Signature de la clé API sans jamais exposer la clé elle-même.

    Miroir de ``_agent_cache_api_key_sig`` upstream : un credential_pool
    actif rend la clé volatile (round-robin/OAuth), on signe donc le pool et
    non le token ; sinon sha256 tronqué de la clé.
    """
    if credential_pool is not None:
        return "credential-pool"
    return hashlib.sha256(str(api_key or "").encode("utf-8")).hexdigest()[:16]


class AgentCache:
    """LRU thread-safe, clé = session_id, valeur = (agent, signature).

    Sémantique upstream :
      - hit : signature identique + agent présent -> réutilisation + move_to_end ;
      - mismatch d'identité : l'entrée est évincée (jamais réutilisée) ;
      - eviction LRU au-delà de ``max_entries`` (les entrées actives — en cours
        de stream — ne sont pas évincées, comme upstream avec ACTIVE_RUNS) ;
      - ``touch``/``release`` marquent une session active pour protéger son
        agent de l'éviction pendant un stream.
    """

    def __init__(self, max_entries=25, factory=None, now=None):
        self.max_entries = max(1, int(max_entries))
        self._factory = factory
        self._now = now or time.monotonic
        self._entries = OrderedDict()  # session_id -> (agent, signature)
        self._active = set()           # session_ids avec un stream en cours
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._invalidations = 0

    # -- stats (diagnostic, non contractuel) ---------------------------------
    def stats(self) -> dict:
        with self._lock:
            return {
                "size": len(self._entries),
                "max_entries": self.max_entries,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "invalidations": self._invalidations,
                "active_sessions": sorted(self._active),
            }

    # -- lifecycle -----------------------------------------------------------
    def touch(self, session_id: str) -> None:
        """Marque une session comme active (stream en cours)."""
        with self._lock:
            self._active.add(session_id)

    def release(self, session_id: str) -> None:
        """Démarque une session active (stream terminé/annulé)."""
        with self._lock:
            self._active.discard(session_id)

    # -- accès principal ------------------------------------------------------
    def get_or_create(self, session_id: str, identity: dict):
        """Retourne l'agent de ``session_id`` pour ``identity``.

        - hit valide : agent en cache, signature identique ;
        - mismatch : éviction de l'entrée (invalidation) puis construction ;
        - miss : construction via la fabrique.
        L'agent retourné est TOUJOURS lié à ``session_id`` (isolation stricte).
        """
        signature = build_agent_signature(identity)
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is not None:
                cached_agent, cached_sig = entry
                if cached_sig == signature:
                    self._entries.move_to_end(session_id)
                    self._hits += 1
                    return cached_agent, False
                # Identité changée (profile/model/...) : invalidation stricte.
                self._entries.pop(session_id, None)
                self._invalidations += 1
            self._misses += 1
            agent = self._build(session_id, identity)
            self._entries[session_id] = (agent, signature)
            self._entries.move_to_end(session_id)
            self._evict_locked()
            return agent, True

    def _build(self, session_id: str, identity: dict):
        if self._factory is None:
            raise RuntimeError("AgentCache: no factory configured")
        return self._factory(session_id, identity)

    def _evict_locked(self):
        """Éviction LRU : les sessions actives ne sont jamais évincées."""
        while len(self._entries) > self.max_entries:
            evictable = None
            for sid in self._entries:
                if sid not in self._active:
                    evictable = sid
                    break
            if evictable is None:
                break  # tout est actif : on reste temporairement au-dessus du cap
            self._entries.pop(evictable)
            self._evictions += 1

    # -- gestion explicite ----------------------------------------------------
    def get(self, session_id: str):
        """Retourne (agent, signature) ou None — sans construire."""
        with self._lock:
            entry = self._entries.get(session_id)
            if entry is None:
                return None
            self._entries.move_to_end(session_id)
            return entry

    def invalidate(self, session_id: str):
        """Évince l'entrée de ``session_id`` (invalidation explicite)."""
        with self._lock:
            if self._entries.pop(session_id, None) is not None:
                self._invalidations += 1

    def evict(self, session_id: str):
        """Alias de ``invalidate`` — nomenclature endpoint DELETE."""
        self.invalidate(session_id)

    def clear(self):
        with self._lock:
            self._entries.clear()
            self._active.clear()

    def snapshot(self) -> list[dict]:
        """Vue diagnostic : session_id, signature, actif — jamais l'agent."""
        with self._lock:
            return [
                {
                    "session_id": sid,
                    "signature": sig,
                    "active": sid in self._active,
                }
                for sid, (_agent, sig) in self._entries.items()
            ]

    def __len__(self):
        with self._lock:
            return len(self._entries)
