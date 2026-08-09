//! `/health` endpoint — faithful port of `_handle_health` (api/routes.py:11785).
//!
//! Upstream contract (baseline 192df903):
//!
//! - GET /health → 200 JSON:
//!   `{"status":"ok","sessions":N,"active_streams":N,"active_runs":N,"runs":[],
//!   "last_run_finished_at":null,"server_started_at":<epoch float>,
//!   "uptime_seconds":<float>,"accept_loop":{"requests_total":N,"last_request_at":<float>}}`
//! - 503 with same shape when status != "ok" (streams lock blocked / run lifecycle).
//! - `?deep=1|true|yes|on` adds "checks" and may return 503.
//!
//! The R0/R1 port has no streams/runs yet, so status is always "ok" and
//! deep checks are trivially healthy — the JSON shape is preserved.

use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::state::AppState;

#[derive(Deserialize)]
pub struct HealthQuery {
    #[serde(default)]
    deep: Option<String>,
}

fn _flag_on(v: &Option<String>) -> bool {
    matches!(
        v.as_deref().map(str::to_lowercase).as_deref(),
        Some("1" | "true" | "yes" | "on")
    )
}

pub async fn health(State(state): State<AppState>, Query(q): Query<HealthQuery>) -> Response {
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0);
    let started = state.server_started_at.elapsed().as_secs_f64();
    let server_started_at = now - started;

    let mut payload: Value = json!({
        "status": "ok",
        "sessions": 0,
        "active_streams": 0,
        "active_runs": 0,
        "runs": [],
        "last_run_finished_at": null,
        "server_started_at": server_started_at,
        "uptime_seconds": (started * 10.0).round() / 10.0,
        "accept_loop": {
            "requests_total": state.requests_total.load(std::sync::atomic::Ordering::Relaxed),
            "last_request_at": state.last_request_at.load(std::sync::atomic::Ordering::Relaxed) as f64,
        },
    });

    if _flag_on(&q.deep) {
        // Upstream _deep_health_checks (api/routes.py:11704): streams_lock,
        // stream_runtime, sessions, projects, state_db. The R0/R1 port has no
        // sessions/projects/state.db yet, so counts are 0 and state_db is
        // "missing" when the state dir has no state.db (upstream semantics).
        let state_db_status = if state.config.state_dir.join("state.db").exists() {
            "ok"
        } else {
            "missing"
        };
        payload["checks"] = json!({
            "streams_lock": {"status": "ok", "active_streams": 0, "ms": 0.0},
            "stream_runtime": {
                "status": "ok",
                "active_streams": 0,
                "streams": [],
                "total_offline_buffered_events": 0,
                "total_subscribers": 0,
            },
            "sessions": {"status": "ok", "count": 0, "ms": 0.0},
            "projects": {"status": "ok", "count": 0, "ms": 0.0},
            "state_db": {"status": state_db_status, "ms": 0.0},
        });
    }

    let status = if payload["status"] == "ok" {
        StatusCode::OK
    } else {
        StatusCode::SERVICE_UNAVAILABLE
    };
    (status, Json(payload)).into_response()
}

pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new().route("/health", axum::routing::get(health))
}
