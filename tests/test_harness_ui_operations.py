"""H4 Harness UI operational-control contract tests."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui as foundation
from api import harness_ui_operations as operations

ROOT = Path(__file__).resolve().parents[1]


def test_h4_bff_routes_are_exact_method_scoped_and_preserve_h3():
    sid = "session_1"
    wid = "dw_1"
    aid = "dwa_1"

    assert operations.resolve_upstream(
        "GET", f"/api/harness/sessions/{sid}/worker-operations"
    ) == f"/api/sessions/{sid}/worker-operations"
    assert operations.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/workers/{wid}/retry"
    ) == f"/api/sessions/{sid}/workers/{wid}/retry"
    assert operations.resolve_upstream(
        "POST",
        f"/api/harness/sessions/{sid}/workers/{wid}/activations/{aid}/cancel",
    ) == f"/api/sessions/{sid}/workers/{wid}/activations/{aid}/cancel"

    assert operations.resolve_upstream(
        "GET", f"/api/harness/sessions/{sid}/workers"
    ) == foundation.resolve_upstream(
        "GET", f"/api/harness/sessions/{sid}/workers"
    )

    assert operations.resolve_upstream(
        "GET", f"/api/harness/sessions/{sid}/workers/{wid}/retry"
    ) is None
    assert operations.resolve_upstream(
        "DELETE",
        f"/api/harness/sessions/{sid}/workers/{wid}/activations/{aid}/cancel",
    ) is None
    assert operations.resolve_upstream(
        "POST", "/api/harness/sessions/../../etc/passwd/workers/x/retry"
    ) is None


def test_h4_shell_injects_operations_layer_before_h3_boot_event():
    html = (ROOT / "static" / "harness.html").read_text(encoding="utf-8")
    assert operations._H3_BOOT in html
    served = html.replace(operations._H3_BOOT, operations._H4_BOOT, 1)
    assert "h4.src='/harness-operations.js'" in served
    assert served.index("h4.src='/harness-operations.js'") < served.index(
        "document.dispatchEvent(new Event('DOMContentLoaded'))"
    )
    assert "harness-operations.js" not in html


def test_h4_server_uses_operations_bff_without_modifying_legacy_server():
    harness_server = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    legacy_server = (ROOT / "server.py").read_text(encoding="utf-8")

    assert "from api.harness_ui_operations import" in harness_server
    assert '"/harness-operations.js"' in harness_server
    assert "harness_ui_operations" not in legacy_server


def test_h4_browser_asset_exposes_cancel_retry_without_backend_secrets():
    source = (ROOT / "static" / "harness-operations.js").read_text(encoding="utf-8")

    assert "/worker-operations" in source
    assert "/retry`" in source
    assert "/cancel`" in source
    assert "expected_revision: worker.revision" in source
    assert "CANCEL_REQUESTED" in source
    assert "window.confirm" in source

    assert "HERMES_WEBUI_GATEWAY_API_KEY" not in source
    assert "API_SERVER_KEY" not in source
    assert "Authorization" not in source
    assert "Bearer " not in source


def test_h4_reuses_h3_sse_instead_of_creating_second_eventsource():
    source = (ROOT / "static" / "harness-operations.js").read_text(encoding="utf-8")

    assert "new EventSource" not in source
    assert "eventsSessionId" not in source
    assert "closeEvents()" not in source
    assert "h4FoundationLoadSessionData" in source
    assert "scheduleRefresh(state.sessionId)" in source


def test_h4_controls_are_state_gated_and_projection_bounded():
    source = (ROOT / "static" / "harness-operations.js").read_text(encoding="utf-8")

    assert 'worker.status !== "FAILED"' in source
    assert 'worker.status === "RUNNING"' in source
    assert '["STARTING", "RUNNING", "CANCEL_REQUESTED"]' in source
    assert 'worker.status !== "DORMANT"' in source
    assert "items.slice(0, 50)" in source
    assert "localStorage" not in source
