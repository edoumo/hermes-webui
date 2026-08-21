# Hermes Harness UI H6 final consolidation

Status: `H6_UI_CLEAN_CANDIDATE_READY_FOR_QUALIFICATION`

Clean candidate branch: `experimental/hermes-harness-ui-h6-upstream-master`

Upstream base: `nesquena/hermes-webui@cfcc39194a4cfbb6c78fe8114695a70737e17bbf`

Qualified H5 behavior baseline: `97f614f19cb71439028287ed87bc11679ebe76db`

## Purpose

H6 is a consolidation phase, not a new Harness feature. The H5 runtime/UI behavior is already qualified. H6 locks that behavior into a clean, reviewable candidate rebuilt directly on the current upstream WebUI base.

The clean candidate is additive: it does not overwrite existing upstream production files. Historical qualification-candidate notes are deliberately omitted from the public-facing diff.

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

No Harness production JavaScript, BFF route, or server behavior is intentionally changed by H6 relative to the H5 implementation. The clean candidate carries the existing H3-H5 Harness runtime, final architecture documentation and consolidated tests onto the current upstream base.

The H6 final-contract coverage verifies the complete surface together, including one browser EventSource owner, no browser secret/header material, no localStorage persistence, localhost listener guards, the full BFF delegation chain, GET/POST-only task controls, and expected Harness assets.

See [AI-assisted development](ai-assisted-development.md) for the scoped `🤖 AI-assisted development` transparency note. It applies to this Harness contribution, not unrelated upstream Hermes WebUI code.

## Qualification boundary

The clean candidate is ready for isolated H6 qualification, not yet declared PASS. The final lab must pair it with the clean upstream-based Hermes Agent H6 candidate and perform focused integration smokes for:

- login/auth/CSRF;
- session selection and worker projections;
- normal Durable Worker execution;
- cancel/retry/recovery;
- task DAG dispatch/recovery;
- one EventSource only with no reconnect storm or SSE 429;
- bounded DOM/state and no browser secrets;
- principal WebUI/runtime unchanged.

A complete replay of every H3-H5 browser scenario is unnecessary unless a smoke reveals regression.

No PR, merge, master update, or principal runtime mutation is authorized by this document.
