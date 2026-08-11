# Handoff — Hermes WebUI Rust R4 (rate-limit + workspace alignment)

TIMESTAMP = 2026-08-11T14:00:00Z
BRANCHE = rust-port/r4-rate-limit-workspace
HEAD = 2a5fe6a (2 commits R4 locaux, AUCUN push)
BASE_R3 = 61e72f9 (R3_CORE_COMPLETE_WITH_DEBT)
UPSTREAM = bd91b649 (exp-v0.52.193) — delta NO_IMPACT
PROD_WEBUI = NON_TOUCHE

## Tracks R4 (Option A)

```
TRACK_1_RATE_LIMIT_LOGIN = DONE — commit 06c051f
  Port fidèle api/auth.py _check_login_rate/_record_login_attempt/
  _clear_login_attempts : fichier STATE_DIR/.login_attempts.json, 5 tentatives
  / IP / fenêtre glissante 60s, purge des expirés, écriture atomique
  (tmp+fsync+chmod 0600+rename), thread-safe (Arc<Mutex>).
  Branche dans /api/auth/login : check avant PBKDF2, record sur échec (401),
  clear sur succès (200). 429 = 'Too many attempts. Try again in a minute.'
  Tests unitaires 3/3 (max attempts, persistance, fichier corrompu).

TRACK_2_WORKSPACE_ALIGNMENT = DONE — commit 2a5fe6a
  Expose /api/list + /api/file (contrat upstream) sur le port, réutilisant
  les handlers list/read existants. session_id toléré (le port isole sur
  workspace_root, upstream résout via session).
  Harnais : scénarios workspace comparés CROSS-SERVEUR via /api/list +
  /api/file (contenu, size, path, types, traversal) — plus de delta
  structurel R2 sur ces routes.

TRACK_3_COMPAT_40_40 = DONE
  Harnais v2 : 40/40 scénarios PASS, 220/220 assertions (0 FAIL).
  Le rate-limit 5/60s est confirmé cross-serveur (python 401×5→429 == rust).

TRACK_4_NON_REGRESSION = DONE
  Gate : fmt PASS, clippy 0, cargo test 111 passed / 17 ignored.
  Benchmark : Rust release /health 0.215 ms (R3: 0.216), RSS 1.9 MB,
  concurrence 100 PASS. Aucune régression.
```

## Commits locaux R4 (aucun push)

```
06c051f feat(auth): login rate-limit 5/60s (R4 Track 1)
2a5fe6a feat(routes): alignement workspace upstream + harnais release (R4 Track 2)
```

## Piège découvert (documenté)

```
Le rate-limit login exige le binaire RELEASE : en debug, PBKDF2 600k prend
~15s/login → 6 logins dépassent la fenêtre de 60s et les premières tentatives
expirent avant le 6e (pas de 429). En release (~0.8s/login) les 6 logins
tiennent dans la fenêtre et le 429 au 6e est déclenché.
→ run_compat_v2.sh utilise désormais target/release (CARGO_PROFILE_RELEASE_LTO=thin).
```

## Gate final R4

```
CARGO_FMT = PASS · CARGO_CLIPPY = 0 warnings
CARGO_TEST = 111 passed, 17 ignored (14 suites, ~24s)
HARNESS_V2 = 40/40 scénarios PASS, 220/220 assertions (0 FAIL)
BENCHMARK = Rust release 0.215 ms /health (R3: 0.216), RSS 1.9 MB, conc 100 PASS
```

## Deltas restants (documentés, non masqués)

```
- persistance sessions : port immédiate vs upstream différée (superset)
- context_length/threshold_tokens : Python résout via hermes-agent (0),
  port = champ persisté null (HERMES_BRIDGE_REQUIRED)
- server_tz : +0000 (port) vs +0200 (tz locale Python)
```

## Prochaines actions

```
1. Archive sanitizée + SHA256 (R4)
2. Rapport final R4 + verdict
3. Décision Ed : push des commits R3+R4 (GO explicite requis)
```

## Red lines

```
PUSH = NON | PR = NON | UPSTREAM = NON | PROD_WEBUI = NON_TOUCHÉ
DONNÉES RÉELLES = NON | BRIDGE = localhost | SECRETS = AUCUN
```
