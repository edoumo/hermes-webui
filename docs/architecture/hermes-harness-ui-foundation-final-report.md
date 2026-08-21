# Hermes Harness UI foundation — final real qualification report

Status: `H3_HARNESS_UI_FOUNDATION_STATUS=PASS`

Date: 2026-08-21

## Qualified revisions

Hermes WebUI repository: `edoumo/hermes-webui`

Qualified Harness revision:

`e67722264f109e22f4f7fd2b29ec3898f69083c5`

Harness branch:

`experimental/hermes-harness-ui-foundation`

Hermes Agent H2.1 backend revision:

`bf336decb0ba298e85e5a34cf5b7a596f7dee2dc`

## Final verdict

```text
H3_B3_REQUALIFICATION_STATUS=PASS
H3_HARNESS_UI_FOUNDATION_STATUS=PASS
WEBUI_HEAD=e67722264f109e22f4f7fd2b29ec3898f69083c5
BACKEND_H21_HEAD=bf336decb0ba298e85e5a34cf5b7a596f7dee2dc
CODE_TESTS=PASS (26/26 in 1.39s, including 2 EventSource ownership tests)
PY_COMPILE=PASS
JS_CHECK=PASS
SAME_SESSION_REUSE=PASS
INITIAL_FRAME_NO_RECONNECT_CYCLE=PASS
SSE_MUTATION_AUTO_REFRESH=PASS
ACTIVATION_AUTO_REFRESH=PASS
CONTEXT_CONTINUITY=PASS
NATIVE_LAST_EVENT_ID=PASS
SESSION_SWITCH_OWNERSHIP=PASS
ZOMBIE_FLOWS=0
SSE_STORM_429=0
DOM_BOUNDED=PASS
CSRF_SMOKE=PASS
SESSION_ISOLATION_SMOKE=PASS
MAIN_RUNTIME_TOUCHED=NO
MAIN_CONFIG_TOUCHED=NO
LEGACY_WEBUI_TOUCHED=NO
CLEAN_SHUTDOWN=PASS
BUGS_NEW=0
GITHUB_TOUCHED=NO
```

Authentication and secret-boundary checks also passed. The laboratory used the isolated `hermes_harness_session` cookie, and the Hermes API Bearer was not exposed to browser assets, JSON responses, or SSE.

## B2 and B3 closure

The original integrated H3 recipe found two defects, both now closed.

### B2 — SSE bridge buffering

The first BFF SSE implementation used buffered block reads. Small SSE frames were not forwarded promptly to the browser.

The BFF now relays upstream SSE line-by-line and flushes each relayed line. The real recipe proved:

* raw SSE connection remained open;
* initial event arrived without waiting for EOF;
* keepalive arrived;
* `Last-Event-ID` was accepted and forwarded;
* no server-side SSE relay bug remained.

### B3 — EventSource recreation loop

The initial client called `startEvents()` after every projection refresh. The first SSE frame triggered a refresh, which recreated EventSource, which produced another first frame, creating a reconnect storm.

The client now assigns EventSource ownership to the selected session:

* refreshes of the same session reuse the existing EventSource;
* switching sessions closes the old source and creates exactly one new source;
* callbacks from stale sources are ignored;
* duplicate event IDs are ignored;
* stale asynchronous projection responses cannot overwrite a newly selected session or worker;
* network reconnect remains delegated to native EventSource semantics, including native `Last-Event-ID` behavior.

The real B3 requalification proved one stream per selected session, no reconnect loop, no zombie flows and no SSE capacity 429 caused by the browser.

## Real functional proof

A real Harness browser session against the isolated H2.1 backend demonstrated:

* same-session EventSource reuse across six refreshes with zero recreation;
* initial SSE frame causing one bounded refresh and then remaining stable;
* an external worker mutation appearing in the UI without manual Refresh;
* two real DeepSeek activations reaching `SUCCEEDED` automatically in the UI;
* real subagents `sa-0-2aca8158` and `sa-0-2bfe037b`;
* durable context continuity with marker `B3-REVAL-AUTO-REFRESH-OK` recalled exactly on the second message without reinjection;
* native `Last-Event-ID` reconnect behavior end-to-end through browser, BFF and backend;
* A -> B -> A -> B session switches with exactly one owned stream after each switch;
* zero zombie flows;
* zero SSE storm 429 responses.

## Browser boundedness

Observed DOM nodes moved `283 -> 165 -> 283` across navigation/refresh cycles, yielding no net growth. `localStorage` remained empty for transcript state and at most one EventSource was active.

This validates the intended architecture: the browser holds bounded projections while Hermes API remains the source of truth.

## Security and isolation proof

The final recipe confirmed:

* Harness listener remained loopback-only;
* Harness auth state remained separate from legacy WebUI auth state;
* authenticated CSRF behavior was `403` without token and successful with a valid session-bound token;
* the Hermes API Bearer remained server-side only;
* cross-session worker access returned `404`;
* legacy WebUI and principal Hermes runtime were not modified;
* principal configuration SHA remained unchanged (`547ff75a...` in the evidence report).

## Environment recipe pitfall

A laboratory-only shell pitfall was discovered and must not be misclassified as a product auth/CSRF failure:

```bash
VAR=x env -u VAR <command>
```

When used with `HERMES_WEBUI_PASSWORD`, the `env -u` operation removes the variable for the launched process even if the shell prefix appeared to assign it. The resulting Harness process has authentication disabled, which makes authenticated CSRF smoke expectations misleading.

Recipe rule: construct the final environment explicitly and verify the launched process sees the intended auth setting before interpreting CSRF results.

This is an operator recipe issue, not a Harness product defect.

## Evidence

Evidence directory:

`/home/edou/lab/hermes-harness-ui-foundation/evidence-h3-b3-fix/`

Archive:

`/home/edou/lab/hermes-harness-ui-foundation/evidence-h3-b3-fix/h3-b3-fix-evidence.tar.gz`

Archive SHA256:

`a09cf985a924666b56bcbeea793aa65fa6547a063e0d77872449732a43e83f54`

The evidence archive was checked for accidental secret disclosure by the qualification operator.

## Qualification boundary

This PASS qualifies the experimental Harness foundation architecture and its current vertical slice. It does not authorize:

* public exposure;
* merge to `master`;
* replacement of the legacy WebUI;
* systemd deployment;
* network, DNS, proxy or firewall changes;
* broader write/delete/steering controls not implemented by the current H2.1 API.

The next development slice may build on this H3 baseline without reopening B2 or B3 unless a regression is observed.
