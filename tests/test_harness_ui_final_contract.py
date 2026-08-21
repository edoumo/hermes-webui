"""H6/H6.1/H6.2/H6.3 final-contract tests for the complete Harness surface."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_task_recovery as recovery
from api import harness_ui_tasks as tasks


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

_BROWSER_RUNTIME_ASSETS = (
    "harness.js",
    "harness-operations.js",
    "harness-tasks.js",
    "harness-task-recovery.js",
    "harness-polish2.js",
    "harness-polish3.js",
)
_BROWSER_ASSETS = ("harness-locales.js", "harness-preferences.js") + _BROWSER_RUNTIME_ASSETS


def _asset_source(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_final_harness_has_exactly_one_eventsource_owner():
    sources = {name: _asset_source(name) for name in _BROWSER_ASSETS}

    assert sources["harness.js"].count("new EventSource") == 1
    for name in _BROWSER_ASSETS:
        if name != "harness.js":
            assert "new EventSource" not in sources[name]


def test_final_browser_assets_keep_server_side_secret_boundary():
    combined = "\n".join(_asset_source(name) for name in _BROWSER_ASSETS)

    for forbidden in (
        "Authorization",
        "Bearer ",
        "API_SERVER_KEY",
        "HERMES_WEBUI_GATEWAY_API_KEY",
        "durable-workers.db",
    ):
        assert forbidden not in combined


def test_uat_preferences_are_the_only_localstorage_surface_and_are_ui_only():
    runtime = "\n".join(_asset_source(name) for name in _BROWSER_RUNTIME_ASSETS)
    locales = _asset_source("harness-locales.js")
    preferences = _asset_source("harness-preferences.js")

    assert "localStorage" not in runtime
    assert "localStorage" not in locales
    assert "localStorage" in preferences
    assert 'const PREFIX = "hermesHarness.ui."' in preferences
    for forbidden in (
        "messageInput",
        "messageList",
        "activationList",
        "taskList",
        "csrfToken",
        "apiKey",
        "Authorization",
        "Bearer ",
    ):
        assert forbidden not in preferences


def test_final_server_defaults_loopback_and_remote_bind_is_guarded():
    server = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    bind_policy = (ROOT / "api" / "harness_ui_bind.py").read_text(encoding="utf-8")

    assert '_DEFAULT_HOST = "127.0.0.1"' in bind_policy
    assert 'HERMES_HARNESS_ALLOW_REMOTE' in bind_policy
    assert 'HERMES_WEBUI_PASSWORD' in bind_policy
    assert "Non-loopback Harness bind requires" in bind_policy
    assert "resolve_harness_bind" in server
    assert "from api.harness_ui_task_recovery import" in server

    for asset in (
        "/harness.js",
        "/harness-locales.js",
        "/harness-preferences.js",
        "/harness-operations.js",
        "/harness-tasks.js",
        "/harness-task-recovery.js",
        "/harness-polish2.js",
        "/harness-polish3.js",
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


def test_final_script_boot_order_is_h3_h4_h5_recovery_h62_h63_then_boot():
    boot = recovery._H5_RECOVERY_BOOT

    h4 = boot.index("h4.src='/harness-operations.js'")
    h5 = boot.index("h5.src='/harness-tasks.js'")
    h5_recovery = boot.index("h5r.src='/harness-task-recovery.js'")
    h62 = boot.index("h62.src='/harness-polish2.js'")
    h63 = boot.index("h63.src='/harness-polish3.js'")
    dom_boot = boot.index("document.dispatchEvent(new Event('DOMContentLoaded'))")

    assert h4 < h5 < h5_recovery < h62 < h63 < dom_boot
    assert boot.count("/harness-operations.js") == 1
    assert boot.count("/harness-tasks.js") == 1
    assert boot.count("/harness-task-recovery.js") == 1
    assert boot.count("/harness-polish2.js") == 1
    assert boot.count("/harness-polish3.js") == 1


def test_final_bff_surface_remains_non_destructive():
    h5_methods = {method for method, _pattern, _template in tasks._H5_ROUTES}
    h61_methods = {method for method, _pattern, _template in recovery._H61_WORKER_ROUTES}
    h62_methods = {method for method, _pattern, _template in recovery._H62_ROUTES}

    assert h5_methods <= {"GET", "POST"}
    assert h61_methods == {"POST"}
    assert h62_methods == {"GET"}
    assert recovery._RECOVERY_ROUTE[0] == "POST"
    assert "DELETE" not in h5_methods | h61_methods | h62_methods
    assert "PUT" not in h5_methods | h61_methods | h62_methods
    assert "PATCH" not in h5_methods | h61_methods | h62_methods
