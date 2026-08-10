# Handoff — Hermes WebUI Rust R3 (état final tracks E/F)

TIMESTAMP = 2026-08-11T00:10:00Z
BRANCHE = rust-port/r3-functional-parity
HEAD = fe9ba0c (docs benchmark) — 6 commits locaux R3 ajoutés, AUCUN push
BASELINE_UPSTREAM = 192df903 (exp-v0.52.192)
UPSTREAM_ACTUEL = bd91b649 (exp-v0.52.193) — delta NO_IMPACT (a11y frontend)
PROD_WEBUI = NON_TOUCHE (service actif 192.168.1.187:8787)
PUSH = AUCUN

## État des tracks R3

```
TRACK_A_AGENT_STATE_CACHE = DONE + COMMIT (antérieur)
TRACK_B_PASSWORD_PBKDF2    = DONE + COMMIT (antérieur)
TRACK_C_WEBAUTHN           = DONE + COMMIT (antérieur, e427f87d)
TRACK_D_UPLOADS            = DONE + COMMIT (antérieur)
TRACK_E_COMPAT_V2          = DONE — harnais v2 qualifié 39/40 PASS (214/215)
  + commit 3c1633e. 1 FAIL = delta fonctionnel documenté (rate-limit login
  non porté : python 401×5→429 vs rust 401×6 — vérifié password, pas de 429).
TRACK_F_IMPORT_EXPORT      = DONE — import_export.rs + tests 14/14 + wiring
  + commit e39d9db + f970c29. import_cli = HERMES_BRIDGE_REQUIRED (non inventé).
TRACK_G_WORKSPACE_MUTATIONS = PREPARE_ONLY
TRACK_H_UPSTREAM_SYNC      = DONE (NO_IMPACT)
TRACK_I_INTEGRATION        = EN_COURS — benchmark R3 DONE (fe9ba0c)
```

## Commits locaux R3 (aucun push)

```
e39d9db3 feat(routes): session import/export (Track F R3)
af515e2d fix(routes): bugs trouvés par le harnais compat v2
3c1633ef test(compat): harnais v2 stateful+SSE (Track E R3)
f970c29d chore(routes): wiring import_export dans le router intégrateur
fe9ba0c8 docs(r3): benchmark impact + script perf corrigé
```

## Bugfixes port (via harnais v2, commit af515e2d)

```
1. sessions rename : panic sur titre multi-octets (slice UTF-8) → chars().take(80)
2. compact() : model/model_provider émis bruts ("" vs null) — parité upstream
3. sessions/new : défauts upstream (workspace résolu, profile "default", model "")
4. settings : password_hash ajouté à CONTROL_KEYS (jamais persisté/exposé)
5. settings : _set_password/_clear_password réellement appliqués (Track B R3) ;
   409 seulement si HERMES_WEBUI_PASSWORD posée (parité upstream routes.py:15944)
6. health : sessions = Store::count() réel (plus 0 codé en dur)
```

## Deltas documentés (harnais v2, jamais masqués)

```
- persistance sessions : port immédiate vs upstream différée (superset)
- routes /api/workspace/* propres au port (upstream: /api/list + /api/file)
- context_length/threshold_tokens : Python résout via hermes-agent (0),
  port = champ persisté null (HERMES_BRIDGE_REQUIRED)
- server_tz : +0000 (port) vs +0200 (tz locale Python)
- rate-limit login 5/60s : upstream 429 au 6e, port 401×6 (delta FAIL rapporté)
```

## Gate

```
CARGO_FMT = PASS · CARGO_CLIPPY = 0 warnings
CARGO_TEST = 109 passed, 17 ignored (14 suites, ~24s)
  (108 avant + 1 nouveau test settings env-precedence)
HARNESS_V2 = 39/40 scénarios PASS, 214/215 assertions (1 delta documenté)
BENCHMARK_R3 = Python(HTTPS) 3.217 ms vs Rust release 0.216 ms /health (N=200)
```

## Prochaines actions

```
1. Soak tests (r3-stability.md) — scénarios 1-7, après intégration E/F
2. Archive sanitizée + SHA256
3. Rapport final §28 + verdict + décision R4 (options A-D du mandat)
4. Décision Ed : port du rate-limit login (delta restant) en R4
```

## Red lines

```
PUSH = NON | PR = NON | UPSTREAM = NON | PROD_WEBUI = NON_TOUCHÉ
DONNÉES RÉELLES = NON (state.db lecture mode=ro uniquement)
BRIDGE = localhost uniquement | SECRETS = AUCUN | PROFESSEUR = NON
```
