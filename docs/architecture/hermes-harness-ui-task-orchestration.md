# Hermes Harness UI H5 Task Orchestration

Status: `H5_TASK_ORCHESTRATION_STATUS=PASS`

Branch: `experimental/hermes-harness-ui-task-orchestration`

Qualified Harness behavior SHA: `97f614f19cb71439028287ed87bc11679ebe76db`

H4 baseline: `601f6049f1d81d2e536e341f2e9fb1e7726ed09d`

Qualified backend SHA: `ea254053c82929fc44646b6cb4c8456498d5deb4`

## Purpose

H5 makes the Durable Worker task DAG operational in Harness while preserving the thin-client architecture:

Browser -> Harness BFF -> canonical Hermes API -> Durable Task Orchestration -> qualified Durable Worker lifecycle.

H3 still owns session state, bounded worker projections, SSE refresh scheduling and the single EventSource per selected session. H4 still owns operator cancel/retry. H5 adds only the task graph, task controls and task-aware recovery.

## BFF surface

H5 exposes same-origin allowlisted routes for:

- session task graph;
- task edit/reassignment;
- dependency add/remove;
- task dispatch;
- failed-task recovery.

POST controls reject query parameters. The Hermes API Bearer remains server-side.

## Browser layering

The Harness shell loads the qualified layers in order:

1. `harness.js` (H3/B3);
2. `harness-operations.js` (H4);
3. `harness-tasks.js` (H5 DAG/actions);
4. `harness-task-recovery.js` (H5 recovery extension);
5. synthetic `DOMContentLoaded` boot.

H5 does not create a second EventSource or duplicate H3 session ownership.

## DAG rendering

The graph is session-level and remains visible without selecting a worker.

The browser renders bounded topological stages and SVG dependency links without introducing a graph framework or build dependency.

Readiness is displayed from the backend projection. The browser does not invent READY/BLOCKED state.

## Task controls

Pending tasks support:

- subject/description edit;
- worker assignment/unassignment/reassignment;
- dependency add/remove;
- dispatch when backend `ready=true` and assigned worker is projected DORMANT.

Failed tasks expose `Recover task`, which invokes the H5 task-aware recovery route rather than generic client-side state manipulation.

The browser never sets `in_progress`, `completed`, `failed` or `pending` optimistically. Durable state transitions arrive through canonical API projections and H3 SSE invalidation.

## Real qualification

Integrated lab qualification completed with `H5_TASK_ORCHESTRATION_STATUS=PASS`.

The real browser/runtime recipe proved:

- session-scoped DAG and exact dependency edges;
- CAS-safe edit/reassignment;
- cycle rejection;
- READY/BLOCKED enforcement;
- real DeepSeek task dispatch and completion;
- automatic dependent unblocking;
- H4 operator cancel while task remains locked `in_progress` until terminal CANCELLED;
- same-message/new-activation redispatch after cancellation;
- fail-closed task failure on system drain;
- `Recover task` restoration and successful redispatch;
- crash/restart projection from H1 ABANDONED back to pending task state;
- worker inbox ordering;
- shared activation capacity;
- session isolation and CSRF;
- one EventSource per session;
- bounded DOM and no localStorage graph/transcript state;
- unchanged principal runtime and legacy WebUI.

JavaScript syntax checks passed on all H3/H4/H5 assets.

## Test-hygiene note

The H5 qualification reported one obsolete H4 static test. That test asserted that `harness_server.py` directly imports `api.harness_ui_operations`, which was true in H4 but is intentionally no longer the physical import topology in H5.

H5 imports the final `api.harness_ui_task_recovery` layer, which delegates through the H5 task layer to H4 operations. Runtime import/delegation was validated in the real recipe.

The test has been updated after qualification to assert the preserved delegation contract instead of the obsolete literal import. That post-qualification change is test-only; qualified H5 behavior remains anchored at SHA `97f614f19cb71439028287ed87bc11679ebe76db`.

## Security invariants

H5 browser code contains no:

- Authorization header;
- Bearer credential;
- API server key;
- direct SQLite access;
- live lifecycle handle;
- second EventSource;
- task graph or transcript storage in localStorage.

Harness auth, cookie isolation and CSRF remain inherited.

## Qualification evidence

Evidence directory:

`/home/edou/lab/hermes-durable-workers-h5/evidence-h5/`

Archive:

`/home/edou/lab/hermes-durable-workers-h5/evidence-h5/h5-task-orchestration-evidence.tar.gz`

SHA256:

`48706e3b31f39f4ae7fca88233ab787f7c1f7659d2b96b89438aef59537f0e43`

No PR, merge, main/master update or principal runtime deployment is authorized by this qualification.
