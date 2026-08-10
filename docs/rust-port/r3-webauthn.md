# R3 — WebAuthn / Passkeys (Track C)

## Objectif

Porter l'auth WebAuthn/passkeys du WebUI upstream en Rust, avec la même
politique de sécurité (challenges single-use, TTL, origin strict).

## Contrat upstream vérifié (api/passkeys.py + api/routes.py, baseline 192df903)

- Fichiers : `passkeys.json` (credentials) et `.passkey_challenges.json`
  (challenges), dans STATE_DIR, écrits atomiquement (mkstemp + fsync +
  chmod 0600 + rename).
- Challenges : TTL 90 s, single-use (consommés à la vérification), max 128
  globaux / 8 par contexte (kind+rp_id+origin), éviction du plus ancien.
- RP : RP_NAME = "Hermes WebUI", RP ID = host sans port (gère IPv6 [::1]),
  origin = {proto}://{Host} (proto via X-Forwarded-Proto sinon contexte
  sécurisé), user.id = b64u(sha256(rp_id)[:16]).
- Routes : GET /api/auth/passkey/options, POST /api/auth/passkey/login,
  GET /api/auth/passkey/register/options, POST /api/auth/passkey/register,
  POST /api/auth/passkey/delete, GET /api/auth/passkeys.
- Feature flag : HERMES_WEBUI_PASSKEY=1 (sinon 404).
- Delete : dernier passkey sans password → 409 "Set a password".

## Implémentation Rust (src/auth/webauthn.rs)

- Crate : webauthn-rs 0.6.1-dev (safe wrapper, pas de crypto maison).
- ChallengeStore : challenges en mémoire (TTL 90s, single-use, max 128/8 par
  contexte) + états webauthn-rs typés associés (jamais sérialisés sur disque).
- PasskeyStore : passkeys.json format upstream + champ webauthn_rs
  (Passkey sérialisé), sign counter mis à jour après auth.
- 6 routes fidèles upstream, feature flag, origin/RP ID stricts.

## Tests (tests/test_webauthn.rs)

- 14 tests : flag off → 404, auth off → 400, options sans passkeys → 400,
  login credential inconnu → 401, delete inconnu → 404, delete dernier sans
  password → 409, delete avec password → OK, list vide, challenge store
  single-use/TTL/eviction, credential store round-trip/delete/malformed.
- Sérialisation des tests qui touchent HERMES_WEBUI_PASSKEY (env var
  globale, course entre tests parallèles).

## Résultat

```
TESTS = 14/14 PASS
GATE = fmt PASS, clippy 0, cargo test global vert (94 passed, 17 ignored debug)
SOAK = flag OFF → 404 sur /api/auth/passkey/* (contrat upstream respecté)
BRIDGE = v1 chat SSE via proxy Rust OK (start→reasoning→token→tool_start→
         tool_result→token→done→[DONE]) ; v1+ agent-cache listé ; v1/sessions
         → StateDbUnavailable en mode mock (state.db absent, attendu)
```

## Déviation documentée (vs upstream)

- Upstream persiste les challenges sur disque (`.passkey_challenges.json`) ;
  le port Rust les garde **en mémoire** (TTL 90s, single-use, max 128/8 par
  contexte) et ne sérialise jamais les états webauthn-rs (évite la feature
  `danger-allow-state-serialisation`). Les credentials restent persistés
  (passkeys.json format upstream + champ `webauthn_rs`).

