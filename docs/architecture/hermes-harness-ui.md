# Hermes Harness UI architecture

Status: experimental integration candidate

## Purpose

Hermes Harness UI is a standalone localhost operator surface for Hermes Durable
Workers. It does not run Hermes Agent itself and it does not own worker
lifecycle state.

The architecture is deliberately thin:

```text
Browser
  -> Harness WebUI auth + CSRF
  -> same-origin Harness BFF
  -> authenticated Hermes API
  -> Durable Workers / task orchestration
  -> existing Hermes subagent lifecycle
```

The browser receives bounded projections and operator controls. Durable state,
API credentials and live lifecycle handles remain server-side.

## Process isolation

Harness runs as a separate localhost WebUI process with its own:

- listener;
- auth cookie name;
- session/auth state directory.

This prevents the Harness process from sharing or concurrently rewriting the
principal Hermes WebUI session state.

The standalone server refuses non-loopback hosts. H6 intentionally does not
turn Harness into a public network service.

## Layering

The final browser stack is additive:

1. `harness.js` — H3 foundation;
2. `harness-operations.js` — H4 operational controls;
3. `harness-tasks.js` — H5 task DAG;
4. `harness-task-recovery.js` — H5 failed-task recovery.

The H3 layer owns:

- selected session/worker state;
- bounded data loading;
- the only browser `EventSource`;
- SSE reconnect/backoff behavior;
- refresh scheduling.

H4 wraps the H3 projection hooks to add:

- operational counts;
- failed-worker retry;
- live activation cancellation.

H5 wraps the existing session refresh path to add:

- session-level task graph;
- task editing and assignment;
- dependency add/remove;
- READY dispatch;
- task-aware recovery.

No later layer creates another session state store or another SSE connection.

## Server-side BFF

The BFF chain is:

```text
api.harness_ui_task_recovery
    -> api.harness_ui_tasks
        -> api.harness_ui_operations
            -> api.harness_ui
```

Each layer adds a small exact allowlist and delegates everything else to the
previous qualified layer.

The BFF holds the Hermes API Bearer server-side. JavaScript never receives or
constructs that credential.

## Authentication and CSRF

Harness reuses Hermes WebUI authentication/hardening while keeping independent
Harness auth state.

Requests are authenticated before Harness routing. POST controls require the
session-bound WebUI CSRF token before the BFF proxies them to Hermes API.

The browser does not persist transcripts, graphs or credentials in
`localStorage`.

## SSE ownership

Exactly one `EventSource` is created for the selected session, in
`harness.js`.

The stream is an invalidation feed, not a second state database. On a durable
change event, Harness schedules bounded API projection reloads. H4/H5 controls
therefore do not invent optimistic durable states.

For example, after operator cancellation the UI can show that cancellation was
requested, but worker/message/task terminal state is taken from subsequent API
projections after the real child lifecycle reaches terminal state.

## Durable Worker controls

Worker detail exposes bounded message and activation history plus normal queue
and run controls.

H4 adds:

- `Retry failed` only for a `FAILED` worker;
- `Cancel activation` only when a `RUNNING` worker has a cancellable live
  activation;
- disabled `Cancellation requested` while durable `CANCEL_REQUESTED` is
  pending.

The UI intentionally does not offer cancellation during the short `STARTING`
window because the backend may not yet possess the capability-bearing live
lifecycle handle.

## Task DAG

The graph is session-level rather than worker-level, so the operator can see
work dependencies without first selecting a worker.

The browser renders backend-projected tasks in topological stages with a light
SVG edge overlay. No graph framework or build dependency is required.

The backend remains authoritative for `ready`. The browser uses projected
`ready`, task status, task revision and worker state only to gate obvious
operator actions.

Task cards support:

- edit subject/description while pending;
- assign/unassign a session worker;
- add/remove dependencies;
- dispatch a READY task;
- recover a failed task.

All mutating H5 operations include `expected_revision` where required.

## Bounded UI

Harness deliberately avoids unbounded transcript/graph rendering.

Current projections use bounded worker history and a maximum 100-node public
task graph. Repeated SSE refreshes replace existing render state rather than
append duplicate controls or cards.

This keeps both DOM growth and BFF response sizes bounded during long-lived
operator sessions.

## Security boundary

Browser assets contain no:

- `Authorization` header construction;
- Bearer token;
- API server key;
- direct SQLite access;
- lifecycle handle or process ownership metadata.

The principal Hermes WebUI `server.py` is not modified to become the Harness
server. `harness_server.py` is a separate opt-in entrypoint.

## Qualification

H3, H4 and H5 were qualified against the real Hermes API/runtime, including:

- authentication and CSRF;
- one EventSource per session;
- Last-Event-ID/reconnect behavior;
- real worker cancel/retry transitions;
- real task DAG dispatch and automatic unblock;
- fail-closed recovery;
- crash/restart continuity;
- session isolation;
- bounded DOM and secret-boundary checks.

H6 adds no intentional Harness runtime behavior. Its final contract tests lock
the complete H3-H5 layering/security surface while the final integration lab
rechecks a focused set of real browser/runtime smokes on the current upstream
base.

## AI assistance disclosure

See [🤖 AI-assisted development](ai-assisted-development.md). The Harness
contribution was developed with substantial AI assistance under human
direction, review and real-runtime qualification. This statement applies to the
Harness/Durable Workers contribution only, not unrelated upstream Hermes WebUI
code.
