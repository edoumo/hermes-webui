# R3 — Rapport final (Hermes WebUI Rust port)

TIMESTAMP = 2026-08-11T12:00:00Z
BRANCHE = rust-port/r3-functional-parity
HEAD = 66c54673 (8 commits locaux R3, AUCUN push)
BASELINE_UPSTREAM = 192df903 (exp-v0.52.192)
UPSTREAM_ACTUEL = bd91b649 (exp-v0.52.193) — delta NO_IMPACT (a11y frontend)
PROD_WEBUI = NON_TOUCHE (service actif 192.168.1.187:8787)

## Tracks R3

```
TRACK_A_AGENT_STATE_CACHE = DONE (bridge v1+ : sessions, agent-cache)
TRACK_B_PASSWORD_PBKDF2    = DONE (PBKDF2 600k, migration sel legacy)
TRACK_C_WEBAUTHN           = DONE (webauthn-rs, challenges TTL 90s single-use)
TRACK_D_UPLOADS            = DONE (multipart, sanitize, 413 avant parsing)
TRACK_E_COMPAT_V2          = DONE (harnais 40 scénarios, 39/40 PASS)
TRACK_F_IMPORT_EXPORT      = DONE (export json/html + import, 14/14 tests)
TRACK_G_WORKSPACE_MUTATIONS = PREPARE_ONLY (non requis R3)
TRACK_H_UPSTREAM_SYNC      = DONE (NO_IMPACT)
TRACK_I_INTEGRATION        = DONE (benchmark + soak + gate final)
```

## Gate final (après soak)

```
CARGO_FMT = PASS
CARGO_CLIPPY = 0 warnings
CARGO_TEST = 108 passed, 17 ignored (14 suites, ~24s)
HARNESS_V2 = 39/40 scénarios PASS, 214/215 assertions
SOAK = 7/7 PASS (FD 10→10, RSS 6.8→9.7MB, PANIC=0, CRASH=0)
BENCHMARK = Python(HTTPS) 3.217 ms vs Rust release 0.216 ms /health (N=200)
RSS release 5.7 MB · concurrence 100 PASS
```

## Dettes fonctionnelles documentées (R4 candidates)

```
1. RATE_LIMIT_LOGIN : upstream 5 essais/60s (429 au 6e) ; le port vérifie le
   password (401) mais n'implémente pas _check_login_rate → delta FAIL
   rapporté par le harnais (jamais masqué). CANDIDAT R4 PRIORITAIRE.
2. WORKSPACE_ROUTES : le port expose /api/workspace/list|read|metadata alors
   qu'upstream expose /api/list + /api/file (dette de naming R2). Le frontend
   upstream n'appelle pas les routes du port. Alignement R4.
3. CONTEXT_LENGTH : Python résout context_length/threshold_tokens via le
   runtime hermes-agent (0) ; le port renvoie le champ persisté (null) —
   HERMES_BRIDGE_REQUIRED. Toléré + listé par le harnais.
4. SERVER_TZ : port UTC fixe (+0000) vs tz locale Python (+0200). Cosmétique.
5. PERSISTANCE_SESSIONS : port immédiate vs upstream différée au premier
   message (superset documenté, jamais cassé la compat du fichier).
```

## Verdict

```
VERDICT = R3_CORE_COMPLETE_WITH_DEBT
```

Justification : toutes les tracks R3 sont livrées et qualifiées (gate vert,
soak 7/7, harnais 39/40 avec 1 seul delta fonctionnel documenté). Le delta
rate-limit login est une dette fonctionnelle réelle (comportement observable
différent : pas de 429) — la convention `R3_ADVANCED_PARITY_COMPLETE` est
réservée à zéro delta fonctionnel, donc `R3_CORE_COMPLETE_WITH_DEBT` est le
verdict rigoureux. Le soak confirme la stabilité du reste.

## Archive

```
FICHIER = /tmp/hermes-webui-rust-r3-archive.tar.gz (22.3 MB)
SHA256  = 443d9149341270b05c476581c09ff347f3977d6c02b0f34c4658440a453ba0a2
SANITIZE = git archive HEAD (pas de target/, .git, venv) ; grep secrets :
           seuls des fixtures de tests de redaction (aucun secret réel)
```

## Commits locaux R3 (aucun push)

```
e39d9db3 feat(routes): session import/export (Track F R3)
af515e2d fix(routes): bugs trouvés par le harnais compat v2
3c1633ef test(compat): harnais v2 stateful+SSE (Track E R3)
f970c295 chore(routes): wiring import_export dans le router intégrateur
fe9ba0c9 docs(r3): benchmark impact + script perf corrigé
36faf23e docs(handoff): R3 tracks E/F finalisées
af7b2720 test(soak): script R3 stability + fmt test_settings
66c54673 docs(r3): stability soak results
```

## Décision R4 (proposition)

```
OPTION_A (recommandée) : R4 ciblé — porter _check_login_rate (5/60s, fichier
  .login_attempts.json upstream) + aligner les routes workspace sur /api/list
  + /api/file. Périmètre court, 2 tracks, gate identique.
OPTION_B : R4 complet — A + context_length via bridge (HERMES_BRIDGE_REQUIRED)
  + server_tz local. Périmètre moyen.
OPTION_C : geler R3 tel quel, R4 plus tard (dettes documentées, aucune
  bloquante pour la parité fonctionnelle de base).
OPTION_D : arrêter le port (non recommandé — le port est stable et qualifié).
```

## Red lines

```
PUSH = NON | PR = NON | UPSTREAM = NON | PROD_WEBUI = NON_TOUCHÉ
DONNÉES RÉELLES = NON | BRIDGE = localhost | SECRETS = AUCUN
```
