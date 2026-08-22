"""Contract tests for the Harness native Hermes model controls.

The panel must remain a thin operator surface over Hermes' own model inventory
and assignments. It must not become a second provider catalog, credential
store, or change the existing browser-local session archive semantics.
"""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_task_recovery as recovery


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_models_bff_exposes_only_native_hermes_model_contracts():
    assert recovery.resolve_upstream("GET", "/api/harness/model-options") == "/api/model/options"
    assert recovery.resolve_upstream("GET", "/api/harness/model-auxiliary") == "/api/model/auxiliary"
    assert recovery.resolve_upstream("POST", "/api/harness/model-set") == "/api/model/set"

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert recovery.resolve_upstream(method, "/api/harness/model-options") is None
        assert recovery.resolve_upstream(method, "/api/harness/model-auxiliary") is None
    for method in ("GET", "PUT", "PATCH", "DELETE"):
        assert recovery.resolve_upstream(method, "/api/harness/model-set") is None


def test_models_panel_uses_hermes_as_single_source_of_truth():
    models = _static("harness-models.js")

    assert 'api(`/api/harness/model-options${suffix}`)' in models
    assert 'api("/api/harness/model-auxiliary")' in models
    assert 'api("/api/harness/model-set"' in models
    assert 'scope: "main"' in models
    assert 'scope: "auxiliary"' in models
    assert 'task: "__reset__"' in models
    assert 'provider: "auto"' in models
    assert 'confirm_expensive_model: true' in models

    # Browser code must not own provider credentials or persist a parallel
    # model configuration.
    forbidden = (
        "Authorization",
        "Bearer ",
        "HERMES_WEBUI_GATEWAY_API_KEY",
        "API_SERVER_KEY",
        "api_key",
        "localStorage",
        "sessionStorage",
    )
    for token in forbidden:
        assert token not in models


def test_models_asset_is_loaded_after_existing_h6_stack():
    recovery_source = (ROOT / "api" / "harness_ui_task_recovery.py").read_text(encoding="utf-8")
    server_source = (ROOT / "harness_server.py").read_text(encoding="utf-8")

    assert "h63.src='/harness-polish3.js'" in recovery_source
    assert "hm.src='/harness-models.js'" in recovery_source
    assert recovery_source.index("h63.src='/harness-polish3.js'") < recovery_source.index("hm.src='/harness-models.js'")
    assert '"/harness-models.js"' in server_source


def test_existing_harness_archive_remains_browser_local_masking():
    preferences = _static("harness-preferences.js")
    locales = _static("harness-locales.js")
    models = _static("harness-models.js")

    assert 'archivedSessions: PREFIX + "archivedSessions"' in preferences
    assert "function setSessionArchived(sessionId, archived)" in preferences
    assert "localStorage" in preferences
    assert "Hermes session data is not deleted" in locales

    # The model lot is deliberately orthogonal to archive/purge lifecycle.
    assert "setSessionArchived" not in models
    assert "/archive" not in models
    assert "/delete" not in models
    assert "/purge" not in models
