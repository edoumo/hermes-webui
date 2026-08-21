"""H6.1 acceptance-regression tests derived from the first human UAT."""
from __future__ import annotations

from pathlib import Path

from api import harness_ui_task_recovery as recovery


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _static(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_uat_has_french_english_language_selector_and_theme_toggle():
    html = _static("harness.html")
    prefs = _static("harness-preferences.js")
    css = _static("harness.css")

    assert 'id="localeSelect"' in html
    assert '<option value="en">EN</option>' in html
    assert '<option value="fr">FR</option>' in html
    assert 'id="themeToggle"' in html
    assert 'fr: {' in prefs
    assert 'en: {' in prefs
    assert 'navigator.language' in prefs
    assert 'startsWith("fr")' in prefs
    assert 'theme === "dark" ? "light" : "dark"' in prefs
    assert ':root[data-theme="light"]' in css


def test_uat_desktop_rails_are_resizable_collapsible_and_preferences_only():
    html = _static("harness.html")
    prefs = _static("harness-preferences.js")
    css = _static("harness.css")

    assert 'data-rail-resizer="sessions"' in html
    assert 'data-rail-resizer="workers"' in html
    assert 'data-collapse-rail="sessions"' in html
    assert 'data-collapse-rail="workers"' in html
    assert 'pointerdown' in prefs
    assert 'setPointerCapture' in prefs
    assert 'sessionsWidth' in prefs
    assert 'workersWidth' in prefs
    assert '.layout.sessions-collapsed' in css
    assert '.layout.workers-collapsed' in css


def test_uat_task_graph_single_stage_no_longer_forces_max_content_width():
    source = _static("harness-tasks.js")

    assert 'min-width:max-content' not in source
    assert 'grid-auto-columns:minmax(280px,1fr)' in source
    assert 'min-width:max(100%,calc(var(--h5-stage-count,1) * 300px))' in source
    assert 'levelsRoot.style.setProperty("--h5-stage-count"' in source
    assert 'canvas.scrollLeft = 0' in source


def test_uat_model_is_discoverable_at_create_and_existing_worker_settings():
    html = _static("harness.html")
    js = _static("harness.js")

    assert html.count('list="modelOptions"') == 2
    assert 'id="workerSettingsBtn"' in html
    assert 'id="workerSettingsModel"' in html
    assert 'id="modelOptions"' in html
    assert 'await api("/api/harness/models")' in js
    assert '/workers/${safeId(worker.worker_id)}/edit`' in js
    assert 'model: model || null' in js


def test_uat_worker_archive_is_backend_reversible_not_hard_delete():
    js = _static("harness.js")
    bff = _read("api/harness_ui_task_recovery.py")

    assert '/archive$' in bff
    assert '/restore$' in bff
    assert 'archived ? "archive" : "restore"' in js
    assert 'status !== "DISABLED"' in js
    assert 'method: "DELETE"' not in js


def test_uat_session_archive_is_explicitly_browser_local_not_data_deletion():
    prefs = _static("harness-preferences.js")
    js = _static("harness.js")

    assert 'archivedSessions: PREFIX + "archivedSessions"' in prefs
    assert 'setSessionArchived' in prefs
    assert 'ui.setSessionArchived(current, true)' in js
    assert 'method: "DELETE"' not in js


def test_uat_worker_controls_are_post_only_through_exact_bff_allowlist():
    sid = "session_1"
    wid = "dw_1"
    for action in ("edit", "archive", "restore"):
        browser_path = f"/api/harness/sessions/{sid}/workers/{wid}/{action}"
        upstream_path = f"/api/sessions/{sid}/workers/{wid}/{action}"
        assert recovery.resolve_upstream("POST", browser_path) == upstream_path
        for method in ("GET", "PUT", "PATCH", "DELETE"):
            assert recovery.resolve_upstream(method, browser_path) is None
