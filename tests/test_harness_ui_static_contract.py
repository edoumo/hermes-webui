"""Static-shell regression gates for Hermes Harness UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dialog_cancel_buttons_cannot_submit_forms():
    html = (ROOT / "static" / "harness.html").read_text(encoding="utf-8")
    assert html.count('type="button" data-dialog-close') == 6
    assert 'id="createWorkerSubmit"' in html
    assert 'id="saveWorkerSettingsBtn"' in html
    assert 'id="createTaskSubmit"' in html
    assert "event.preventDefault()" in html
    assert "dialog.close()" in html


def test_harness_shell_fetches_csrf_before_invoking_client_loader():
    html = (ROOT / "static" / "harness.html").read_text(encoding="utf-8")
    # Preferences are UI-only and may load before auth data. The operational
    # client still starts only after the CSRF request settles.
    assert 'src="/harness-preferences.js"' in html
    assert "fetch('/api/harness/csrf'" in html
    assert ".finally(loadClient)" in html
    assert "s.src='/harness.js'" in html
    assert "csrfToken" in html


def test_harness_browser_assets_do_not_embed_backend_secret_names():
    assets = "\n".join(
        (ROOT / "static" / name).read_text(encoding="utf-8")
        for name in (
            "harness.html",
            "harness.js",
            "harness-preferences.js",
            "harness-operations.js",
            "harness-tasks.js",
            "harness-task-recovery.js",
            "harness.css",
        )
    )
    assert "HERMES_WEBUI_GATEWAY_API_KEY" not in assets
    assert "API_SERVER_KEY" not in assets
    assert "Authorization: Bearer" not in assets


def test_harness_remains_a_separate_entrypoint():
    legacy = (ROOT / "server.py").read_text(encoding="utf-8")
    harness = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    assert "HarnessHandler" not in legacy
    assert "QuietHTTPServer" in harness
    assert "HERMES_HARNESS_PORT" in harness
    assert "HERMES_WEBUI_STATE_DIR" in harness
