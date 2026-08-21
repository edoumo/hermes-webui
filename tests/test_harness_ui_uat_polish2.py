"""H6.2 acceptance gates derived from Ed's second human UAT."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_task_recovery as recovery


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_h62_model_inventory_uses_rich_hermes_picker_endpoint():
    polish = _static("harness-polish2.js")

    assert recovery.resolve_upstream("GET", "/api/harness/model-options") == "/api/model/options"
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert recovery.resolve_upstream(method, "/api/harness/model-options") is None
    assert 'await api("/api/harness/model-options")' in polish
    assert "payload?.providers" in polish
    assert "payload?.provider" in polish
    assert "row?.models" in polish


def test_h62_worker_model_control_is_an_explicit_select_with_custom_escape_hatch():
    html = _static("harness.html")
    polish = _static("harness-polish2.js")

    assert '<select name="model_choice" id="workerModelSelect"></select>' in html
    assert '<select name="model_choice" id="workerSettingsModelSelect"></select>' in html
    assert 'id="workerCustomModelInput" class="hidden"' in html
    assert 'id="workerSettingsCustomModelInput" class="hidden"' in html
    assert 'select.add(new Option(t("gatewayDefault"), ""))' in polish
    assert 'select.add(new Option(t("customModel"), H62_CUSTOM_MODEL))' in polish
    assert "only models belonging to the active provider are offered" in polish


def test_h62_session_creation_has_optional_human_title_and_blank_auto_fallback():
    html = _static("harness.html")
    polish = _static("harness-polish2.js")

    assert 'id="sessionDialog"' in html
    assert 'id="sessionForm"' in html
    assert 'name="title" id="sessionTitleInput"' in html
    assert "required" not in html.split('id="sessionTitleInput"', 1)[1].split(">", 1)[0]
    assert 'const body = title ? { title } : {};' in polish
    assert 'api("/api/harness/sessions", { method: "POST", body })' in polish


def test_h62_locale_catalog_is_data_only_extensible_and_has_six_initial_languages():
    html = _static("harness.html")
    locales = _static("harness-locales.js")
    prefs = _static("harness-preferences.js")

    assert html.index('src="/harness-locales.js"') < html.index('src="/harness-preferences.js"')
    assert "window.HermesHarnessLocales" in locales
    for code, label in (
        ("en", "English"),
        ("fr", "Français"),
        ("es", "Español"),
        ("pt", "Português"),
        ("de", "Deutsch"),
        ("it", "Italiano"),
    ):
        assert f'{code}: Object.freeze({{ label: "{label}"' in locales
    assert "Object.entries(LOCALES)" in prefs
    assert "normalizeLocale" in prefs
    assert "navigator.languages" in prefs
    assert "localStorage" not in locales


def test_h62_assets_do_not_take_sse_or_secret_ownership():
    combined = _static("harness-locales.js") + "\n" + _static("harness-polish2.js")

    assert "new EventSource" not in combined
    assert "HERMES_WEBUI_GATEWAY_API_KEY" not in combined
    assert "API_SERVER_KEY" not in combined
    assert "Authorization" not in combined
    assert "Bearer " not in combined
    assert "localStorage" not in combined
