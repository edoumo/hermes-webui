# Hermes Harness H6.2 — second human-UAT polish

Status: `READY_FOR_QUALIFICATION`

H6.2 responds to the second human UAT before any upstream pull request. It is a
WebUI/BFF-only delta on top of the qualified H6.1 candidate; Hermes Agent and the
Durable Workers schema/runtime remain unchanged.

## User-visible changes

### Explicit model picker

H6.1 used an HTML `input` + `datalist`, which browsers can render like an
ordinary text field and therefore did not make model discovery obvious. H6.2
uses a real `select` in both worker creation and worker settings.

The Harness BFF exposes the stock Hermes `/api/model/options` endpoint as
`GET /api/harness/model-options`. The browser never receives the Hermes bearer
credential. The picker intentionally lists models from the **active provider**
only: a Durable Worker currently persists a model id, not an independent
provider credential set, so showing unrelated-provider models would advertise a
routing capability the runtime does not safely provide.

`Gateway default` and `Custom model…` remain explicit escape hatches.

### Optional session name

Creating a session now opens a dialog with an optional human title. When the
field is non-empty Harness sends `{ "title": "…" }` to the existing Hermes
session-create endpoint. When blank it sends `{}` and Hermes keeps its normal
automatic session identity/title behavior.

No session ID generation is moved into the browser.

### Extensible locales

Translations are moved from the preferences controller into the data-only
`static/harness-locales.js` catalog. Initial entries are:

- English
- Français
- Español
- Português
- Deutsch
- Italiano

English is the canonical per-key fallback, which makes a new community locale
safe to contribute incrementally. Locale discovery uses `navigator.languages`
and falls back to English when no browser language is supported.

The locale catalog has no auth, CSRF, transcript, task, activation or storage
responsibility. UI preference persistence remains exclusively in
`harness-preferences.js` under `hermesHarness.ui.*`.

## Security / architecture invariants

H6.2 adds no EventSource owner, no scheduler, no Durable Worker route, no SQLite
schema change and no destructive session/worker operation. The new model catalog
route is GET-only and uses the existing server-side authenticated Harness BFF.

## Qualification boundary

Qualify this WebUI HEAD against the unchanged H6.1 backend. Required checks are:
model inventory rendering, optional/automatic session naming, six-locale switch
and persistence, existing H6.1 regression suite, one-EventSource ownership,
secret boundary, LAN-auth behavior and a smoke worker creation/dispatch.

No upstream PR is authorized until the subsequent human UAT is accepted.
