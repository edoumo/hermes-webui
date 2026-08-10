# R3 — Stabilité / Soak Tests

## Objectif

Détecter les régressions évidentes (crash, panic, fd leak, task leak,
croissance mémoire grossière) sur les nouveaux parcours R3. Pas besoin d'un
test de plusieurs heures — un soak local court suffit.

## Scénarios de soak (à exécuter après intégration R3)

| # | Scénario | Volume | Mesures |
|---|---|---|---|
| 1 | Créations/lectures sessions temporaires | 100 itérations | crash, panic, fd count |
| 2 | Streams mock séquentiels (bridge) | 100 itérations | crash, task leak, RSS |
| 3 | Streams concurrents (bridge) | 10 × 10 | crash, task leak |
| 4 | Cancel répétée | 50 itérations | crash, stream terminé proprement |
| 5 | Uploads temporaires | 50 fichiers | crash, fd leak, disk growth |
| 6 | Login/logout répété | 50 itérations | crash, session store growth |
| 7 | Agent cache A/B alterné | 30 itérations | isolation, eviction, RSS |

## Mesures

```
CRASH = 0
PANIC = 0
FD_LEAK = fd count stable avant/après (tolérance ±5)
TASK_LEAK = aucun task actif résiduel après soak
MEMOIRE = RSS grossier avant/après (tolérance documentée)
```

## Commandes de référence

```bash
# fd count d'un process
ls /proc/<pid>/fd | wc -l
# RSS
ps -o rss= -p <pid>
```

## Statut

```
SOAK_TESTS = PENDING (à exécuter après intégration des tracks A/B/D)
```
