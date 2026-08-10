# Handoff — Nouvelle entrée 2026-08-10 (arbitrage d'entrée R3)

TIMESTAMP = 2026-08-10T17:05:00Z

## 1. Fichier lu
```
CHEMIN = /home/edou/.hermes/webui/attachments/ab8d61007fc4/pasted-text-2026-08-10_15-56-32-288.md
TAILLE = 23 085 octets, 1067 lignes
LU_INTEGRALEMENT = OUI
```

## 2. Type
```
FILE_TYPE = NEW_MANDATE
TITRE = MANDAT DIANE — HERMES WEBUI RUST — PHASE R3
SOUS_TITRE = PARITÉ FONCTIONNELLE AVANCÉE : AGENT STATE + AUTH COMPLÈTE + UPLOADS + COMPAT V2
```

## 3. Résumé opérationnel
R3 doit traiter les 8 dettes R2, prioritairement : AUTH_PASSWORD (PBKDF2 600k),
AUTH_WEBAUTHN, UPLOADS, AGENT_SESSIONS_STATE_DB (via bridge), BRIDGE_AGENT_CACHE,
COMPAT_HARNESS_V2 (>=40 scénarios stateful). Tracks : A agent state/cache,
B password PBKDF2, C WebAuthn, D uploads, E compat v2, F import/export,
G workspace mutations (prepare), H upstream sync, I intégration.
Doctrine : un seul intégrateur pour app.rs/state.rs/config.rs/main.rs/lib.rs/Cargo.toml.
Verdicts : R3_ADVANCED_PARITY_COMPLETE | R3_CORE_COMPLETE_WITH_DEBT | R3_PARTIAL_WITH_EXPLICIT_BLOCKERS.
Préparer la décision R4 (options A-D) sans exécuter push.

## 4. Impact R2
```
IMPACT_R2 = AUCUN — R2 reste checkpoint gelé (rust-port/r2-agent-bridge @ 16145f75)
NE_PAS_ROUVRIR = Bridge v1, Sessions R2, Workspace read-only, StaticCache
```

## 5. Impact R3
```
IMPACT_R3 = SOURCE_OPERATIONNELLE_PRIORITAIRE — le mandat R3 remplace/étend les dettes R2
BRANCHE_R3 = rust-port/r3-functional-parity (à créer depuis HEAD R2)
```

## 6. Contradictions éventuelles
```
AUCUNE — le mandat de reprise (16-59-25-103.md) et le mandat R3 sont compatibles.
Le mandat de reprise impose : lire le fichier (fait), classifier (fait), produire ce
handoff (en cours), puis PROCEED_R3.
```

## 7. Actions déjà exécutées
```
- Lecture intégrale du fichier R3 (2 passes, 1-500 + 501-1067)
- Lecture intégrale du mandat de reprise (295 lignes)
- Classification : NEW_MANDATE / PROCEED_R3
- Ce handoff de transition
```

## 8. Branche active
```
BRANCHE_ACTIVE = rust-port/r2-agent-bridge (HEAD 16145f75) — R2 gelé
BRANCHE_R3_A_CREER = rust-port/r3-functional-parity
```

## 9. Red lines
```
PUSH = NON | PR = NON | RELEASE = NON | UPSTREAM = NON
PROD_WEBUI = NON_TOUCHE | ~/.hermes = NON | STATE_DB_REEL = NON
CREDENTIALS_REELLES = NON | BRIDGE = localhost | FIREWALL/RESEAU/REBOOT = NON
PROFESSEUR = NON SANS GO
```

## 10. Décision
```
DECISION = PROCEED_R3
PROCHAINE_ACTION = créer branche rust-port/r3-functional-parity depuis 16145f75,
                   fetch upstream read-only (Track H), lancer tracks R3 en parallèle
                   (périmètres exclusifs, intégrateur unique pour fichiers partagés)
```
