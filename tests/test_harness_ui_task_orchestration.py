"""H5/H6.1 Harness task-orchestration contract tests."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_operations as operations
from api import harness_ui_task_recovery as recovery
from api import harness_ui_tasks as tasks

ROOT = Path(__file__).resolve().parents[1]


def test_h5_bff_routes_are_exact_and_preserve_h4():
    sid = "session_1"
    wid = "dw_1"
    tid = "dwt_1"

    assert recovery.resolve_upstream(
        "GET", f"/api/harness/sessions/{sid}/worker-task-graph"
    ) == f"/api/sessions/{sid}/worker-task-graph"
    assert recovery.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/edit"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/edit"
    assert recovery.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dependencies/add"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/dependencies/add"
    assert recovery.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dependencies/remove"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/dependencies/remove"
    assert recovery.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dispatch"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/dispatch"
    assert recovery.resolve_upstream(
        "POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/recover"
    ) == f"/api/sessions/{sid}/worker-tasks/{tid}/recover"

    for action in ("edit", "archive", "restore"):
        assert recovery.resolve_upstream(
            "POST", f"/api/harness/sessions/{sid}/workers/{wid}/{action}"
        ) == f"/api/sessions/{sid}/workers/{wid}/{action}"
        assert recovery.resolve_upstream(
            "GET", f"/api/harness/sessions/{sid}/workers/{wid}/{action}"
        ) is None

    h4_path = f"/api/harness/sessions/{sid}/worker-operations"
    assert recovery.resolve_upstream("GET", h4_path) == operations.resolve_upstream(
        "GET", h4_path
    )
    assert recovery.resolve_upstream(
        "DELETE", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dispatch"
    ) is None
    assert recovery.resolve_upstream(
        "POST", "/api/harness/sessions/../../etc/passwd/worker-task-graph"
    ) is None


def test_h5_shell_loads_h3_h4_h5_recovery_then_boot():
    html = (ROOT / "static" / "harness.html").read_text(encoding="utf-8")
    assert operations._H3_BOOT in html
    served = html.replace(operations._H3_BOOT, recovery._H5_RECOVERY_BOOT, 1)

    assert "/harness-preferences.js" in served
    assert "h4.src='/harness-operations.js'" in served
    assert "h5.src='/harness-tasks.js'" in served
    assert "h5r.src='/harness-task-recovery.js'" in served
    assert served.index("h4.src='/harness-operations.js'") < served.index(
        "h5.src='/harness-tasks.js'"
    )
    assert served.index("h5.src='/harness-tasks.js'") < served.index(
        "h5r.src='/harness-task-recovery.js'"
    )
    assert served.index("h5r.src='/harness-task-recovery.js'") < served.index(
        "document.dispatchEvent(new Event('DOMContentLoaded'))"
    )


def test_h5_server_uses_recovery_bff_and_serves_all_task_assets():
    server = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    recovery_source = (ROOT / "api" / "harness_ui_task_recovery.py").read_text(encoding="utf-8")
    tasks_source = (ROOT / "api" / "harness_ui_tasks.py").read_text(encoding="utf-8")
    legacy = (ROOT / "server.py").read_text(encoding="utf-8")

    assert "from api.harness_ui_task_recovery import" in server
    assert "serve_harness_asset(self, parsed.path)" in server
    assert '"/harness-preferences.js"' in recovery_source
    assert '"/harness-task-recovery.js"' in recovery_source
    assert '"/harness-tasks.js"' in tasks_source
    assert "harness_ui_task_recovery" not in legacy


def test_h5_browser_assets_have_dag_recovery_without_eventsource_or_secrets():
    source = (ROOT / "static" / "harness-tasks.js").read_text(encoding="utf-8")
    recovery_source = (ROOT / "static" / "harness-task-recovery.js").read_text(
        encoding="utf-8"
    )
    combined = source + recovery_source

    assert "/worker-task-graph?limit=100" in source
    assert '"dependencies/add"' in source
    assert '"dependencies/remove"' in source
    assert '"dispatch"' in source
    assert "/recover`" in recovery_source
    assert "expected_revision: task.revision" in combined
    assert "h5Levels" in source
    assert "h5DrawEdges" in source
    assert 't("recoverTask")' in recovery_source
    assert 'button.textContent === "Reset to pending"' not in recovery_source

    assert "new EventSource" not in combined
    assert "eventsSessionId" not in combined
    assert "Authorization" not in combined
    assert "Bearer " not in combined
    assert "API_SERVER_KEY" not in combined
    assert "HERMES_HARNESS_GATEWAY_API_KEY" not in combined
    assert "HERMES_WEBUI_GATEWAY_API_KEY" not in combined
    assert "localStorage" not in combined


def test_h5_projection_remains_bounded_state_driven_and_single_stage_fits():
    source = (ROOT / "static" / "harness-tasks.js").read_text(encoding="utf-8")

    assert "graph.tasks) ? graph.tasks.slice(0, 100)" in source
    assert 'task.status === "pending"' in source
    assert "task.ready" in source
    assert 'worker?.status !== "DORMANT"' in source
    assert "scheduleRefresh(state.sessionId)" in source
    assert "state.h5GraphSessionId === state.sessionId" in source
    assert "min-width:max-content" not in source
    assert '--h5-stage-count' in source
    assert 'width:100%' in source
    assert 'canvas.scrollLeft = 0' in source
