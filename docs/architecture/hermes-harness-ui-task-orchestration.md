# Hermes Harness UI H5 task orchestration

Status: `H5_UI_CODE_IN_PROGRESS`

Branch: `experimental/hermes-harness-ui-task-orchestration`

H4 baseline: `601f6049f1d81d2e536e341f2e9fb1e7726ed09d`

Backend H5 branch: `experimental/durable-workers-task-orchestration`

## Purpose

H5 makes the existing Durable Worker task DAG operational in Harness without moving orchestration state into the browser.

Layering remains:

Browser -> Harness BFF -> canonical Hermes API -> Durable Worker task orchestration -> existing H1/H4 lifecycle.

H3 continues to own:

- session state;
- worker projections;
- the single EventSource per selected session;
- SSE refresh scheduling.

H4 continues to own:

- operational summary;
- worker cancel;
- worker retry.

H5 owns only:

- DAG graph projection;
- pending task edit/reassignment;
- dependency add/remove;
- READY task dispatch;
- task-focused rendering.

## BFF additions

- `GET /api/harness/sessions/{session_id}/worker-task-graph`
- `POST /api/harness/sessions/{session_id}/worker-tasks/{task_id}/edit`
- `POST /api/harness/sessions/{session_id}/worker-tasks/{task_id}/dependencies/add`
- `POST /api/harness/sessions/{session_id}/worker-tasks/{task_id}/dependencies/remove`
- `POST /api/harness/sessions/{session_id}/worker-tasks/{task_id}/dispatch`

POST controls reject query parameters.

The Hermes API Bearer remains server-side.

## Browser load order

The H5 shell loads classic scripts in this order:

1. `harness.js` (qualified H3/B3);
2. `harness-operations.js` (qualified H4);
3. `harness-tasks.js` (H5);
4. synthetic `DOMContentLoaded` boot.

H5 therefore wraps the already-qualified H4 `loadSessionData()` function rather than replacing session/SSE ownership.

## DAG rendering

No graph framework or build dependency is introduced.

The browser computes topological levels from the bounded graph projection and renders task nodes in horizontal stages. An SVG overlay draws dependency edges between the rendered nodes.

The graph is bounded to 100 tasks.

The browser does not infer readiness. It displays the backend-projected `task.ready` value and uses it to gate dispatch controls.

## Task controls

Pending task cards support:

- assignment/unassignment to an existing session worker;
- subject/description edit;
- dependency add/remove;
- dispatch when `ready` and the assigned worker is projected `DORMANT`.

Failed/cancelled cards expose a reset-to-pending action through the existing task status route.

Task dispatch sends only `expected_revision`; the backend creates the durable message and activation atomically.

The browser never sets `in_progress`, `completed` or `failed` optimistically. Those states arrive through the canonical API projection and H3 SSE invalidation path.

## Security and isolation invariants

H5 browser code contains no:

- `Authorization` header;
- Bearer value;
- API server key;
- direct SQLite access;
- lifecycle handle;
- second EventSource;
- transcript in localStorage.

Existing Harness auth, cookie isolation and CSRF remain inherited.

## Tests added

`tests/test_harness_ui_task_orchestration.py`

It locks:

- exact H5 BFF allowlist;
- H3 -> H4 -> H5 load order;
- server isolation from legacy WebUI;
- bounded graph projection usage;
- state/revision-driven controls;
- no Bearer/EventSource/localStorage regression.

## Qualification boundary

No real H5 runtime/browser recipe has run yet.

The lab must validate real graph updates, dependency editing, READY gating, real task dispatch, automatic completion/failure/cancel projection, one EventSource ownership, bounded DOM and unchanged H4 controls before H5 is marked PASS.

No PR, merge or principal runtime mutation is authorized.
