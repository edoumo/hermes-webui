# Hermes Harness UI H6 final consolidation

Status: `H6_UI_FINAL_QUALIFICATION=PASS`

Clean candidate branch: `experimental/hermes-harness-ui-h6-upstream-master`

Upstream base: `nesquena/hermes-webui@cfcc39194a4cfbb6c78fe8114695a70737e17bbf`

Behavior-qualified H6 SHA: `9a6ea47e969489149c3964c6de8bdb9923acd3cc`

Paired backend behavior-qualified H6 SHA: `011fe3c2fb20385c97ecad450ded02d0982ae3db`

Backend test-hygiene HEAD: `07dd7bd93411f3486e405b55dda48892395ea637`

## Purpose

H6 is a consolidation phase, not a new Harness feature. The H5 runtime/UI behavior was already qualified; H6 proves that behavior on a clean candidate rebuilt directly on the recorded upstream WebUI base.

The clean candidate is additive and does not overwrite existing upstream production files. Historical qualification-candidate notes are deliberately omitted from the public-facing diff.

## Final layering

The browser load and delegation chain remains:

1. `harness.js` — H3 session state, worker projections and the single SSE owner;
2. `harness-operations.js` — H4 cancel/retry/operator summary;
3. `harness-tasks.js` — H5 session-level task DAG and dispatch controls;
4. `harness-task-recovery.js` — H5 task-aware failed recovery;
5. synthetic `DOMContentLoaded` boot.

The standalone server delegates through:

`harness_server.py -> api.harness_ui_task_recovery -> api.harness_ui_tasks -> api.harness_ui_operations -> api.harness_ui`

H6 adds no additional BFF layer.

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

## H6 behavior

No Harness production JavaScript, BFF route, or server behavior was intentionally changed by H6 relative to H5. The clean candidate carries the qualified H3-H5 Harness runtime, final architecture documentation and consolidated tests onto the recorded upstream base.

The final H6 campaign proved:

- WebUI Python compilation PASS;
- Harness suite `36/36 PASS`;
- JavaScript syntax checks PASS;
- real Durable Worker dispatch through Harness PASS;
- task DAG READY/BLOCKED projection and backend-driven unblock PASS;
- operator cancel and same-message/new-activation redispatch PASS;
- task fail-closed recovery and redispatch PASS;
- crash/restart reconciliation PASS;
- authentication, CSRF and cross-session isolation PASS;
- browser secret boundary PASS;
- exactly one EventSource owner per session, no reconnect storm and no SSE 429 PASS;
- bounded DOM after repeated transitions PASS;
- principal WebUI/runtime/configuration untouched.

The paired backend real-runtime evidence archive SHA-256 is:

`e7ac0b0d89e13055b94046d7aefed9123e1b40a9454bdd8a56300120a7755d91`

See [AI-assisted development](ai-assisted-development.md) for the scoped `🤖 AI-assisted development` transparency note. It applies to this Harness contribution, not unrelated upstream Hermes WebUI code.

## User acceptance gate

Technical qualification is complete, but upstream PR preparation is deliberately gated on a hands-on maintainer acceptance pass. The maintainer should use the Harness as an operator rather than merely replaying the automated test matrix, and report any usability, workflow, terminology or visual issues before PR preparation.

No PR, merge, master update, or principal runtime mutation is authorized without explicit maintainer approval.
