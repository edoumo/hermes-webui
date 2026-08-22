"""H6/H6.1/H6.2/H6.3 final-contract tests for the complete Harness surface."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_task_recovery as recovery
from api import harness_ui_tasks as tasks
from harness_runtime import bff as foundation


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

_BROWSER_RUNTIME_ASSETS = (
    "harness.js",
    "harness-operations.js",
    "harness-tasks.js",
    "harness-task-recovery.js",
    "harness-polish2.js",
    "harness-polish3.js",
    "harness-models.js",
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
        "HERMES_HARNESS_GATEWAY_API_KEY",
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


def test_final_server_defaults_loopback_and_remote_bind_is_guarded_by_harness_runtime():
    server = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    auth = (ROOT / "harness_runtime" / "auth.py").read_text(encoding="utf-8")

    assert '"127.0.0.1"' in server
    assert 'HERMES_HARNESS_ALLOW_REMOTE' in server
    assert 'HERMES_HARNESS_PASSWORD' in server
    assert "Non-loopback Harness bind requires" in server
    assert "resolve_bind" in server
    assert "from api.harness_ui_task_recovery import" in server
    assert "HERMES_HARNESS_STATE_DIR" in auth

    # The standalone server delegates asset resolution to the qualified H6
    # layer instead of duplicating an asset allowlist in the HTTP entrypoint.
    assert "serve_harness_asset(self, parsed.path)" in server
    recovery_source = (ROOT / "api" / "harness_ui_task_recovery.py").read_text(encoding="utf-8")
    for asset in (
        "/harness-task-recovery.js",
        "/harness-preferences.js",
        "/harness-locales.js",
        "/harness-polish2.js",
        "/harness-polish3.js",
        "/harness-models.js",
    ):
        assert f'"{asset}"' in recovery_source


def test_final_bff_delegation_chain_preserves_h4_operations_on_standalone_foundation():
    server = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    recovery_source = (ROOT / "api" / "harness_ui_task_recovery.py").read_text(encoding="utf-8")
    task_source = (ROOT / "api" / "harness_ui_tasks.py").read_text(encoding="utf-8")
    operations_source = (ROOT / "api" / "harness_ui_operations.py").read_text(encoding="utf-8")
    legacy_server = (ROOT / "server.py").read_text(encoding="utf-8")

    assert "from api.harness_ui_task_recovery import" in server
    assert "from api import harness_ui_tasks as tasks" in recovery_source
    assert "from api import harness_ui_operations as operations" in recovery_source
    assert "from api import harness_ui_operations as operations" in task_source
    assert "from harness_runtime import bff as foundation" in operations_source
    assert "from harness_runtime import bff as foundation" in task_source
    assert "from harness_runtime import bff as foundation" in recovery_source
    assert "harness_ui_task_recovery" not in legacy_server


def test_final_script_boot_order_is_h3_h4_h5_recovery_h62_h63_models_then_boot():
    boot = recovery._H5_RECOVERY_BOOT

    h4 = boot.index("h4.src='/harness-operations.js'")
    h5 = boot.index("h5.src='/harness-tasks.js'")
    h5_recovery = boot.index("h5r.src='/harness-task-recovery.js'")
    h62 = boot.index("h62.src='/harness-polish2.js'")
    h63 = boot.index("h63.src='/harness-polish3.js'")
    models = boot.index("hm.src='/harness-models.js'")
    dom_boot = boot.index("document.dispatchEvent(new Event('DOMContentLoaded'))")

    assert h4 < h5 < h5_recovery < h62 < h63 < models < dom_boot
    assert boot.count("/harness-operations.js") == 1
    assert boot.count("/harness-tasks.js") == 1
    assert boot.count("/harness-task-recovery.js") == 1
    assert boot.count("/harness-polish2.js") == 1
    assert boot.count("/harness-polish3.js") == 1
    assert boot.count("/harness-models.js") == 1


def test_final_bff_surface_remains_allowlisted_and_non_destructive():
    foundation_methods = {method for method, _pattern, _template in foundation._ROUTES}
    h5_methods = {method for method, _pattern, _template in tasks._H5_ROUTES}
    h61_methods = {method for method, _pattern, _template in recovery._H61_WORKER_ROUTES}
    h62_methods = {method for method, _pattern, _template in recovery._H62_ROUTES}

    assert foundation_methods <= {"GET", "POST"}
    assert h5_methods <= {"GET", "POST"}
    assert h61_methods == {"POST"}
    assert h62_methods == {"GET", "POST"}  # model-set is the sole H6 model write
    assert recovery._RECOVERY_ROUTE[0] == "POST"
    combined = foundation_methods | h5_methods | h61_methods | h62_methods
    assert "DELETE" not in combined
    assert "PUT" not in combined
    assert "PATCH" not in combined
