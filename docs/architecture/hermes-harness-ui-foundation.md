# Hermes Harness UI foundation

Status: `H3_HARNESS_UI_FOUNDATION_STATUS=PASS`

This document describes the first experimental Hermes Harness UI slice. It is
not a replacement for the shipped Hermes WebUI yet and it is not enabled by
default.

## Qualified baselines

Hermes Agent repository: `edoumo/hermes-agent`

Durable Workers H2.1 baseline:

`bf336decb0ba298e85e5a34cf5b7a596f7dee2dc`

H2.1 real-runtime verdict:

`H2_1_B1_REQUALIFICATION_STATUS=PASS`

That backend demonstrated real worker creation, durable inbox idempotency,
actual DeepSeek activations, cold context continuity, worker serialization,
global activation capacity, task DAG writes, SSE invalidation, cooperative
drain, crash recovery, session isolation and process-metadata privacy.

Hermes WebUI qualified Harness code revision:

`e67722264f109e22f4f7fd2b29ec3898f69083c5`

H3 real-browser verdict:

`H3_B3_REQUALIFICATION_STATUS=PASS`

`H3_HARNESS_UI_FOUNDATION_STATUS=PASS`

The qualification proved same-session EventSource reuse, real SSE mutation
delivery, real activation auto-refresh, durable context continuity, native
Last-Event-ID reconnection semantics, session-switch stream ownership, bounded
DOM state and clean shutdown while leaving the principal runtime untouched.

The final real qualification report is recorded in:

`docs/architecture/hermes-harness-ui-foundation-final-report.md`

## WebUI source baseline

The `edoumo/hermes-webui` default branch was intentionally not used as the
foundation because it was an ancestor from 2026-06-17. The experimental branch
was created directly from current upstream `nesquena/hermes-webui`:

`1f5e45527ac8eb14230a9f5720664d0867e74035`

Branch:

`experimental/hermes-harness-ui-foundation`

The fork's `master` branch remains untouched.

Documentation commits may follow the qualified code revision on the
experimental branch. The code SHA above remains the authoritative H3 runtime
qualification point unless a later code change is explicitly requalified.

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

The B2 real qualification proved that the BFF SSE bridge relays small frames
line-by-line with immediate flush, rather than waiting for block-sized reads or
EOF.

## WebUI auth and CSRF

`harness_server.py` reuses the WebUI authentication implementation and the
existing `_check_csrf` policy for unsafe same-origin requests.

The browser obtains its session-bound CSRF value from the authenticated
same-origin endpoint:

`GET /api/harness/csrf`

The token is then attached to Harness POST requests as:

`X-Hermes-CSRF-Token`

This token is a WebUI anti-CSRF value. It is not the Hermes API Bearer.

The final real recipe proved authenticated `403` rejection without a CSRF token
and success with the valid session-bound token while preserving a separate
`hermes_harness_session` cookie namespace.

## Memory and EventSource posture

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
8. A selected session owns at most one EventSource.
9. Same-session refreshes reuse the existing EventSource.
10. Switching sessions closes the previous EventSource before opening the new
    session stream.
11. Native EventSource reconnection owns Last-Event-ID behavior; the client does
    not synthesize that header.
12. Duplicate event IDs and callbacks from stale streams are ignored.
13. Stale asynchronous session/worker loads cannot overwrite a newly selected
    scope.

The H3 B3 real recipe observed DOM nodes `283 -> 165 -> 283`, zero net growth,
empty transcript `localStorage`, one EventSource maximum, zero zombie streams
and zero SSE storm 429 responses.

The implementation therefore treats Hermes API as source of truth and the DOM
as a bounded projection, not as a second conversation database.

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

Laboratory recipe note: do not use `VAR=x env -u VAR <command>` when `VAR` is
intended to remain set for the child process. In particular, unsetting
`HERMES_WEBUI_PASSWORD` that way disables Harness auth and can produce false
CSRF-smoke failures. Verify the final child-process environment before
interpreting auth/CSRF results.

## Validation status

The final H3 code revision passed:

* `26/26` targeted tests
* Python compile checks
* JavaScript syntax check
* BFF SSE raw-stream qualification
* EventSource same-session reuse
* initial-frame no-loop behavior
* real mutation auto-refresh
* real activation auto-refresh to `SUCCEEDED`
* durable context continuity without marker reinjection
* native Last-Event-ID reconnection behavior
* A/B session stream ownership
* zero zombie streams
* zero SSE storm 429 responses
* bounded DOM projection
* auth, CSRF, secret-boundary and session-isolation smokes
* clean laboratory shutdown

The principal runtime, legacy WebUI and principal configuration were not
modified by the qualification.

The upstream WebUI GitHub Actions workflow runs only for pushes to `master` or
pull requests targeting `master`. No PR is authorized for this experimental
branch, so this H3 PASS is based on the targeted repository tests and the real
isolated integrated recipe, not a claimed upstream full-CI run.

## Gate result and next slice boundary

The original gate to the next UI slice is satisfied:

* Harness and legacy WebUI coexist without cookie/session interference: PASS
* browser never receives the Hermes API Bearer: PASS
* authenticated CSRF-protected worker creation: PASS
* real worker activation STARTING -> RUNNING -> SUCCEEDED through the BFF: PASS
* SSE refreshes the bounded UI after worker state changes: PASS
* session isolation remains fail-closed through the BFF: PASS
* browser projection remains bounded across repeated refresh/activation cycles: PASS
* stopping Harness leaves legacy WebUI and Hermes runtime untouched: PASS

The next slice may therefore build on H3 without reopening B2/B3 unless a
regression is observed. Public exposure, replacement of the legacy WebUI,
systemd deployment, merge to `master`, and broader destructive or steering
controls remain outside this qualification and require their own design and
gates.
