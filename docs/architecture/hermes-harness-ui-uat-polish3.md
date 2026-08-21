# Hermes Harness UI — H6.3 human-UAT polish

Status: `READY_FOR_QUALIFICATION`

H6.3 is a narrow WebUI-only response to the third human UAT. It preserves the
qualified H6.1 backend and all Durable Workers runtime contracts.

## Human-UAT findings addressed

1. **Model picker showed no catalog models.** `/api/model/options` can expose a
   rich provider list while leaving root `provider` and `model` empty. H6.3
   resolves the provider deterministically from, in order: an explicit root
   provider, a unique provider containing a runtime/worker model hint, a unique
   provider marked active/current/selected, or a unique authenticated/configured
   provider. If none is unambiguous, the picker remains fail-closed rather than
   mixing models from unrelated credential sets.
2. **Language selector lacked visual flags.** The six shipped locales are
   decorated with compact Unicode country flags while the locale catalog remains
   data-driven and extensible.
3. **Single-stage task DAG could remain horizontally clipped.** A single-stage
   graph is forced into one fluid column, horizontal overflow is disabled and
   `scrollLeft` is reset after graph/layout changes. Multi-stage DAGs retain the
   existing horizontal layout and scrolling behavior.

## Security and ownership

H6.3 remains browser-only. It adds no API route, no listener, no EventSource,
no browser credential, and no localStorage surface. Model discovery continues to
use the server-authenticated Harness BFF endpoint introduced in H6.2.

## Qualification boundary

Qualification should be targeted to the WebUI delta: Python/JS syntax, Harness
regression tests, browser verification of the model list and locale flags, a
single-stage DAG without horizontal clipping, and a quick multi-stage DAG
non-regression check. The H6.1 backend does not need requalification.
