# R3 — Auth Password PBKDF2 (Track B)

## Objectif

Porter l'auth password PBKDF2 compatible upstream, avec interopérabilité
prouvée contre le vrai code Python.

## Contrat upstream vérifié (HEAD bd91b649)

- `api/auth.py` : `hash_password` = PBKDF2-HMAC-SHA256, itérations lues
  depuis `settings.json` (`pbkdf2_iterations`, défaut 600_000), sel =
  `.pbkdf2_key` (32 octets, mode 0600), hash stocké hex brut dans
  `settings.json` (`password_hash`).
- `verify_password` : comparaison constant-time, migration legacy
  `.signing_key` (sel) si présent.
- `is_password_auth_enabled` : `password_hash` présent dans settings.json
  (l'env `HERMES_WEBUI_PASSWORD` a priorité — auth.py:423).
- Bootstrap : `POST /api/settings` avec `password` → hash + persistance.

## Implémentation (rust-server/src/auth/password.rs)

```
hash_password(state_dir, password) -> String (hex 64)
verify_password(state_dir, password) -> (bool, migrated)
is_password_auth_enabled(state_dir) -> bool
bootstrap_password(state_dir, password) -> String
set_password(state_dir, password) / remove_password(state_dir)
```

- PBKDF2-HMAC-SHA256 conforme RFC 2898 (dkLen 32, un bloc T_1),
  implémentation manuelle vérifiée contre `hashlib.pbkdf2_hmac`
  (vecteurs Python générés avec le vrai code upstream).
- `.pbkdf2_key` : 32 octets aléatoires, mode 0600, stable (réutilisé).
- Comparaison constant-time (pas d'early exit).
- Migration legacy `.signing_key` → `.pbkdf2_key` sans ré-hash.

## Vecteurs de compatibilité (générés avec le code upstream réel)

```
SALT_HEX, VEC_1000_ascii/unicode/empty/long — interopérabilité prouvée
```

## Tests

```
DEBUG (gate rapide) : 8 passed, 16 ignored (15s) — les tests 600k réels
  sont #[ignore] (coût ~8s/hash en debug)
RELEASE (couverture complète) : 16/16 passed --ignored (166.7s)
TOTAL = 24 tests (22 uniques + 2 variantes)
```

## Wiring (intégrateur)

- `auth/mod.rs` : `login` vérifie PBKDF2 quand `password_hash` présent
  (401 sinon), `auth_status` reflète `password_auth_enabled` réel.
- `test_auth.rs` : retrait de `HERMES_WEBUI_PASSWORD` héritée (priorité env).

## Coût

- 600k itérations : ~0.8s en release (upstream identique) — coût voulu
  par la politique anti-bruteforce.
