"""Contract tests for the experimental Hermes Harness UI foundation."""
from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

import api.harness_ui as harness
from api.harness_ui_bind import resolve_harness_bind


ROOT = Path(__file__).resolve().parents[1]


def test_harness_is_default_off_and_explicit_opt_in():
    assert harness.harness_enabled({}) is False
    for value in ("1", "true", "yes", "on"):
        assert harness.harness_enabled({"HERMES_WEBUI_HARNESS_UI": value}) is True
    for value in ("0", "false", "off", ""):
        assert harness.harness_enabled({"HERMES_WEBUI_HARNESS_UI": value}) is False


def test_bff_route_allowlist_is_exact_and_method_scoped():
    sid = "session_1"
    wid = "dw_abc123"
    tid = "dwt_abc123"
    cases = {
        ("GET", "/api/harness/models"): "/v1/models",
        ("GET", "/api/harness/sessions"): "/api/sessions",
        ("POST", "/api/harness/sessions"): "/api/sessions",
        ("GET", f"/api/harness/sessions/{sid}"): f"/api/sessions/{sid}",
        ("GET", f"/api/harness/sessions/{sid}/workers"): f"/api/sessions/{sid}/workers",
        ("POST", f"/api/harness/sessions/{sid}/workers"): f"/api/sessions/{sid}/workers",
        ("GET", f"/api/harness/sessions/{sid}/workers/{wid}"): f"/api/sessions/{sid}/workers/{wid}",
        ("GET", f"/api/harness/sessions/{sid}/workers/{wid}/messages"): f"/api/sessions/{sid}/workers/{wid}/messages",
        ("POST", f"/api/harness/sessions/{sid}/workers/{wid}/messages"): f"/api/sessions/{sid}/workers/{wid}/messages",
        ("POST", f"/api/harness/sessions/{sid}/workers/{wid}/run"): f"/api/sessions/{sid}/workers/{wid}/run",
        ("GET", f"/api/harness/sessions/{sid}/workers/{wid}/activations"): f"/api/sessions/{sid}/workers/{wid}/activations",
        ("GET", f"/api/harness/sessions/{sid}/worker-tasks"): f"/api/sessions/{sid}/worker-tasks",
        ("POST", f"/api/harness/sessions/{sid}/worker-tasks"): f"/api/sessions/{sid}/worker-tasks",
        ("POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/status"): f"/api/sessions/{sid}/worker-tasks/{tid}/status",
        ("POST", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/dependencies"): f"/api/sessions/{sid}/worker-tasks/{tid}/dependencies",
        ("GET", f"/api/harness/sessions/{sid}/worker-events"): f"/api/sessions/{sid}/worker-events",
    }
    assert len(cases) == 16
    for (method, path), expected in cases.items():
        assert harness.resolve_upstream(method, path) == expected

    assert harness.resolve_upstream("DELETE", f"/api/harness/sessions/{sid}/workers/{wid}") is None
    assert harness.resolve_upstream("PATCH", f"/api/harness/sessions/{sid}/worker-tasks/{tid}/status") is None
    assert harness.resolve_upstream("GET", "/api/harness/../../etc/passwd") is None
    assert harness.resolve_upstream("GET", f"/api/harness/sessions/{sid}/workers/{wid}/unknown") is None


def test_gateway_origin_is_loopback_only_and_cannot_embed_credentials():
    assert harness._gateway_base_url({}) == "http://127.0.0.1:8642"
    assert harness._gateway_base_url({"HERMES_WEBUI_GATEWAY_BASE_URL": "http://localhost:9000"}) == "http://localhost:9000"
    assert harness._gateway_base_url({"HERMES_WEBUI_GATEWAY_BASE_URL": "http://[::1]:8642"}) == "http://[::1]:8642"

    with pytest.raises(harness.HarnessConfigError):
        harness._gateway_base_url({"HERMES_WEBUI_GATEWAY_BASE_URL": "https://example.com"})
    with pytest.raises(harness.HarnessConfigError):
        harness._gateway_base_url({"HERMES_WEBUI_GATEWAY_BASE_URL": "http://user:pass@127.0.0.1:8642"})
    with pytest.raises(harness.HarnessConfigError):
        harness._gateway_base_url({"HERMES_WEBUI_GATEWAY_BASE_URL": "http://127.0.0.1:8642/api"})


def test_gateway_key_is_required_and_never_part_of_target_url():
    with pytest.raises(harness.HarnessConfigError):
        harness._gateway_api_key({})

    fake = SimpleNamespace(
        headers={"Content-Length": "2"},
        rfile=io.BytesIO(b"{}"),
    )
    parsed = SimpleNamespace(query="")
    request = harness._upstream_request(
        fake,
        parsed,
        method="POST",
        upstream_path="/api/sessions",
        environ={
            "HERMES_WEBUI_GATEWAY_BASE_URL": "http://127.0.0.1:8642",
            "HERMES_WEBUI_GATEWAY_API_KEY": "super-secret-test-key",
        },
    )
    assert request.full_url == "http://127.0.0.1:8642/api/sessions"
    assert "super-secret-test-key" not in request.full_url
    assert request.get_header("Authorization") == "Bearer super-secret-test-key"


def test_query_and_request_body_are_bounded():
    assert harness._safe_query("limit=50&cursor=abc") == "limit=50&cursor=abc"
    with pytest.raises(harness.HarnessConfigError):
        harness._safe_query("token=secret")

    too_large = SimpleNamespace(
        headers={"Content-Length": str(harness._MAX_REQUEST_BYTES + 1)},
        rfile=io.BytesIO(),
    )
    with pytest.raises(harness.HarnessConfigError):
        harness._read_json_body(too_large)

    non_object = SimpleNamespace(
        headers={"Content-Length": "2"},
        rfile=io.BytesIO(b"[]"),
    )
    with pytest.raises(harness.HarnessConfigError):
        harness._read_json_body(non_object)


def test_static_client_contains_no_gateway_bearer_secret_surface():
    assets = [
        ROOT / "static" / "harness.html",
        ROOT / "static" / "harness.js",
        ROOT / "static" / "harness-preferences.js",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in assets)
    assert "HERMES_WEBUI_GATEWAY_API_KEY" not in combined
    assert "Authorization" not in combined
    assert "Bearer " not in combined
    assert "/api/harness/csrf" in combined
    assert "X-Hermes-CSRF-Token" in combined


def test_browser_projection_is_explicitly_bounded_and_event_driven():
    js = (ROOT / "static" / "harness.js").read_text(encoding="utf-8")
    prefs = (ROOT / "static" / "harness-preferences.js").read_text(encoding="utf-8")
    assert "workers?limit=100" in js
    assert "worker-tasks?limit=100" in js
    assert "messages?limit=50" in js
    assert "activations?limit=50" in js
    assert "new EventSource" in js
    assert "durable_workers.changed" in js
    assert "localStorage" not in js
    assert 'const PREFIX = "hermesHarness.ui."' in prefs


def test_standalone_server_preserves_auth_csrf_and_guards_remote_bind():
    source = (ROOT / "harness_server.py").read_text(encoding="utf-8")
    assert "check_auth(self, parsed)" in source
    assert "_check_csrf(self)" in source
    assert "csrf_token_for_session" in source
    assert "HERMES_HARNESS_STATE_DIR" in source
    assert 'HERMES_WEBUI_COOKIE_NAME", "hermes_harness_session"' in source
    assert "resolve_harness_bind" in source

    assert resolve_harness_bind({}) == ("127.0.0.1", 8790)
    with pytest.raises(RuntimeError, match="HERMES_HARNESS_ALLOW_REMOTE"):
        resolve_harness_bind({"HERMES_HARNESS_HOST": "192.168.1.187"})
    with pytest.raises(RuntimeError, match="HERMES_WEBUI_PASSWORD"):
        resolve_harness_bind({
            "HERMES_HARNESS_HOST": "192.168.1.187",
            "HERMES_HARNESS_ALLOW_REMOTE": "1",
        })
    assert resolve_harness_bind({
        "HERMES_HARNESS_HOST": "192.168.1.187",
        "HERMES_HARNESS_ALLOW_REMOTE": "1",
        "HERMES_WEBUI_PASSWORD": "test-only-password",
        "HERMES_HARNESS_PORT": "8794",
    }) == ("192.168.1.187", 8794)


def test_harness_does_not_modify_legacy_server_entrypoint():
    server = (ROOT / "server.py").read_text(encoding="utf-8")
    assert "api.harness_ui" not in server
    assert "HarnessHandler" not in server
