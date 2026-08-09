//! `/`, `/index.html`, `/session/<id>` app-shell — faithful port of the
//! index branch of `handle_get` (api/routes.py:12219).
//!
//! Upstream contract (baseline 192df903):
//! - Serves static/index.html with process-constant substitutions:
//!   `__WEBUI_VERSION__` (URL-encoded version token) and
//!   `__MAX_UPLOAD_BYTES__` (bytes), plus per-request `__CSRF_TOKEN_JSON__`
//!   (empty string when auth is disabled — the R0/R1 port has no auth yet).
//! - Content-Type: text/html; charset=utf-8.
//! - `/session/<id>` and `/session/static/*` are routed to the same shell /
//!   static sandbox (the static part is handled by the static_files router).

use std::sync::atomic::Ordering;

use axum::extract::{Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;

use crate::state::AppState;

pub async fn index(State(state): State<AppState>, req: Request) -> Response {
    let path = req.uri().path().to_string();
    let is_session = path.starts_with("/session/") && !path.starts_with("/session/static/");

    // /session/static/* is served by the static router; anything else under
    // /session/ gets the app shell (upstream catch-all behavior).
    if path.starts_with("/session/static/") {
        return (StatusCode::NOT_FOUND, Json(json!({"error": "not found"}))).into_response();
    }

    let index_path = state.config.index_html_path();
    let Ok(raw) = std::fs::read_to_string(&index_path) else {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": "index.html unavailable"})),
        )
            .into_response();
    };

    // URL-encode the version token like upstream `quote(WEBUI_VERSION, safe="")`.
    let version_token = urlencode(&state.config.version);
    let html = raw
        .replace("__WEBUI_VERSION__", &version_token)
        .replace(
            "__MAX_UPLOAD_BYTES__",
            &state.config.max_upload_bytes.to_string(),
        )
        .replace("__CSRF_TOKEN_JSON__", "\"\"");

    let mut resp = Response::new(axum::body::Body::from(html));
    *resp.status_mut() = StatusCode::OK;
    resp.headers_mut().insert(
        axum::http::header::CONTENT_TYPE,
        "text/html; charset=utf-8".parse().unwrap(),
    );
    if is_session {
        // Upstream serves /session/<id> with the same shell; no extra headers
        // are added in the R0/R1 subset.
    }
    // Keep the accept-loop counters in sync with the upstream health payload.
    state.record_request();
    let _ = Ordering::Relaxed;
    resp
}

fn urlencode(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new()
        .route("/", axum::routing::get(index))
        .route("/index.html", axum::routing::get(index))
        .route("/session/{*path}", axum::routing::get(index))
}
