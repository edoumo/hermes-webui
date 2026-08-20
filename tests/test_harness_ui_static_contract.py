"""Static-shell regression gates for Hermes Harness UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dialog_cancel_buttons_cannot_submit_create_forms():
    html = (ROOT / "static" / "harness.html").read_text(encoding="utf-8")
    assert html.count('type="button" data-dialog-close') == 4
    assert 'id="createWorkerSubmit"' in html
    assert 'id="createTaskSubmit"' in html
    assert "event.preventDefault()" in html
    assert "dialog.close()" in html


def test_harness_shell_bootstraps_csrf_before_loading_client():
    html = (ROOT / "static" / "harness.html").read_text(encoding="utf-8")
    csrf_pos = html.index("/api/harness/csrf")
    client_pos = html.index("s.src='/harness.js'")
    assert csrf_pos < client_pos
    assert "csrfToken" in html


def test_harness_browser_assets_do_not_embed_backend_secret_names():
    assets = "\n".join(
        (ROOT / "static" / name).read_text(encoding="utf-8")
        for name in ("harness.html", "harness.js", "harness.css")
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
