# R3 — Compat Harness v2 (Track E)

## Objectif

Étendre le harnais de compatibilité Python/Rust à des scénarios STATEFUL
(sessions CRUD, settings, auth, workspace) et SSE (bridge mock), exécutés
réellement contre les deux serveurs, avec rapport PASS/FAIL et deltas
documentés (jamais masqués).

## Livrable

```
tests/compat/compat_v2.py        harnais v2 (40 scénarios, runner, normalisations)
tests/compat/run_compat_v2.sh    orchestration 3 serveurs (bridge mock 8794,
                                 python upstream 8793, rust 8792)
tests/compat/fixtures/           session_compat_v2.json + workspace_sample.txt
```

Lancement :

```bash
bash tests/compat/run_compat_v2.sh   # build rust + démarre + compare + arrête
```

## Exécution réelle (2026-08-10)

### Run de référence avant corrections

```
SCENARIOS = 34 | PASS = 13 | FAIL = 21 | ASSERTIONS = 134/173
```

Causes des FAIL triées en 3 catégories :

### A. Bugs du harnais (écrit par le worker sans jamais avoir été exécuté)

| Bug | Correctif |
|---|---|
| `_absorb_cookies` appelait `.items()` sur une liste de paires → crash de TOUS les scénarios | itérer les paires directement |
| `SSE_EXPECTED` omettait l'event final `data: [DONE]` (sans nom → `None`) | séquence = 7 events + `None` |
| `title "Nouvelle session"` attendu — upstream IGNORE le title du body à la création (`Untitled`) | assert `Untitled` + note upstream |
| `delete-missing` utilisait un sid avec apostrophes (invalide → 400) | sid alnum safe absent |
| `.pbkdf2_key` lu en `read_text()` — fichier BINAIRE → UnicodeDecodeError | `read_bytes().hex()` |
| routes `/api/workspace/*` comparées à Python — routes INEXISTANTES côté upstream | scénarios = qualification du contrat du port + delta documenté |

### B. Vrais bugs du port Rust corrigés (3)

| Bug | Fichier | Correctif |
|---|---|---|
| `apply_title_rename` panic sur titre multi-octets (slice par octets sur UTF-8) — "Remote end closed connection" sur rename avec titre accentué | `src/sessions/mod.rs:295` | `chars().take(80)` (frontières de caractères) |
| `password_hash` PERSISTÉ sur disque par POST /api/settings (absent de CONTROL_KEYS) — fuite potentielle + activait l'auth à l'insu | `src/routes/settings.rs:146` | ajout `password_hash` à `CONTROL_KEYS` |
| `/health` sessions codé en dur à 0 — store existant depuis R2 | `src/routes/health.rs` | `Store::count()` réel (fail-closed IO) |

### C. Deltas structurels documentés (pas des bugs, décisions R2)

1. **Workspace** : le port expose `/api/workspace/list|read|metadata|download`
   alors qu'upstream expose `/api/list` + `/api/file` (+ `/api/workspaces`).
   Le frontend upstream n'appelle PAS les routes du port. Dette de naming R2,
   à aligner en R4 (garder les routes du port en alias ou migrer le frontend).
2. **Shapes de réponse** : le port est un sous-ensemble progressif — le Python
   expose des champs absents du port (`_messages_offset`, `_messages_truncated`,
   `_msg_limit_max`, `composer_draft`, champs compression, etc.). Le harnais
   compare désormais en `⊇` (rust ⊆ python) avec **liste explicite** des clés
   rust manquantes côté python dans le rapport — jamais masqué.
3. **Rate-limit login** : upstream 5 essais/60s (429 au 6e) ; le port Rust ne
   l'implémente pas encore → delta rapporté (FAIL documenté, pas masqué).
4. **Auth status** : `auth_enabled` reflète le password stocké ; le fix B2
   supprime la pollution résiduelle qui activait l'auth pendant les tests.

## Comptage final

```
SCENARIOS_ENREGISTRES = 40 (>= 40 exigé par le mandat)
  sessions CRUD: 14 · settings: 4 · auth: 4 · sse: 7 · workspace: 4
  health/static: 3 · import/export (Track F): 6 · rate-limit: 1
```

## Statut

```
TRACK_E = EN_COURS (dernier run 13/34 avant corrections ; run de
          qualification en cours après correctifs A+B+C)
```
