# Hermes Bridge Protocol v1

Protocole local entre Hermes WebUI Rust et le bridge Python qui wrappe
Hermes Agent (AIAgent). Transport : HTTP sur localhost uniquement + SSE
pour le streaming.

## Principe

```
Browser
  -> Rust Axum WebUI (routes upstream)
       -> Bridge Python (HTTP/SSE localhost)   [ce protocole]
            -> Hermes Agent / AIAgent
```

Le bridge est MINCE : il adapte les APIs Python natives Hermes vers un
contrat transportable. Il ne devient PAS un deuxième backend WebUI.

## Transport & sécurité

- Écoute uniquement sur `127.0.0.1` (ou socket Unix) par défaut.
- Jamais exposé sur 0.0.0.0.
- Ne jamais transmettre de clé API dans les events/logs.
- Requêtes concurrentes supportées ; un `request_id` est strictement
  associé à un stream.

## Endpoints

### GET /v1/health
```
200 = {"status":"ok","service":"hermes-bridge","protocol_version":"1.0",
       "agent_available":<bool>,"mode":"real|mock"}
```

### GET /v1/version
```
200 = {"protocol_version":"1.0","bridge_version":"0.1.0"}
```

### POST /v1/chat  (SSE stream)
```
Body JSON :
  request_id   (str, obligatoire — associé strictement au stream)
  session_id   (str)
  message      (str)
  profile      (str, optionnel)
  model        (str, optionnel)
  provider     (str, optionnel)
  workspace    (str, optionnel)
  history      (list de {role,content}, optionnel — contexte de session)
  approval_policy (str, optionnel)

Réponse : text/event-stream (SSE), events ci-dessous.
```

### POST /v1/cancel
```
Body JSON : { "request_id": str, "session_id": str }
200 = {"cancelled": true}
Le stream de ce request_id émet un event "cancelled" et s'arrête.
```

### GET /v1/streams
```
200 = {"active": [request_id, ...]}   (diagnostic, non critique)
```

## Events SSE (POST /v1/chat)

Chaque event : `event: <name>\ndata: <json>\n\n`

| event | data | signification |
|---|---|---|
| `start` | `{request_id}` | début du turn |
| `token` | `{request_id, text}` | delta de texte assistant |
| `reasoning` | `{request_id, text}` | reasoning/thinking |
| `tool_start` | `{request_id, name, arguments}` | un tool commence |
| `tool_result` | `{request_id, name, result}` | résultat de tool |
| `approval_required` | `{request_id, question, choices}` | demande d'approbation |
| `error` | `{request_id, error, error_class}` | erreur |
| `usage` | `{request_id, ...}` | métriques tokens |
| `done` | `{request_id, final_response}` | fin propre |
| `cancelled` | `{request_id}` | annulé |

Le stream se termine par `done` ou `cancelled` ou `error`. Pas de timestamps
contractuels (la sémantique compte, pas les timings).

## Lifecycle

- `request_id` → un seul stream actif à la fois.
- `POST /v1/cancel` marque l'annulation ; le prochain point d'arrêt émet
  `cancelled` et termine.
- shutdown propre : SIGTERM/SIGINT → arrêt des streams actifs + sortie 0.

## Erreurs

- `error_class` utilise la taxonomie : BadRequest, Unauthorized, Forbidden,
  NotFound, Conflict, PayloadTooLarge, HermesUnavailable, HermesProtocolError,
  Internal.
- Jamais de backtrace vers le client.

## Versionnage

- `protocol_version` = "1.0" dans /v1/version.
- Tout changement incompatible → bump majeur du protocole.
