//! `/api/sessions*` — faithful port of the upstream session routes
//! (`api/routes.py` handlers for `/api/sessions`, `/api/session`,
//! `/api/session/new`, `/api/session/update`, `/api/session/rename`,
//! `/api/session/delete`).
//!
//! Operates exclusively on the WebUI-owned JSON session store
//! (`STATE_DIR/sessions/*.json`). Agent-owned (hermes `state.db`) and CLI
//! imported sessions are never merged into this store; CLI sidecars that
//! exist on disk are classified read-only and surfaced separately in the
//! `/api/sessions` counts (see `crate::sessions::classify_ownership`).

use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};

use crate::sessions::{self, Session, Store};
use crate::state::AppState;

/// GET /api/sessions — list WebUI session metadata.
///
/// Response shape mirrors upstream `_session_list_payload_to_response`:
/// `{sessions, sidebar_reference_sessions, cli_count, archived_count, ...,
/// webui_session_count, cli_session_count, server_time, server_tz}`.
pub async fn list_sessions(State(state): State<AppState>) -> Response {
    let store = Store::new(state.config.state_dir.clone());
    let all = match store.list() {
        Ok(v) => v,
        Err(e) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("failed to list sessions: {e}"),
            );
        }
    };

    let mut rows: Vec<Value> = Vec::new();
    let mut webui_count = 0usize;
    let mut cli_count = 0usize;
    let mut archived_count = 0usize;
    let mut archived_webui_count = 0usize;
    let mut archived_cli_count = 0usize;

    for s in all {
        let ownership = sessions::classify_ownership(s.data());
        let is_cli = ownership.is_cli();
        let item = s.sidebar_item();
        // Keep `is_cli_session` on the row consistent with the classifier.
        let mut obj = match item {
            Value::Object(m) => m,
            _ => Map::new(),
        };
        obj.insert("is_cli_session".into(), json!(is_cli));
        obj.insert("attention".into(), json!(0.0));
        let archived = s.archived();
        if archived {
            archived_count += 1;
            if is_cli {
                archived_cli_count += 1;
            } else {
                archived_webui_count += 1;
            }
        }
        if is_cli {
            cli_count += 1;
        } else {
            webui_count += 1;
        }
        rows.push(Value::Object(obj));
    }

    // Upstream sorts sidebar rows by last_activity descending; fall back to
    // updated_at. We sort by (last_message_at, updated_at) desc.
    rows.sort_by(|a, b| {
        let ka = sort_key(a);
        let kb = sort_key(b);
        kb.partial_cmp(&ka).unwrap_or(std::cmp::Ordering::Equal)
    });

    let now = unix_now();
    let payload = json!({
        "sessions": rows,
        "sidebar_reference_sessions": [],
        "cli_count": cli_count,
        "archived_count": archived_count,
        "archived_webui_count": archived_webui_count,
        "archived_cli_count": archived_cli_count,
        "include_archived": false,
        "all_profiles": false,
        "active_profile": "default",
        "other_profile_count": 0,
        "server_time": now,
        "server_tz": "+0000",
        "webui_session_count": webui_count,
        "cli_session_count": cli_count,
    });
    (StatusCode::OK, Json(payload)).into_response()
}

/// GET /api/session?session_id=X&messages=0&resolve_model=0 — load one session.
///
/// `messages=0` skips the message payload (fast session switching). `resolve_model`
/// is accepted for compatibility (no provider resolution in the R2 port).
pub async fn get_session(
    State(state): State<AppState>,
    axum::extract::Query(params): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> Response {
    let sid = params.get("session_id").cloned().unwrap_or_default();
    if sid.is_empty() {
        return error_response(StatusCode::BAD_REQUEST, "session_id is required");
    }
    let load_messages = params.get("messages").map(|s| s.as_str()) != Some("0");

    let store = Store::new(state.config.state_dir.clone());
    let session = match store.load(&sid) {
        Ok(Some(s)) => s,
        Ok(None) => {
            // Match upstream: invalid/unparsable id → 404 "Session not found".
            return error_response(StatusCode::NOT_FOUND, "Session not found");
        }
        Err(e) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("failed to load session: {e}"),
            );
        }
    };

    let mut compact = session.compact();
    if !load_messages {
        if let Some(obj) = compact.as_object_mut() {
            obj.insert("messages".into(), Value::Array(vec![]));
        }
    }
    // `_messages_truncated`/`_messages_offset` are only relevant when paging.
    let payload = json!({ "session": compact });
    (StatusCode::OK, Json(payload)).into_response()
}

/// POST /api/session/new — create a new WebUI session.
///
/// Accepts optional `workspace`, `model`, `model_provider`, `profile`, `project_id`.
/// Mirrors upstream: the session is saved to the JSON store (upstream defers the
/// first write to the first message, but persists worktree-backed sessions; for a
/// self-contained port we persist immediately so the created session survives a
/// restart — a superset that never breaks upstream compatibility of the file).
pub async fn new_session(State(state): State<AppState>, Json(body): Json<Value>) -> Response {
    let store = Store::new(state.config.state_dir.clone());
    let sid = store.generate_id();

    let mut session = Session::new(sid.clone());
    // Défauts upstream (api/models.py Session.__init__ + routes.py:14567) :
    // workspace = défaut résolu, profile = "default", model = "" (env
    // HERMES_WEBUI_DEFAULT_MODEL, vide par défaut).
    if let Some(ws) = body.get("workspace").and_then(|v| v.as_str()) {
        session
            .data_mut()
            .insert("workspace".into(), Value::String(ws.to_string()));
        // created_workspace defaults to workspace for new sessions.
        session
            .data_mut()
            .insert("created_workspace".into(), Value::String(ws.to_string()));
    } else {
        let ws = crate::routes::settings::discover_default_workspace(&state);
        session
            .data_mut()
            .insert("workspace".into(), Value::String(ws.clone()));
        session
            .data_mut()
            .insert("created_workspace".into(), Value::String(ws));
    }
    // NB : Session::new() insère TOUTES les METADATA_FIELDS à Value::Null —
    // les checks ci-dessous testent la VALEUR (null), pas la présence.
    if matches!(session.data().get("profile"), None | Some(Value::Null)) {
        session
            .data_mut()
            .insert("profile".into(), Value::String("default".into()));
    }
    if matches!(session.data().get("model"), None | Some(Value::Null)) {
        session
            .data_mut()
            .insert("model".into(), Value::String(String::new()));
    }
    if let Some(m) = body.get("model").and_then(|v| v.as_str()) {
        session.set_model(Some(m.to_string()));
    }
    if let Some(p) = body.get("model_provider").and_then(|v| v.as_str()) {
        session.set_model_provider(Some(p.to_string()));
    }
    if let Some(prof) = body.get("profile").and_then(|v| v.as_str()) {
        session
            .data_mut()
            .insert("profile".into(), Value::String(prof.to_string()));
    }
    if let Some(pid) = body.get("project_id").and_then(|v| v.as_str()) {
        session
            .data_mut()
            .insert("project_id".into(), Value::String(pid.to_string()));
    }

    session.prepare_save();
    if let Err(e) = store.save(&session) {
        return error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            &format!("failed to create session: {e}"),
        );
    }

    let mut compact = session.compact();
    if let Some(obj) = compact.as_object_mut() {
        obj.insert("messages".into(), Value::Array(vec![]));
    }
    let payload = json!({ "session": compact });
    (StatusCode::OK, Json(payload)).into_response()
}

/// POST /api/session/rename — user-driven title rename.
/// Mirrors upstream `/api/session/rename` + `apply_session_title_rename`.
pub async fn rename_session(State(state): State<AppState>, Json(body): Json<Value>) -> Response {
    let Some(sid) = body.get("session_id").and_then(|v| v.as_str()) else {
        return error_response(StatusCode::BAD_REQUEST, "session_id and title are required");
    };
    let Some(title) = body.get("title").and_then(|v| v.as_str()) else {
        return error_response(StatusCode::BAD_REQUEST, "session_id and title are required");
    };

    let store = Store::new(state.config.state_dir.clone());
    let Some(mut session) = (match store.load(sid) {
        Ok(s) => s,
        Err(e) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("failed to load session: {e}"),
            );
        }
    }) else {
        return error_response(StatusCode::NOT_FOUND, "Session not found");
    };

    if session.read_only() || sessions::classify_ownership(session.data()).is_cli() {
        return error_response(
            StatusCode::FORBIDDEN,
            "Read-only imported sessions cannot be renamed from WebUI",
        );
    }

    session.apply_title_rename(title);
    session.prepare_save();
    if let Err(e) = store.save(&session) {
        return error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            &format!("failed to rename session: {e}"),
        );
    }
    let payload = json!({ "session": session.compact() });
    (StatusCode::OK, Json(payload)).into_response()
}

/// POST /api/session/update — update workspace / model / provider metadata.
/// Mirrors upstream `/api/session/update`.
pub async fn update_session(State(state): State<AppState>, Json(body): Json<Value>) -> Response {
    let Some(sid) = body.get("session_id").and_then(|v| v.as_str()) else {
        return error_response(StatusCode::BAD_REQUEST, "session_id is required");
    };

    let store = Store::new(state.config.state_dir.clone());
    let Some(mut session) = (match store.load(sid) {
        Ok(s) => s,
        Err(e) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("failed to load session: {e}"),
            );
        }
    }) else {
        return error_response(StatusCode::NOT_FOUND, "Session not found");
    };

    if session.read_only() || sessions::classify_ownership(session.data()).is_cli() {
        return error_response(
            StatusCode::FORBIDDEN,
            "Read-only imported sessions cannot be updated from WebUI",
        );
    }

    if let Some(ws) = body.get("workspace").and_then(|v| v.as_str()) {
        session.set_workspace(ws.to_string());
    }
    if let Some(m) = body.get("model").and_then(|v| v.as_str()) {
        session.set_model(Some(m.to_string()));
    }
    if body.get("model_provider").is_some() {
        let p = body.get("model_provider").and_then(|v| v.as_str());
        session.set_model_provider(p.map(|s| s.to_string()));
    }

    session.prepare_save();
    if let Err(e) = store.save(&session) {
        return error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            &format!("failed to update session: {e}"),
        );
    }
    let mut compact = session.compact();
    if let Some(obj) = compact.as_object_mut() {
        obj.insert("messages".into(), Value::Array(vec![]));
    }
    let payload = json!({ "session": compact });
    (StatusCode::OK, Json(payload)).into_response()
}

/// POST /api/session/delete — delete a WebUI session sidecar (+ .bak).
/// Mirrors upstream `/api/session/delete`.
pub async fn delete_session(State(state): State<AppState>, Json(body): Json<Value>) -> Response {
    let Some(sid) = body.get("session_id").and_then(|v| v.as_str()) else {
        return error_response(StatusCode::BAD_REQUEST, "session_id is required");
    };
    if !sessions::is_safe_session_id(sid) {
        return error_response(StatusCode::BAD_REQUEST, "Invalid session_id");
    }

    let store = Store::new(state.config.state_dir.clone());
    // Read-only CLI imported sessions are refused, mirroring upstream.
    match store.load(sid) {
        Ok(Some(s)) => {
            if s.read_only() || sessions::classify_ownership(s.data()).is_cli() {
                return error_response(
                    StatusCode::BAD_REQUEST,
                    "Read-only imported sessions cannot be deleted from WebUI",
                );
            }
        }
        Ok(None) => {
            // Upstream still succeeds deleting an absent sidecar id (returns ok).
        }
        Err(e) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("failed to load session: {e}"),
            );
        }
    }

    match store.delete(sid) {
        Ok(()) => (),
        Err(e) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("failed to delete session: {e}"),
            );
        }
    }
    let payload = json!({ "ok": true, "state_db_cleanup_failed": false });
    (StatusCode::OK, Json(payload)).into_response()
}

/// Assemble the sessions router.
pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new()
        .route("/api/sessions", axum::routing::get(list_sessions))
        .route("/api/session", axum::routing::get(get_session))
        .route("/api/session/new", axum::routing::post(new_session))
        .route("/api/session/rename", axum::routing::post(rename_session))
        .route("/api/session/update", axum::routing::post(update_session))
        .route("/api/session/delete", axum::routing::post(delete_session))
}

// ── internal helpers ───────────────────────────────────────────────────────

fn error_response(status: StatusCode, msg: &str) -> Response {
    (status, Json(json!({ "error": msg }))).into_response()
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn sort_key(v: &Value) -> f64 {
    let last = v
        .get("last_message_at")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let updated = v.get("updated_at").and_then(Value::as_f64).unwrap_or(0.0);
    if last > 0.0 {
        last
    } else {
        updated
    }
}
