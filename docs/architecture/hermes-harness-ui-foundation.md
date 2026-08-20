# Hermes Harness UI foundation

Status: `FOUNDATION_CODE_READY_FOR_INTEGRATED_QUALIFICATION`

This document describes the first experimental Hermes Harness UI slice. It is
not a replacement for the shipped Hermes WebUI yet and it is not enabled by
default.

## Qualified backend baseline

Hermes Agent repository: `edoumo/hermes-agent`

Durable Workers H2.1 baseline:

`bf336decb0ba298e85e5a34cf5b7a596f7dee2dc`

H2.1 real-runtime verdict:

`H2_1_B1_REQUALIFICATION_STATUS=PASS`

That backend demonstrated real worker creation, durable inbox idempotency,
actual DeepSeek activations, cold context continuity, worker serialization,
global activation capacity, task DAG writes, SSE invalidation, cooperative
drain, crash recovery, session isolation and process-metadata privacy.

## WebUI source baseline

The `edoumo/hermes-webui` default branch was intentionally not used as the
foundation because it was an ancestor from 2026-06-17. The experimental branch
was created directly from current upstream `nesquena/hermes-webui`:

`1f5e45527ac8eb14230a9f5720664d0867e74035`

Branch:

`experimental/hermes-harness-ui-foundation`

The fork's `master` branch remains untouched.

## Architecture

```text
Browser
   |
   | same-origin cookie + CSRF
   v
Hermes Harness UI server (localhost, experimental)
   |
   | server-side Bearer only
   v
Canonical Hermes API (H2.1)
   |
   v
Hermes Agent
   |
   +-- DurableWorkerStore
   +-- SubagentLifecycleService
   +-- Memory / Skills / Tools / runtime providers
```

The Harness server is a thin UI/BFF. It does not instantiate `AIAgent`, does
not read `durable-workers.db`, and does not own worker lifecycle state.

The browser never receives `HERMES_WEBUI_GATEWAY_API_KEY`.

## Transitional coexistence

The legacy WebUI may continue to run on its normal port while Harness runs on a
different localhost port (default `8790`). Harness has a separate WebUI auth
state directory and cookie namespace so two Python processes do not race on the
same `.sessions.json` or overwrite one another's browser cookie.

Default Harness state:

`~/.hermes/webui-harness`

Default Harness cookie:

`hermes_harness_session`

Harness remains localhost-only in this foundation. Public or remote deployment
is deliberately out of scope until a separate threat model and deployment gate
exist.

## BFF security boundary

`api/harness_ui.py` implements a strict route allowlist. It is not an arbitrary
reverse proxy.

Supported browser prefix:

`/api/harness`

The BFF permits only the native Hermes sessions/models reads and the H2.1
Durable Workers routes needed by the first UI.

Controls:

1. Hermes API origin must be loopback (`127.0.0.1`, `localhost`, or `::1`).
2. URL credentials are rejected.
3. Arbitrary upstream paths are impossible through the resolver.
4. Only explicit GET/POST routes are accepted.
5. Normal JSON request bodies are capped at 256 KiB.
6. Normal upstream responses are capped at 4 MiB.
7. Query parameters are restricted to `limit` and `cursor`.
8. Redirects are not followed.
9. Inbound browser credentials are not forwarded to Hermes API.
10. The server adds only its configured Hermes API Bearer.
11. The SSE bridge forwards only a validated `Last-Event-ID`.
12. Host process metadata remains filtered by the already-qualified H2.1 API.

## WebUI auth and CSRF

`harness_server.py` reuses the WebUI authentication implementation and the
existing `_check_csrf` policy for unsafe same-origin requests.

The browser obtains its session-bound CSRF value from the authenticated
same-origin endpoint:

`GET /api/harness/csrf`

The token is then attached to Harness POST requests as:

`X-Hermes-CSRF-Token`

This token is a WebUI anti-CSRF value. It is not the Hermes API Bearer.

## Memory posture

The first UI intentionally avoids the architecture that caused large-session
browser memory pressure in older WebUI generations.

Rules in this slice:

1. Worker list is requested with `limit=100`.
2. Task list is requested with `limit=100`.
3. Only the selected worker's latest 50 messages are materialized.
4. Only the selected worker's latest 50 activations are materialized.
5. No transcript is persisted to `localStorage`.
6. No reasoning trace or tool activity journal is mirrored into the page.
7. SSE carries only invalidation/change tokens; after a change the client
   reloads bounded projections.
8. Switching sessions closes the old EventSource before opening another.

The first implementation therefore treats the Hermes API as source of truth and
the DOM as a bounded projection, not as a second conversation database.

## First UI surface

The standalone page is `/harness` and currently exposes:

* Hermes API session selection and creation
* Durable Worker list and creation
* worker status/role/model/toolset chips
* bounded durable message history
* idempotent message enqueue field
* asynchronous `run next`
* bounded activation history
* Task DAG list, creation and guarded status transitions
* session-scoped `durable_workers.changed` SSE refresh

No delete worker, permanent disable, worker steering, or direct cancel control is
exposed in this foundation.

## Start contract

The foundation is deliberately default-off.

Example environment is in `.env.harness.example`.

Conceptual lab launch:

```bash
export HERMES_WEBUI_HARNESS_UI=1
export HERMES_WEBUI_GATEWAY_BASE_URL=http://127.0.0.1:8642
export HERMES_WEBUI_GATEWAY_API_KEY='...'
python harness_server.py
```

The server refuses a non-loopback `HERMES_HARNESS_HOST`.

## Current validation status

Repository contract tests are in:

`tests/test_harness_ui_foundation.py`

They cover:

* default-off behavior
* exact route/method allowlist
* path traversal rejection
* loopback-only Hermes API origin
* rejection of URL credentials
* server-side Bearer placement
* request/query bounds
* absence of Bearer/API-key references in browser assets
* explicit bounded browser projections
* EventSource use
* CSRF/auth hooks
* separate Harness auth state
* non-modification of `server.py`

The upstream WebUI GitHub Actions workflow runs only for pushes to `master` or
pull requests targeting `master`. No PR is authorized for this experimental
branch, so a full upstream CI result must not be claimed yet.

The next gate is an isolated integrated recipe on the real Hermes environment,
using the qualified H2.1 API. It must not modify the main WebUI, Gateway, Hermes
runtime, systemd, network, firewall or public exposure.

## Gate to the next slice

The foundation can move to the next UI slice only after an integrated recipe
proves at least:

* Harness and legacy WebUI can coexist without cookie/session interference
* Browser never receives the Hermes API Bearer
* authenticated CSRF-protected worker creation works
* real worker activation goes STARTING -> RUNNING -> SUCCEEDED through the BFF
* SSE refreshes the bounded UI after worker state changes
* session isolation remains fail-closed through the BFF
* browser memory remains bounded during repeated refresh/activation cycles
* stopping Harness leaves legacy WebUI and Hermes runtime untouched
