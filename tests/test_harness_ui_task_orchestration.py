"""H5 Harness task-orchestration contract tests."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_operations as operations
from api import harness_ui_tasks as tasks

ROOT = Path(__file__).resolve().parents[1]


def test_h5_bff_routes_are_exact_and_preserve_h4():
    sid = "session_1"
    tid = "dwt_1"

    assert tasks.resolve_upstream(
        "GET", f"/api/harness/sessions/{sid}/worker-task-graph"
    ) == f"/api/sessions/{sid}/worker-task-graph"
    assert tasks.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/edit"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/edit"
    assert tasks.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dependencies/add"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/dependencies/add"
    assert tasks.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dependencies/remove"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/dependencies/remove"
    assert tasks.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dispatch"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/dispatch"

    h4_path = f"/api/harness/sessions/{sid}/worker-operations"
    assert tasks.resolve_upstream("GET", h4_path) == operations.resolve_upstream(
        "GET", h4_path
    )
    assert tasks.resolve_upstream(
        "DELETE", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dispatch"
    ) is None
    assert tasks.resolve_upstream(
        "POST", "/api/harness/sessions/../../etc/passwd/worker-task-graph"
    ) is None


def test_h5_shell_loads_h3_then_h4_then_h5_before_boot():
    html = (ROOT / "static" / "harness.html").read_text(encoding="utf-8")
    assert operations._H3_BOOT in html
    served = html.replace(operations._H3_BOOT, tasks._H5_BOOT, 1)

    assert "h4.src='/harness-operations.js'" in served
    assert "h5.src='/harness-tasks.js'" in served
    assert served.index("h4.src='/harness-operations.js'") < served.index(
        "h5.src='/harness-tasks.js'"
    )
    assert served.index("h5.src='/harness-tasks.js'") < served.index(
        "document.dispatchEvent(new Event('DOMContentLoaded'))"
    )


def test_h5_server_uses_task_bff_and_serves_task_asset():
    server = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    legacy = (ROOT / "server.py").read_text(encoding="utf-8")

    assert "from api.harness_ui_tasks import" in server
    assert '"/harness-tasks.js"' in server
    assert "harness_ui_tasks" not in legacy


def test_h5_browser_asset_has_dag_controls_without_new_eventsource_or_secrets():
    source = (ROOT / "static" / "harness-tasks.js").read_text(encoding="utf-8")

    assert "/worker-task-graph?limit=100" in source
    assert '"dependencies/add"' in source
    assert '"dependencies/remove"' in source
    assert '"dispatch"' in source
    assert "expected_revision: task.revision" in source
    assert "h5Levels" in source
    assert "h5DrawEdges" in source
    assert "Reset to pending" in source

    assert "new EventSource" not in source
    assert "eventsSessionId" not in source
    assert "Authorization" not in source
    assert "Bearer " not in source
    assert "API_SERVER_KEY" not in source
    assert "HERMES_WEBUI_GATEWAY_API_KEY" not in source
    assert "localStorage" not in source


def test_h5_projection_remains_bounded_and_state_driven():
    source = (ROOT / "static" / "harness-tasks.js").read_text(encoding="utf-8")

    assert "graph.tasks) ? graph.tasks.slice(0, 100)" in source
    assert 'task.status === "pending"' in source
    assert "task.ready" in source
    assert 'worker?.status !== "DORMANT"' in source
    assert "scheduleRefresh(state.sessionId)" in source
    assert "state.h5GraphSessionId === state.sessionId" in source
