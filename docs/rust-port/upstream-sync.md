# Doctrine de synchronisation upstream — Hermes WebUI Rust

Mécanisme documenté pour rester suivable par rapport à `nesquena/hermes-webui`.

## Principe

```
upstream SHA N
    |
    +-- diff vers SHA N+1
    |
    +-- classification
          frontend
          Python backend
          API contract
          tests
          docs
    |
    +-- impact Rust
    |
    +-- port
    |
    +-- compatibility tests
```

Objectif à terme : pouvoir dire `Rust port compatible with upstream <SHA/version>`,
pas `probablement similaire à Hermes WebUI`.

## Baseline actuelle

```
UPSTREAM_BASELINE_SHA = 192df903
UPSTREAM_BASELINE_VERSION = exp-v0.52.192
BRANCHE_PORT = rust-port/r0-r1-bootstrap
```

## Procédure de sync (à chaque release upstream)

1. **Fetch** : `git fetch upstream` dans le repo de travail.
2. **Diff** : `git log --oneline <baseline>..upstream/master` — lister les commits.
3. **Classer** chaque commit :
   - `frontend` : static/*, i18n, thèmes → impact Rust = servir les nouveaux assets (aucun port).
   - `Python backend` : api/*.py → vérifier si une route portée en Rust a changé de contrat.
   - `API contract` : changement de shape JSON / route / header → **priorité** : re-run harnais.
   - `tests` : tests/ → adapter les fixtures de compat si le contrat a changé.
   - `docs` : docs/ → mettre à jour baseline.
4. **Impact Rust** : pour chaque route portée (health, static, index, settings), comparer
   le handler upstream modifié au handler Rust. Si le contrat a changé, porter le delta.
5. **Port** : appliquer le delta Rust + mettre à jour `docs/rust-port/api-contract.md`.
6. **Compat tests** : re-run `tests/compat/compare.py` (Python upstream vs Rust) → viser 12/12.
7. **Mettre à jour** la baseline SHA dans ce fichier et dans `upstream-baseline.md`.

## Règles

- Ne jamais merger silencieusement : montrer le changelog classé avant d'agir.
- Le frontend upstream est la source de vérité visuelle : le servir tel quel.
- Un changement de contrat API upstream prime sur toute optimisation Rust.
- Les deltas tolérés (agent_version, state_db) sont documentés dans `tests/compat/compare.py`
  — les re-vérifier à chaque sync (un bridge Hermes en R2 peut les résoudre).

## Vérification de compatibilité

```bash
# 1. Démarrer Python upstream isolé (voir tests/compat/README.md)
# 2. Démarrer Rust port
# 3. Comparer
python3 tests/compat/compare.py --python-url http://127.0.0.1:8793 --rust-url http://127.0.0.1:8792
# Attendu : RESULT: 12/12 routes in parity
```
