# R2 — Auth / Security Foundation (Track E)

## Objectif

Préparer puis porter la couche auth WebUI sans toucher aux vrais credentials.
Fondation validée ; le password réel (PBKDF2 600k) est différé en R3.

## Ce qui est porté (fidèle upstream, baseline 192df903)

| Élément | Implémentation Rust | Référence upstream |
|---|---|---|
| Cookie session | `{token}.{hmac_sha256_hex}` | create_session (auth.py:604) |
| Token | hex(32 octets) | secrets.token_hex(32) |
| Signature | HMAC-SHA256, clé dérivée du state_dir (isolé) | _signing_key() |
| TTL | 30 jours | SESSION_TTL (auth.py:28) |
| CSRF | HMAC-SHA256("csrf:{token}") | csrf_token_for_session (auth.py:988) |
| Cookie flags | HttpOnly, SameSite=Lax, Path=/ | set_auth_cookie |
| Login | POST /api/auth/login → Set-Cookie + csrf_token | routes.py |
| Logout | POST /api/auth/logout → cookie expiré (Max-Age=0) | routes.py |
| Status | GET /api/auth/status → logged_in, auth_enabled, csrf | routes.py |
| password_hash | JAMAIS exposé dans les réponses | pop(password_hash) |

## Écarts documentés (intentionnels, temporaires)

1. **Password réel non porté** : le login R2 crée une session signée sans
   valider de mot de passe (auth désactivée par défaut upstream quand aucun
   password_hash n'existe). Le PBKDF2-HMAC-SHA256 600k itérations est
   `AUTH_PASSWORD = R3_IMPLEMENT`.
2. **Clé de signature** : dérivée du state_dir (isolé par instance de test),
   pas d'une vraie clé de prod. Suffisant pour la fondation ; la gestion de
   clé réelle est R3.
3. **WebAuthn/passkeys** : `AUTH_WEBAUTHN = R3_DEFER` (couplage navigateur +
   challenge, hors périmètre R2).

## Tests (9, tous verts)

no auth configured, signed cookie valid, tampered cookie rejected, expired
cookie rejected, CSRF stable par session, login sets signed cookie, status
reflète login/logout, status sans cookie, password_hash jamais exposé.

## Fichiers

```
rust-server/src/auth/mod.rs      — SessionStore, create/verify session, CSRF,
                                   login/logout/status, security_headers
rust-server/tests/test_auth.rs   — 9 tests
```

## Sécurité

- Aucun secret réel importé (fixtures uniquement).
- Aucun password_hash dans les réponses.
- Security headers : X-Content-Type-Options, X-Frame-Options, Referrer-Policy.
- Tests sur state_dir temporaire isolé.
