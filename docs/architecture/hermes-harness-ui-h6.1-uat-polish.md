# Hermes Harness UI H6.1 UAT polish

Status: `READY_FOR_TARGETED_QUALIFICATION`

Branch: `experimental/hermes-harness-ui-h6-uat-polish`

H6 behavior-qualified baseline: `9a6ea47e969489149c3964c6de8bdb9923acd3cc`

H6 post-qualification baseline used for this branch: `56eb9cc0a3ffbe8398f73b2e1fac1c5ead0727c9`

## Why this follow-up exists

The first human UAT deliberately happened before any upstream PR. The backend
and Harness execution paths were technically green, but the user found the
interface not yet suitable for public review. H6.1 addresses those concrete UX
findings without changing the qualified worker/task execution architecture.

## UAT findings addressed

### Direct LAN access

Loopback remains the default. A non-loopback Harness listener is now possible
only with both:

- `HERMES_HARNESS_ALLOW_REMOTE=1`;
- a non-empty `HERMES_WEBUI_PASSWORD`.

The canonical Hermes API remains loopback-only behind the server-side BFF. The
example configuration recommends a specific LAN address, not a public bind.

### Localisation

Harness now exposes EN/FR selection and defaults to French when the browser
locale starts with `fr`. Static UI and the H4/H5 operator/task controls use the
same small translation layer.

### Theme

Dark remains the default. A header toggle switches between dark and light
palettes. The setting is a browser UI preference only.

### Desktop rail ergonomics

The Sessions and Workers rails can now:

- be resized by pointer drag;
- be collapsed/expanded independently;
- restore their previous width in the same browser.

The existing stacked mobile layout remains the small-screen fallback.

### Task graph clipping

The H5 DAG no longer uses `min-width:max-content`. One stage consumes the
available workspace width; horizontal scrolling is introduced only when the
number of stages genuinely needs more space. Nodes and controls explicitly use
`min-width:0` to avoid the clipping seen in the first UAT.

### Model discoverability

Worker creation keeps a model field backed by the advertised `/v1/models`
catalog when available. Existing workers now have a visible **Worker settings**
action for changing label, model and toolsets. The selected model remains a
property of the durable worker, so every task dispatched to that worker uses
that worker configuration.

### Worker lifecycle

Workers are archived/restored rather than hard-deleted. Archive is a backend
`DORMANT -> DISABLED` transition that preserves durable history. Archived
workers are hidden by default but can be shown and restored.

### Session lifecycle

Harness does not hard-delete Hermes sessions from this UX. A session can be
archived from the Harness list, which is intentionally a browser-local hide/
restore preference. Deleting the upstream session while leaving Durable Worker
rows keyed by that session would create an orphaning policy question that
should not be solved casually as UI polish.

## Browser persistence boundary

H6 originally prohibited browser persistence of durable data. H6.1 preserves
that security property while allowing harmless UI preferences in localStorage.
Only keys under `hermesHarness.ui.*` are used for:

- locale;
- theme;
- rail widths/collapse states;
- archived-session display preferences;
- whether archived workers/sessions are visible.

No transcript, message, activation, task graph, API key, bearer token or CSRF
token is persisted there.

## Targeted qualification boundary

Before a second human UAT, the lab must prove:

- all prior Harness tests plus the new UAT-regression tests pass;
- JS syntax checks pass for all five Harness browser scripts;
- EN/FR and dark/light switching work live;
- rail drag/collapse works at desktop width;
- a one-stage task shows its full controls without artificial horizontal
  clipping;
- an existing worker model can be changed and that model is used on the next
  real activation;
- worker archive/restore is reversible and history-preserving;
- direct LAN access works with password auth and fails closed without the two
  explicit remote-bind guards;
- one EventSource ownership, CSRF, session isolation and browser secret
  boundaries remain green;
- the principal Hermes/WebUI runtime remains untouched.

A second human UAT is mandatory after this targeted qualification. No PR should
be opened until that UAT is accepted by the project owner.
