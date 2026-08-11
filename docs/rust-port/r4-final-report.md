# R4 — Rapport final (Hermes WebUI Rust port)

TIMESTAMP = 2026-08-11T14:00:00Z
BRANCHE = rust-port/r4-rate-limit-workspace
HEAD = 2a5fe6a (2 commits R4 locaux, AUCUN push)
BASE_R3 = 61e72f9 (R3_CORE_COMPLETE_WITH_DEBT)
UPSTREAM = bd91b649 (exp-v0.52.193) — delta NO_IMPACT
PROD_WEBUI = NON_TOUCHE

## Tracks R4 (Option A)

```
TRACK_1_RATE_LIMIT_LOGIN = DONE
  Port fidèle api/auth.py : .login_attempts.json, 5/60s, purge, atomique 0600,
  thread-safe. Branche dans /api/auth/login (check/record/clear). 429 au 6e.
  Tests 3/3.

TRACK_2_WORKSPACE_ALIGNMENT = DONE
  /api/list + /api/file exposés (contrat upstream), réutilisant list/read.
  Harnais : scénarios workspace comparés cross-serveur.

TRACK_3_COMPAT_40_40 = DONE
  Harnais v2 : 40/40 scénarios PASS, 220/220 assertions (0 FAIL).

TRACK_4_NON_REGRESSION = DONE
  Gate vert, benchmark inchangé.
```

## Gate final R4

```
CARGO_FMT = PASS · CARGO_CLIPPY = 0 warnings
CARGO_TEST = 111 passed, 17 ignored (14 suites, ~24s)
HARNESS_V2 = 40/40 scénarios PASS, 220/220 assertions (0 FAIL)
BENCHMARK = Rust release 0.215 ms /health (R3: 0.216), RSS 1.9 MB, conc 100 PASS
```

## Verdict

```
VERDICT = R4_COMPLETE
```

Justification : les 4 tracks R4 (Option A) sont livrées et qualifiées. Le
rate-limit login 5/60s est porté et confirmé cross-serveur (le dernier delta
fonctionnel R3 est fermé). L'alignement workspace supprime le delta structurel
R2 sur /api/list + /api/file. Compat 40/40, gate vert, benchmark inchangé.

## Deltas restants (documentés, non masqués)

```
- persistance sessions : port immédiate vs upstream différée (superset)
- context_length/threshold_tokens : HERMES_BRIDGE_REQUIRED (null vs 0)
- server_tz : +0000 (port) vs +0200 (tz locale Python)
```

Ces deltas sont structurels (persistance) ou HERMES_BRIDGE_REQUIRED (résolution
runtime hermes-agent) — aucun n'est un delta fonctionnel observable sur les
routes portées.

## Commits locaux R4 (aucun push)

```
06c051f feat(auth): login rate-limit 5/60s (R4 Track 1)
2a5fe6a feat(routes): alignement workspace upstream + harnais release (R4 Track 2)
```

## Red lines

```
PUSH = NON | PR = NON | UPSTREAM = NON | PROD_WEBUI = NON_TOUCHÉ
DONNÉES RÉELLES = NON | BRIDGE = localhost | SECRETS = AUCUN
```
