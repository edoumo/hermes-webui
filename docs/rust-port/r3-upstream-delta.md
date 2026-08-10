# R3 — Upstream Delta

## Ouverture R3 (2026-08-10)

```
UPSTREAM_HEAD_OUVERTURE = bd91b649 (exp-v0.52.193)
UPSTREAM_VERSION = exp-v0.52.193
BASELINE_R2 = 192df903 (exp-v0.52.192)
COMMITS_DEPUIS_BASELINE_R2 = 2
  70c77095 fix(a11y): clear stale aria-expanded when switching panels (#6656)
  bd91b649 Release exp-v0.52.193: a11y aria-expanded clear on panel switch (#6887)
```

## Classification

| Commit | Type | Impact R3 |
|---|---|---|
| 70c77095 | FRONTEND_ONLY (a11y aria-expanded, static/*) | AUCUN — frontend servi tel quel |
| bd91b649 | FRONTEND_ONLY (release/changelog) | AUCUN |

```
UPSTREAM_DELTA = NO_IMPACT
RECOMMANDATION = SYNC_R3 (aucune urgence ; frontend exp-v0.52.193 servi tel quel)
```

## Règle appliquée

Pas de rebase automatique pendant R3 (doctrine §16 du mandat R3). La baseline
reste `192df903` ; le delta est documenté, pas intégré. Un fetch read-only
sera refait en clôture R3 pour re-vérifier.
