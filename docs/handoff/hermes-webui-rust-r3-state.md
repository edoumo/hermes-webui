# Handoff — Hermes WebUI Rust R3 (état de reprise)

TIMESTAMP = 2026-08-10T20:30:00Z
BRANCHE = rust-port/r3-functional-parity
HEAD = 34fabbbc (docs R3 agent-state/agent-cache/auth-password/uploads)
BASELINE_UPSTREAM = 192df903 (exp-v0.52.192)
UPSTREAM_ACTUEL = bd91b649 (exp-v0.52.193) — delta NO_IMPACT (a11y frontend)
PROD_WEBUI = NON_TOUCHE (service actif 192.168.1.187:8787)
PUSH = AUCUN

## État des tracks R3

```
TRACK_A_AGENT_STATE_CACHE = DONE + COMMIT
  bridge/agent_cache.py + agent_sessions.py + server.py v1+ + tests 19/19
  fix factory AgentCache (v1 chat non-régression restauré)
TRACK_B_PASSWORD_PBKDF2 = DONE + COMMIT
  src/auth/password.rs + tests 22/22 release (16 --ignored) + 8/8 debug
  wiring login réel dans auth/mod.rs (commit 12970215)
TRACK_D_UPLOADS = DONE + COMMIT
  src/routes/uploads.rs + tests 16/16 (implémenté par l'intégrateur)
TRACK_C_WEBAUTHN = WORKER_EN_COURS (deleg_7a563f00 task-0)
  périmètre : src/auth/webauthn.rs + tests/test_webauthn.rs
TRACK_E_COMPAT_V2 = WORKER_EN_COURS (deleg_7a563f00 task-1)
  périmètre : tests/compat/ (>= 40 scénarios, stateful, SSE)
TRACK_F_IMPORT_EXPORT = PENDING
TRACK_G_WORKSPACE_MUTATIONS = PREPARE_ONLY
TRACK_H_UPSTREAM_SYNC = DONE (NO_IMPACT)
TRACK_I_INTEGRATION = EN_COURS
```

## Gate actuelle

```
CARGO_FMT = PASS
CARGO_CLIPPY = 0 warnings
CARGO_TEST = 80 passed, 16 ignored (12 suites)
  test_auth 9 | test_auth_password 8+16 | test_health 3 | test_index 3
  test_sessions 16 | test_settings 4 | test_static 6 | test_uploads 16
  test_workspace 14 | test_bridge 1
BRIDGE_PYTEST = 19/19 PASS
```

## Commits locaux R3 (aucun push)

```
1. feat(bridge): expose Hermes session and agent-cache operations (v1+)
2. feat(auth): add upstream-compatible PBKDF2 verification
3. feat(upload): port attachment handling
4. test(auth): mark 600k-heavy tests #[ignore] for debug gate
5. feat(auth): wire real PBKDF2 login (R3 core)
6. docs(r3): agent state, agent cache, auth password, uploads
7. style(upload): cargo fmt canonical formatting
```

## Prochaines actions

```
1. Attendre/qualifier workers vague 2 (WebAuthn, compat v2)
2. Intégrer les livrables (wiring webauthn si fourni, harnais v2)
3. Docs R3 restantes : r3-webauthn, r3-compat-v2, r3-upstream-delta (déjà
   écrite), r3-stability (déjà écrite)
4. Soak tests (r3-stability.md)
5. Build release + benchmark impact
6. Archive sanitizée + SHA256
7. Rapport final §28 + verdict + décision R4
```

## Red lines

```
PUSH = NON | PR = NON | UPSTREAM = NON | PROD_WEBUI = NON_TOUCHÉ
DONNÉES RÉELLES = NON (state.db lecture mode=ro uniquement)
BRIDGE = localhost uniquement | SECRETS = AUCUN | PROFESSEUR = NON
```
