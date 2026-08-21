"""H6 final-contract tests for the complete H3-H5 Harness surface."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_task_recovery as recovery
from api import harness_ui_tasks as tasks


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

_BROWSER_ASSETS = (
    "harness.js",
    "harness-operations.js",
    "harness-tasks.js",
    "harness-task-recovery.js",
)


def _asset_source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_final_harness_has_exactly_one_eventsource_owner():
    sources = {name: _asset_source(name) for name in _BROWSER_ASSETS}

    assert sources["harness.js"].count("new EventSource") == 1
    assert "new EventSource" not in sources["harness-operations.js"]
    assert "new EventSource" not in sources["harness-tasks.js"]
    assert "new EventSource" not in sources["harness-task-recovery.js"]


def test_final_browser_assets_keep_server_side_secret_boundary():
    combined = "\n".join(_asset_source(name) for name in _BROWSER_ASSETS)

    for forbidden in (
        "Authorization",
        "Bearer ",
        "API_SERVER_KEY",
        "HERMES_WEBUI_GATEWAY_API_KEY",
    ):
        assert forbidden not in combined
    assert "localStorage" not in combined


def test_final_server_remains_localhost_only_and_serves_all_layers():
    server = (ROOT / "harness_server.py").read_text(encoding="utf-8")

    assert '_DEFAULT_HOST = "127.0.0.1"' in server
    assert 'host not in {"127.0.0.1", "::1", "localhost"}' in server
    assert "Hermes Harness UI foundation is localhost-only" in server
    assert "from api.harness_ui_task_recovery import" in server

    for asset in (
        "/harness.js",
        "/harness-operations.js",
        "/harness-tasks.js",
        "/harness-task-recovery.js",
    ):
        assert f'"{asset}"' in server


def test_final_bff_delegation_chain_preserves_h4_operations():
    server = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    recovery_source = (ROOT / "api" / "harness_ui_task_recovery.py").read_text(
        encoding="utf-8"
    )
    task_source = (ROOT / "api" / "harness_ui_tasks.py").read_text(
        encoding="utf-8"
    )
    legacy_server = (ROOT / "server.py").read_text(encoding="utf-8")

    assert "from api.harness_ui_task_recovery import" in server
    assert "from api import harness_ui_tasks as tasks" in recovery_source
    assert "from api import harness_ui_operations as operations" in recovery_source
    assert "from api import harness_ui_operations as operations" in task_source
    assert "harness_ui_task_recovery" not in legacy_server


def test_final_script_boot_order_is_h3_h4_h5_recovery_then_boot():
    boot = recovery._H5_RECOVERY_BOOT

    h4 = boot.index("h4.src='/harness-operations.js'")
    h5 = boot.index("h5.src='/harness-tasks.js'")
    h5_recovery = boot.index("h5r.src='/harness-task-recovery.js'")
    dom_boot = boot.index("document.dispatchEvent(new Event('DOMContentLoaded'))")

    assert h4 < h5 < h5_recovery < dom_boot
    assert boot.count("/harness-operations.js") == 1
    assert boot.count("/harness-tasks.js") == 1
    assert boot.count("/harness-task-recovery.js") == 1


def test_final_h5_bff_surface_adds_only_get_and_post_controls():
    h5_methods = {method for method, _pattern, _template in tasks._H5_ROUTES}

    assert h5_methods <= {"GET", "POST"}
    assert recovery._RECOVERY_ROUTE[0] == "POST"
    assert "DELETE" not in h5_methods
    assert "PUT" not in h5_methods
    assert "PATCH" not in h5_methods
