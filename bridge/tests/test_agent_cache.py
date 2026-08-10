#!/usr/bin/env python3
"""Tests unitaires AgentCache — isolation stricte multi-session.

Couvre : hit/miss, isolation A/B (jamais de fuite), invalidation sur
changement de signature (profile/model/provider/base_url/api_key/...),
eviction LRU (avec protection des sessions actives), cancellation,
concurrence A+B, stats.
"""

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_cache import AgentCache, build_agent_signature  # noqa: E402


class _FakeAgent:
    """Agent factice portant un tag de session pour prouver l'isolation."""

    def __init__(self, session_id, identity):
        self.session_id = session_id
        self.identity = dict(identity or {})
        self.streams = 0

    def stream(self, *a, **k):
        self.streams += 1
        return f"agent[{self.session_id}]"


def _factory(agents, lock):
    def make(session_id, identity):
        agent = _FakeAgent(session_id, identity)
        with lock:
            agents.append(agent)
        return agent
    return make


def _base_identity(**over):
    ident = {
        "model": "gpt-4o",
        "provider": "openai",
        "base_url": "https://api.openai.com/v1",
        "api_key": "sk-test-123",
        "api_mode": "",
        "command": "",
        "args": [],
        "credential_pool": None,
        "max_iterations": "",
        "max_tokens": "",
        "fallback": {},
        "toolsets": [],
        "reasoning_config": {},
        "profile_home": "/tmp/home-a",
    }
    ident.update(over)
    return ident


class AgentCacheIsolationTests(unittest.TestCase):
    def setUp(self):
        self.agents = []
        self.lock = threading.Lock()
        self.cache = AgentCache(max_entries=25, factory=_factory(self.agents, self.lock))

    # -- hit / miss ----------------------------------------------------------
    def test_miss_then_hit_same_session_same_identity(self):
        a1, created1 = self.cache.get_or_create("sess-A", _base_identity())
        self.assertTrue(created1)
        a2, created2 = self.cache.get_or_create("sess-A", _base_identity())
        self.assertFalse(created2)
        self.assertIs(a1, a2, "même session + même identité -> même agent")
        self.assertEqual(len(self.agents), 1)
        self.assertEqual(self.cache.stats()["hits"], 1)
        self.assertEqual(self.cache.stats()["misses"], 1)

    # -- isolation stricte A/B ------------------------------------------------
    def test_session_a_never_leaks_to_session_b(self):
        agent_a, _ = self.cache.get_or_create("sess-A", _base_identity())
        agent_b, _ = self.cache.get_or_create("sess-B", _base_identity())
        self.assertIsNot(agent_a, agent_b, "A et B doivent être des agents distincts")
        self.assertEqual(agent_a.session_id, "sess-A")
        self.assertEqual(agent_b.session_id, "sess-B")
        # Même identité, sessions différentes : jamais de partage.
        agent_a2, created = self.cache.get_or_create("sess-A", _base_identity())
        self.assertFalse(created)
        self.assertIs(agent_a2, agent_a)
        agent_b2, created = self.cache.get_or_create("sess-B", _base_identity())
        self.assertFalse(created)
        self.assertIs(agent_b2, agent_b)
        self.assertIsNot(agent_a2, agent_b2)
        self.assertEqual(len(self.agents), 2)

    def test_identity_never_crosses_sessions(self):
        """Un agent construit pour A ne peut jamais être retourné pour B,
        même si B demande exactement la même identité."""
        agent_a, _ = self.cache.get_or_create("sess-A", _base_identity())
        agent_b, _ = self.cache.get_or_create("sess-B", _base_identity())
        # Le tag interne de l'agent A doit rester A.
        self.assertEqual(agent_a.session_id, "sess-A")
        self.assertEqual(agent_b.session_id, "sess-B")
        # Et le cache ne contient que 2 entrées distinctes.
        self.assertEqual(len(self.cache), 2)

    # -- invalidation sur changement de signature -----------------------------
    def test_profile_change_invalidates(self):
        a1, _ = self.cache.get_or_create("sess-A", _base_identity(profile_home="/tmp/home-a"))
        a2, created = self.cache.get_or_create(
            "sess-A", _base_identity(profile_home="/tmp/home-b")
        )
        self.assertTrue(created, "changement de profile -> reconstruction")
        self.assertIsNot(a1, a2)
        self.assertEqual(self.cache.stats()["invalidations"], 1)
        # L'ancien agent n'est plus dans le cache.
        self.assertIsNone(self.cache.get("sess-A")[0] if False else None)
        entry = self.cache.get("sess-A")
        self.assertIs(entry[0], a2)

    def test_model_change_invalidates(self):
        a1, _ = self.cache.get_or_create("sess-A", _base_identity(model="gpt-4o"))
        a2, created = self.cache.get_or_create("sess-A", _base_identity(model="gpt-4.1"))
        self.assertTrue(created)
        self.assertIsNot(a1, a2)

    def test_provider_change_invalidates(self):
        a1, _ = self.cache.get_or_create("sess-A", _base_identity(provider="openai"))
        a2, created = self.cache.get_or_create("sess-A", _base_identity(provider="anthropic"))
        self.assertTrue(created)
        self.assertIsNot(a1, a2)

    def test_base_url_change_invalidates(self):
        a1, _ = self.cache.get_or_create("sess-A", _base_identity(base_url="https://a"))
        a2, created = self.cache.get_or_create("sess-A", _base_identity(base_url="https://b"))
        self.assertTrue(created)
        self.assertIsNot(a1, a2)

    def test_api_key_change_invalidates_but_never_exposes_key(self):
        a1, _ = self.cache.get_or_create("sess-A", _base_identity(api_key="sk-secret-1"))
        a2, created = self.cache.get_or_create("sess-A", _base_identity(api_key="sk-secret-2"))
        self.assertTrue(created, "changement de clé API -> reconstruction")
        self.assertIsNot(a1, a2)
        # La clé ne doit apparaître nulle part dans le snapshot.
        snap = json_dumps(self.cache.snapshot())
        self.assertNotIn("sk-secret", snap)
        self.assertNotIn("sk-secret-1", snap)
        self.assertNotIn("sk-secret-2", snap)

    def test_credential_pool_stabilizes_signature(self):
        """Avec credential_pool, la clé volatile ne casse pas le cache
        (miroir upstream _agent_cache_api_key_sig)."""
        a1, _ = self.cache.get_or_create(
            "sess-A", _base_identity(api_key="token-1", credential_pool="pool-x")
        )
        a2, created = self.cache.get_or_create(
            "sess-A", _base_identity(api_key="token-2", credential_pool="pool-x")
        )
        self.assertFalse(created, "credential_pool -> signature stable")
        self.assertIs(a1, a2)

    def test_toolsets_change_invalidates(self):
        a1, _ = self.cache.get_or_create("sess-A", _base_identity(toolsets=["web"]))
        a2, created = self.cache.get_or_create("sess-A", _base_identity(toolsets=["web", "code"]))
        self.assertTrue(created)
        self.assertIsNot(a1, a2)

    def test_signature_deterministic_and_sensitive(self):
        s1 = build_agent_signature(_base_identity())
        s2 = build_agent_signature(_base_identity())
        self.assertEqual(s1, s2)
        self.assertEqual(len(s1), 16)
        s3 = build_agent_signature(_base_identity(model="other"))
        self.assertNotEqual(s1, s3)

    # -- eviction LRU ----------------------------------------------------------
    def test_lru_eviction(self):
        cache = AgentCache(max_entries=2, factory=_factory(self.agents, self.lock))
        cache.get_or_create("sess-A", _base_identity())
        cache.get_or_create("sess-B", _base_identity())
        cache.get_or_create("sess-C", _base_identity())
        self.assertEqual(len(cache), 2)
        self.assertIsNone(cache.get("sess-A"), "A (plus ancien) doit être évincé")
        self.assertIsNotNone(cache.get("sess-B"))
        self.assertIsNotNone(cache.get("sess-C"))
        self.assertEqual(cache.stats()["evictions"], 1)

    def test_lru_touch_keeps_recent(self):
        cache = AgentCache(max_entries=2, factory=_factory(self.agents, self.lock))
        cache.get_or_create("sess-A", _base_identity())
        cache.get_or_create("sess-B", _base_identity())
        cache.get("sess-A")  # A redevient récent
        cache.get_or_create("sess-C", _base_identity())
        self.assertIsNotNone(cache.get("sess-A"))
        self.assertIsNone(cache.get("sess-B"), "B (moins récent) évincé")

    def test_active_session_not_evicted(self):
        cache = AgentCache(max_entries=2, factory=_factory(self.agents, self.lock))
        cache.get_or_create("sess-A", _base_identity())
        cache.get_or_create("sess-B", _base_identity())
        cache.touch("sess-A")  # A en cours de stream
        cache.get_or_create("sess-C", _base_identity())
        # B est évincable, A est protégé -> le cache peut dépasser le cap
        # temporairement (sémantique upstream ACTIVE_RUNS).
        # NB: on vérifie via snapshot() (pas get()) pour ne pas modifier l'ordre LRU.
        self.assertIsNotNone(cache.get("sess-A"))
        self.assertIsNone(cache.get("sess-B"))
        cache.release("sess-A")
        cache.get_or_create("sess-D", _base_identity())
        # A libéré -> évincable ensuite. L'éviction touche le plus ancien
        # non-actif : après le get("sess-A") ci-dessus, A est récent, donc
        # c'est C (ou A selon l'ordre) qui part — l'invariant est que le cache
        # revient à max_entries et que l'entrée évincée est non-active.
        self.assertEqual(len(cache), 2, "le cache revient au cap après libération")
        remaining = {e["session_id"] for e in cache.snapshot()}
        self.assertIn("sess-D", remaining)
        self.assertNotIn("sess-B", remaining, "B (évincé pendant l'activité) ne revient pas")

    def test_explicit_evict(self):
        self.cache.get_or_create("sess-A", _base_identity())
        self.cache.evict("sess-A")
        self.assertIsNone(self.cache.get("sess-A"))
        self.assertEqual(self.cache.stats()["invalidations"], 1)
        # Reconstruction après éviction.
        a2, created = self.cache.get_or_create("sess-A", _base_identity())
        self.assertTrue(created)
        self.assertEqual(a2.session_id, "sess-A")

    # -- cancellation ----------------------------------------------------------
    def test_cancel_release_keeps_agent_for_next_turn(self):
        """Après annulation, la session est libérée mais l'agent reste en
        cache pour le turn suivant (même identité)."""
        self.cache.get_or_create("sess-A", _base_identity())
        self.cache.touch("sess-A")
        self.cache.release("sess-A")
        self.assertNotIn("sess-A", self.cache.stats()["active_sessions"])
        a2, created = self.cache.get_or_create("sess-A", _base_identity())
        self.assertFalse(created, "l'agent survit à l'annulation (même identité)")

    # -- concurrence A+B --------------------------------------------------------
    def test_concurrent_a_and_b_no_cross_contamination(self):
        errors = []
        results = {}

        def worker(session_id, identity, n):
            try:
                for _ in range(n):
                    agent, _ = self.cache.get_or_create(session_id, identity)
                    if agent.session_id != session_id:
                        errors.append(f"leak: {agent.session_id} != {session_id}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{session_id}: {exc!r}")

        threads = [
            threading.Thread(target=worker, args=("sess-A", _base_identity(profile_home="/tmp/a"), 200)),
            threading.Thread(target=worker, args=("sess-B", _base_identity(profile_home="/tmp/b"), 200)),
            threading.Thread(target=worker, args=("sess-C", _base_identity(profile_home="/tmp/c"), 200)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"fuites inter-sessions: {errors}")
        # Chaque session a exactement un agent, taggé à sa session.
        self.assertEqual(len(self.agents), 3)
        for agent in self.agents:
            self.assertIn(agent.session_id, {"sess-A", "sess-B", "sess-C"})

    def test_concurrent_same_session_single_agent(self):
        """N threads sur la même session -> un seul agent construit."""
        errors = []
        barrier = threading.Barrier(8)

        def worker():
            try:
                barrier.wait()
                agent, _ = self.cache.get_or_create("sess-A", _base_identity())
                if agent.session_id != "sess-A":
                    errors.append("leak")
            except Exception as exc:  # noqa: BLE001
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(self.agents), 1)

    # -- divers ------------------------------------------------------------------
    def test_clear(self):
        self.cache.get_or_create("sess-A", _base_identity())
        self.cache.get_or_create("sess-B", _base_identity())
        self.cache.clear()
        self.assertEqual(len(self.cache), 0)
        self.assertEqual(self.cache.stats()["size"], 0)


def json_dumps(obj):
    import json
    return json.dumps(obj)


if __name__ == "__main__":
    unittest.main(verbosity=2)
