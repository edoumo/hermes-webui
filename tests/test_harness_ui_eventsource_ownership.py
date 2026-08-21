"""Regression gates for the Harness EventSource ownership contract (B3)."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "harness.js"


def test_refresh_does_not_recreate_eventsource_for_same_session():
    source = JS.read_text(encoding="utf-8")

    assert "eventsSessionId: null" in source
    assert "eventLastId: null" in source
    assert "if (state.events && state.eventsSessionId === sid) return;" in source
    assert "state.eventsSessionId = sid;" in source
    assert "if (eventId && eventId === state.eventLastId) return;" in source
    assert "scheduleRefresh(sid);" in source
    assert "if (sid && state.sessionId === sid) loadSessionData();" in source
    assert "window.addEventListener(\"beforeunload\", closeEvents);" in source

    start = source.index("function startEvents()")
    end = source.index("function scheduleRefresh", start)
    start_events = source[start:end]
    # startEvents may close a stream only when there is no session or the
    # selected session changes. It must not unconditionally close/recreate it.
    assert "if (state.events && state.eventsSessionId === sid) return;" in start_events


def test_eventsource_runtime_keeps_one_owner_and_closes_on_session_switch():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")

    script = r'''
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync(__HARNESS_JS__, "utf8");

class FakeEventSource {
  static instances = [];
  constructor(url, options) {
    this.url = url;
    this.options = options;
    this.closed = false;
    this.listeners = new Map();
    FakeEventSource.instances.push(this);
  }
  addEventListener(name, callback) {
    const list = this.listeners.get(name) || [];
    list.push(callback);
    this.listeners.set(name, list);
  }
  emit(name, event = {}) {
    for (const callback of this.listeners.get(name) || []) callback(event);
  }
  close() { this.closed = true; }
}

const domNodes = new Map();
function domNode(id) {
  if (!domNodes.has(id)) {
    domNodes.set(id, {
      textContent: "",
      classList: { add() {}, remove() {} },
    });
  }
  return domNodes.get(id);
}

let nextTimer = 1;
const timers = new Map();
const context = {
  window: { __HERMES_HARNESS__: {}, addEventListener() {} },
  document: { getElementById: domNode, addEventListener() {} },
  EventSource: FakeEventSource,
  setTimeout(callback) {
    const id = nextTimer++;
    timers.set(id, callback);
    return id;
  },
  clearTimeout(id) { timers.delete(id); },
  console,
};
vm.createContext(context);
vm.runInContext(source, context);

// Replace the network projection with the smallest realistic consequence of a
// durable event: a projection refresh calls startEvents() again.
vm.runInContext("loadSessionData = async function () { startEvents(); };", context);

vm.runInContext('state.sessionId = "session_A"; startEvents(); startEvents();', context);
if (FakeEventSource.instances.length !== 1) {
  throw new Error(`same-session start created ${FakeEventSource.instances.length} EventSources`);
}
const sourceA = FakeEventSource.instances[0];
if (sourceA.closed) throw new Error("session A source was closed unexpectedly");

// The initial/current event may trigger one projection refresh, but that
// refresh must reuse the exact same EventSource.
sourceA.emit("durable_workers.changed", { lastEventId: "token-A" });
for (const callback of Array.from(timers.values())) callback();
timers.clear();
if (FakeEventSource.instances.length !== 1 || sourceA.closed) {
  throw new Error("event-driven refresh recreated the same-session EventSource");
}

// Duplicate delivery of the same event id must not schedule another refresh.
sourceA.emit("durable_workers.changed", { lastEventId: "token-A" });
if (timers.size !== 0) throw new Error("duplicate event id scheduled a refresh");

// Switching session transfers ownership exactly once and closes the old source.
vm.runInContext('state.sessionId = "session_B"; startEvents();', context);
if (FakeEventSource.instances.length !== 2) {
  throw new Error("session switch did not create exactly one replacement EventSource");
}
const sourceB = FakeEventSource.instances[1];
if (!sourceA.closed || sourceB.closed) throw new Error("EventSource ownership was not transferred cleanly");
if (vm.runInContext("state.eventsSessionId", context) !== "session_B") {
  throw new Error("eventsSessionId does not match selected session");
}

// A late event from the closed A source cannot refresh the currently selected B.
sourceA.emit("durable_workers.changed", { lastEventId: "late-A" });
if (timers.size !== 0) throw new Error("stale session event scheduled a refresh");

// EventSource owns its own native reconnect loop. An error callback must not
// instantiate a replacement source from application code.
sourceB.emit("error", {});
if (FakeEventSource.instances.length !== 2 || sourceB.closed) {
  throw new Error("client error handler replaced the native EventSource reconnect loop");
}
'''.replace("__HARNESS_JS__", json.dumps(str(JS)))

    completed = subprocess.run(
        [node, "-e", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
