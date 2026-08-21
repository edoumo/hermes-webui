# Hermes Harness UI H4 operational controls

Status: `H4_HARNESS_UI_OPERATIONS_STATUS=PASS`

Branch: `experimental/hermes-harness-ui-operations`

Qualified H3 code baseline: `e67722264f109e22f4f7fd2b29ec3898f69083c5`

Qualified H4 Harness SHA: `decc07a86ef109f953e9f13433cd8990dff249ef`

Qualified H4 backend behavior SHA: `2e14c4f719a9c85bb79b9a44dc72a15cecfb1c39`

Backend plugin version: `0.3.2`

H4 backend branch: `experimental/durable-workers-operational-recovery`

## Purpose

H4 turns the H3 observability surface into a bounded operator control plane without moving lifecycle ownership into the browser.

The browser still talks only to the same-origin Harness BFF. The BFF still holds the Hermes API Bearer server side. SQLite and live subagent handles remain inaccessible to JavaScript.

## Added controls

### Operational summary

For the selected session, Harness displays:

* active Durable Worker activation count for that session;
* failed worker count;
* configured maximum concurrent Durable Worker activations.

The UI does not claim that the configured maximum minus the session count is globally available capacity because other sessions may legitimately consume the same process-wide limit.

### Cancel activation

The button is shown only when:

* selected worker is `RUNNING`;
* its current activation is `RUNNING` or already `CANCEL_REQUESTED`.

The transient durable `STARTING` state is intentionally not presented as cancellable in H4 UI. The capability-bearing live lifecycle handle is registered only after bind, so offering cancel earlier would create a short window where the backend must correctly reject the request as not locally supervised.

For `CANCEL_REQUESTED`, the button becomes disabled and reports that cancellation is already pending.

An operator cancel is acknowledged by HTTP 202 only after the backend has persisted cancellation intent and the lifecycle service has accepted interruption (or terminal reconciliation is already racing).

The browser never changes worker/message state optimistically. It waits for the normal H3 SSE invalidation and reloads bounded projections.

When the child is confirmed `CANCELLED`, backend H4 transitions the durable message back to `PENDING` and the worker to `DORMANT`. The operator can then use normal `Run next` to retry that same durable message.

### Retry failed

The button is shown only when the selected worker is `FAILED`.

Harness sends the worker's current `revision` as `expected_revision`. The backend performs a CAS and rejects stale recovery actions.

Retry preserves the failed activation as audit history, requeues its failed parent message and moves the worker back to `DORMANT`. A later normal run creates a new activation id.

## H3 preservation strategy

The H3 qualified files remain untouched on this H4 branch where practical:

* `api/harness_ui.py` remains the H3 BFF implementation;
* `static/harness.js` remains the H3/B3 session/SSE implementation;
* `static/harness.html` remains the H3 source shell.

H4 adds:

* `api/harness_ui_operations.py` as a route/asset extension;
* `static/harness-operations.js` as an operator-control layer.

`harness_server.py` imports the H4 extension instead of the foundation BFF.

When `/harness` is served, the H4 BFF injects `harness-operations.js` after `harness.js` loads but before the synthetic DOMContentLoaded boot event. This lets H4 wrap bounded projection functions without reimplementing H3 SSE ownership.

If the expected H3 bootstrap snippet is no longer present, the H4 shell fails loud instead of silently booting an incompatible extension.

## BFF allowlist additions

* `GET /api/harness/sessions/{session_id}/worker-operations`
* `POST /api/harness/sessions/{session_id}/workers/{worker_id}/retry`
* `POST /api/harness/sessions/{session_id}/workers/{worker_id}/activations/{activation_id}/cancel`

No DELETE, PUT, PATCH or arbitrary proxy path is introduced.

## Security invariants

H4 preserves H3 requirements:

* localhost-only Harness foundation;
* separate Harness auth cookie/state;
* WebUI auth and session-bound CSRF;
* Hermes API Bearer never present in browser assets/requests;
* session-scoped backend ownership;
* no direct SQLite access;
* no live lifecycle handle exposed;
* no second EventSource implementation;
* bounded worker/task/message/activation projections.

## Qualification result

`H4_HARNESS_UI_OPERATIONS_STATUS=PASS` as part of consolidated `H4_OPERATIONAL_RECOVERY_STATUS=PASS`.

The isolated real-runtime recipes established:

* operational summary displayed once per selected session with correct session-scoped active/failed/cap values;
* Cancel hidden in `STARTING`, visible in `RUNNING`, disabled as `Cancellation requested`, then removed after terminal cancellation;
* Retry visible only for a real `FAILED` worker and removed automatically after successful recovery;
* `Run next` disabled for invalid worker states and restored when the canonical projection returns `DORMANT`;
* UI transitions are driven by API/SSE state rather than optimistic browser-side mutations;
* operator cancellation, same-message rerun, fail-closed drain, retry CAS and post-retry success all completed against real Durable Worker state;
* one EventSource per selected session, no H4-created EventSource, no reconnect storm and no SSE 429 storm;
* bounded DOM with no duplicated controls or operational summary;
* cross-session isolation, CSRF and browser secret boundary remain intact;
* query-bearing H4 control calls remain rejected by the BFF while normal operations remain available;
* the principal runtime, legacy WebUI and principal configuration remained untouched and lab shutdown was clean.

The qualified Harness code SHA remains `decc07a86ef109f953e9f13433cd8990dff249ef`; no WebUI code changes were required by the backend B4/B4-final corrections.

No PR, merge or principal runtime mutation is authorized at this stage.
