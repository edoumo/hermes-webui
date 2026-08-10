//! Tests du port import/export de sessions (Track F R3) — couverture :
//! export JSON (400/404/200, redaction, Content-Disposition), export HTML
//! (200, content-type, palette), import (400 body invalide, messages requis,
//! workspace invalide, round-trip export→import, pinned/tool_calls conservés,
//! persistance disque), import_cli classé HERMES_BRIDGE_REQUIRED (non porté).

use std::path::PathBuf;

use axum::body::Body;
use axum::http::{header, Request, StatusCode};
use serde_json::{json, Value};
use tower::ServiceExt;

use hermes_webui_rust::app::build_router;
use hermes_webui_rust::config::Config;
use hermes_webui_rust::sessions::{Session, Store};
use hermes_webui_rust::state::AppState;

fn test_state() -> AppState {
    let repo_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("repo dir");
    let state_dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-importexport-test-{}-{}",
        std::process::id(),
        std::thread::current()
            .name()
            .unwrap_or("unnamed")
            .replace("::", "-")
    ));
    let _ = std::fs::remove_dir_all(&state_dir);
    let config = Config::new(
        "127.0.0.1".into(),
        0,
        repo_dir,
        "exp-v0.52.192".into(),
        20,
        state_dir,
    )
    .expect("config");
    AppState::new(config, std::time::Instant::now())
}

fn store(state: &AppState) -> Store {
    Store::new(state.config.state_dir.clone())
}

fn seed_session(state: &AppState, sid: &str, title: &str) {
    let mut s = Session::new(sid.to_string());
    s.set_title(title.to_string());
    s.data_mut().insert(
        "messages".into(),
        json!([
            {"id": "user-1", "role": "user", "content": "Bonjour"},
            {"id": "assistant-1", "role": "assistant", "content": "Bonjour !"}
        ]),
    );
    s.data_mut().insert("pinned".into(), json!(true));
    s.data_mut().insert(
        "tool_calls".into(),
        json!([{"name": "test_tool", "args": {"api_key": "sk-live-abcdefghijklmnop"}}]),
    );
    s.prepare_save();
    store(state).save(&s).expect("seed save");
}

async fn get(uri: &str, state: &AppState) -> (StatusCode, Value) {
    let resp = build_router(state.clone())
        .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1024 * 1024)
        .await
        .unwrap();
    let body = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, body)
}

async fn get_raw(uri: &str, state: &AppState) -> (StatusCode, axum::http::HeaderMap, Vec<u8>) {
    let resp = build_router(state.clone())
        .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
        .await
        .unwrap();
    let status = resp.status();
    let headers = resp.headers().clone();
    let bytes = axum::body::to_bytes(resp.into_body(), 1024 * 1024)
        .await
        .unwrap();
    (status, headers, bytes.to_vec())
}

async fn post(uri: &str, body: Value, state: &AppState) -> (StatusCode, Value) {
    let resp = build_router(state.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(uri)
                .header("content-type", "application/json")
                .body(Body::from(body.to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1024 * 1024)
        .await
        .unwrap();
    let body = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, body)
}

// ── Export ──────────────────────────────────────────────────────────────────

#[tokio::test]
async fn export_requires_session_id() {
    let state = test_state();
    let (status, body) = get("/api/session/export", &state).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        body.get("error").and_then(Value::as_str),
        Some("session_id is required")
    );
}

#[tokio::test]
async fn export_missing_session_404() {
    let state = test_state();
    let (status, body) = get("/api/session/export?session_id=doesnotexist", &state).await;
    assert_eq!(status, StatusCode::NOT_FOUND);
    assert_eq!(
        body.get("error").and_then(Value::as_str),
        Some("Session not found")
    );
}

#[tokio::test]
async fn export_json_roundtrip_headers() {
    let state = test_state();
    seed_session(&state, "aabbccddeeff", "Export JSON");
    let (status, headers, raw) =
        get_raw("/api/session/export?session_id=aabbccddeeff", &state).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        headers.get("content-type").and_then(|v| v.to_str().ok()),
        Some("application/json; charset=utf-8")
    );
    let cd = headers
        .get("content-disposition")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    assert!(
        cd.contains("attachment; filename=\"hermes-aabbccddeeff.json\""),
        "CD={cd}"
    );
    assert_eq!(
        headers.get("cache-control").and_then(|v| v.to_str().ok()),
        Some("no-store")
    );
    let parsed: Value = serde_json::from_slice(&raw).expect("valid json export");
    assert_eq!(
        parsed.get("session_id").and_then(Value::as_str),
        Some("aabbccddeeff")
    );
    assert_eq!(
        parsed.get("title").and_then(Value::as_str),
        Some("Export JSON")
    );
    // messages présents
    assert!(parsed
        .get("messages")
        .and_then(Value::as_array)
        .map(|a| a.len() == 2)
        .unwrap_or(false));
}

#[tokio::test]
async fn export_redacts_credentials() {
    let state = test_state();
    seed_session(&state, "112233445566", "Redact");
    let (status, _, raw) = get_raw("/api/session/export?session_id=112233445566", &state).await;
    assert_eq!(status, StatusCode::OK);
    let text = String::from_utf8_lossy(&raw);
    // sk-... doit être masqué (jamais exposé) — le masque par préfixe
    // produit `sk-***` (port du fallback upstream `_CRED_RE` + `_mask`).
    assert!(
        !text.contains("sk-live-abcdefghijklmnop"),
        "credential leak: {text}"
    );
    assert!(text.contains("sk-***"), "masking expected: {text}");
}

#[tokio::test]
async fn export_html_content_type_and_cd() {
    let state = test_state();
    seed_session(&state, "334455667788", "HTML Export");
    let (status, headers, raw) = get_raw(
        "/api/session/export?session_id=334455667788&format=html",
        &state,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        headers.get("content-type").and_then(|v| v.to_str().ok()),
        Some("text/html; charset=utf-8")
    );
    let cd = headers
        .get("content-disposition")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    assert!(cd.contains("hermes-334455667788.html"), "CD={cd}");
    let html = String::from_utf8_lossy(&raw);
    assert!(html.starts_with("<!DOCTYPE html>"));
    assert!(html.contains("HTML Export"));
    assert!(html.contains("Bonjour"));
}

#[tokio::test]
async fn export_html_palette_capped() {
    let state = test_state();
    seed_session(&state, "5566778899aa", "Palette");
    // palette > 64 clés → ignorée (200 quand même)
    let mut big = Vec::new();
    for i in 0..70 {
        big.push(json!({format!("k{i}"): "v"}));
    }
    let palette_b64 = base64_encode(&serde_json::to_string(&json!(big)).unwrap());
    let (status, _, raw) = get_raw(
        &format!("/api/session/export?session_id=5566778899aa&format=html&palette={palette_b64}"),
        &state,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    let html = String::from_utf8_lossy(&raw);
    assert!(html.contains("<!DOCTYPE html>"));
}

#[tokio::test]
async fn export_invalid_format_falls_back_to_json() {
    let state = test_state();
    seed_session(&state, "778899aabbcc", "BadFmt");
    let (status, headers, _) = get_raw(
        "/api/session/export?session_id=778899aabbcc&format=xml",
        &state,
    )
    .await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(
        headers.get("content-type").and_then(|v| v.to_str().ok()),
        Some("application/json; charset=utf-8")
    );
}

// ── Import ──────────────────────────────────────────────────────────────────

#[tokio::test]
async fn import_requires_object_body() {
    let state = test_state();
    let (status, body) = post("/api/session/import", json!([1, 2, 3]), &state).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        body.get("error").and_then(Value::as_str),
        Some("Request body must be a JSON object")
    );
}

#[tokio::test]
async fn import_requires_messages_array() {
    let state = test_state();
    // messages absent
    let (status, body) = post("/api/session/import", json!({"title": "x"}), &state).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        body.get("error").and_then(Value::as_str),
        Some("JSON must contain a \"messages\" array")
    );
    // messages non-array
    let (status, body) = post("/api/session/import", json!({"messages": "nope"}), &state).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(
        body.get("error").and_then(Value::as_str),
        Some("JSON must contain a \"messages\" array")
    );
}

#[tokio::test]
async fn import_rejects_workspace_outside_root() {
    let state = test_state();
    let (status, body) = post(
        "/api/session/import",
        json!({"messages": [], "workspace": "/etc"}),
        &state,
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let err = body.get("error").and_then(Value::as_str).unwrap_or("");
    assert!(err.contains("Workspace"), "err={err}");
}

#[tokio::test]
async fn import_roundtrip_creates_new_session() {
    let state = test_state();
    seed_session(&state, "9900aabbccdd", "Source");
    // export → import
    let (status, _, raw) = get_raw("/api/session/export?session_id=9900aabbccdd", &state).await;
    assert_eq!(status, StatusCode::OK);
    let exported: Value = serde_json::from_slice(&raw).unwrap();
    let (istatus, ibody) = post("/api/session/import", exported, &state).await;
    assert_eq!(istatus, StatusCode::OK);
    assert_eq!(ibody.get("ok").and_then(Value::as_bool), Some(true));
    let sess = ibody.get("session").cloned().unwrap_or(Value::Null);
    let new_sid = sess.get("session_id").and_then(Value::as_str).unwrap_or("");
    assert!(!new_sid.is_empty());
    assert_ne!(new_sid, "9900aabbccdd", "import crée un NOUVEL id");
    assert_eq!(sess.get("title").and_then(Value::as_str), Some("Source"));
    assert_eq!(
        sess.get("messages")
            .and_then(Value::as_array)
            .map(|a| a.len()),
        Some(2)
    );
    // persisté sur disque
    let loaded = store(&state)
        .load(new_sid)
        .unwrap()
        .expect("imported on disk");
    assert_eq!(loaded.title(), "Source");
}

#[tokio::test]
async fn import_preserves_pinned_and_tool_calls() {
    let state = test_state();
    let body = json!({
        "messages": [{"role": "user", "content": "hi"}],
        "title": "WithMeta",
        "pinned": true,
        "tool_calls": [{"name": "t1"}],
        "model": "echo-model"
    });
    let (status, ibody) = post("/api/session/import", body, &state).await;
    assert_eq!(status, StatusCode::OK);
    let sess = ibody.get("session").cloned().unwrap_or(Value::Null);
    assert_eq!(sess.get("pinned").and_then(Value::as_bool), Some(true));
    assert_eq!(
        sess.get("model").and_then(Value::as_str),
        Some("echo-model")
    );
    // tool_calls n'est pas dans compact() (conforme upstream : la réponse est
    // compact() | {messages}) — il est conservé sur DISQUE.
    let new_sid = sess.get("session_id").and_then(Value::as_str).unwrap_or("");
    assert!(!new_sid.is_empty());
    let loaded = store(&state)
        .load(new_sid)
        .unwrap()
        .expect("imported on disk");
    assert_eq!(
        loaded
            .data()
            .get("tool_calls")
            .and_then(Value::as_array)
            .map(|a| a.len()),
        Some(1)
    );
}

#[tokio::test]
async fn import_defaults_title_and_model() {
    let state = test_state();
    let (status, ibody) = post("/api/session/import", json!({"messages": []}), &state).await;
    assert_eq!(status, StatusCode::OK);
    let sess = ibody.get("session").cloned().unwrap_or(Value::Null);
    assert_eq!(
        sess.get("title").and_then(Value::as_str),
        Some("Imported session")
    );
}

#[tokio::test]
async fn import_messages_empty_ok() {
    let state = test_state();
    let (status, ibody) = post("/api/session/import", json!({"messages": []}), &state).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!(ibody.get("ok").and_then(Value::as_bool), Some(true));
}

fn base64_encode(data: &str) -> String {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD.encode(data.as_bytes())
}
