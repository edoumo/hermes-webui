# Hermes Harness UI H6 final consolidation

Status: `H6_UI_CODE_IN_PROGRESS`

Branch: `experimental/hermes-harness-ui-final-consolidation`

H5 WebUI baseline: `76ed367cffed37f3abe3aaa881f6a6172c0d8c37`

Qualified H5 behavior baseline: `97f614f19cb71439028287ed87bc11679ebe76db`

## Purpose

H6 is a consolidation phase, not a new Harness feature. The H5 runtime/UI
behavior is already qualified. H6 locks that behavior into a final reviewable
surface and documents the invariants a public integration must preserve.

## Final layering

The browser load and delegation chain remains:

1. `harness.js` — H3 session state, worker projections and the single SSE owner;
2. `harness-operations.js` — H4 cancel/retry/operator summary;
3. `harness-tasks.js` — H5 session-level task DAG and dispatch controls;
4. `harness-task-recovery.js` — H5 task-aware failed recovery;
5. synthetic `DOMContentLoaded` boot.

The standalone server delegates through:

`harness_server.py -> api.harness_ui_task_recovery -> api.harness_ui_tasks -> api.harness_ui_operations -> api.harness_ui`

H6 does not add another BFF layer.

## Security boundary

The final Harness contract remains:

- localhost-only standalone listener;
- separate Harness auth cookie/state directory;
- WebUI authentication before Harness routing;
- CSRF on Harness POST controls;
- Hermes API Bearer only in the server-side BFF;
- no direct SQLite access from JavaScript;
- no lifecycle handle exposed to the browser;
- no transcript/task graph persistence in `localStorage`;
- one EventSource per selected session, owned only by H3;
- bounded task/message/activation projections.

## H6 changes

No Harness production JavaScript, BFF route or server behavior is intentionally
changed by H6.

H6 adds a final contract test that checks the complete H3-H5 surface together:

- one and only one browser `EventSource` constructor;
- no browser secret/header material;
- no localStorage persistence;
- localhost-only server guard still present;
- final BFF delegation chain still includes H4 operations;
- H5 route methods remain GET/POST only;
- every expected Harness asset is served by the standalone server.

See [AI-assisted development](ai-assisted-development.md) for the voluntary
transparency note used by this contribution.

## Qualification boundary

The final H6 lab should reuse the qualified H5 browser implementation and
perform a focused integration smoke after the backend schema adoption:

- login/auth/CSRF;
- session selection;
- worker list/detail;
- one normal run;
- one cancel/retry smoke;
- one task DAG dispatch/recovery smoke;
- one EventSource only;
- no reconnect storm or 429 SSE;
- bounded DOM after repeated updates;
- principal WebUI/runtime unchanged.

A complete replay of H3-H5 browser scenarios is unnecessary unless these
smokes reveal a regression.

No PR, merge or principal runtime mutation is authorized by this document.
