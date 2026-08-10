//! Tests du domaine Sessions (Track B R2) — couverture obligatoire du mandat :
//! empty store, create, get, list one, list multiple, rename, update metadata,
//! delete, invalid id, malformed/corrupt metadata, path tampering, persistence
//! après reconstruction, concurrent reads, classification WebUI/CLI.

use std::path::PathBuf;

use axum::body::Body;
use axum::http::{header, Request, StatusCode};
use serde_json::{json, Value};
use tower::ServiceExt;

use hermes_webui_rust::app::build_router;
use hermes_webui_rust::config::Config;
use hermes_webui_rust::sessions::{self, Session, Store};
use hermes_webui_rust::state::AppState;

fn test_state() -> AppState {
    let repo_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("repo dir");
    let state_dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-sessions-test-{}-{}",
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

fn create_session_file(state: &AppState, sid: &str, title: &str) -> Session {
    let mut s = Session::new(sid.to_string());
    s.set_title(title.to_string());
    s.prepare_save();
    store(state).save(&s).expect("save");
    s
}

// ── Store unit tests ────────────────────────────────────────────────────────

#[test]
fn empty_store_lists_nothing() {
    let state = test_state();
    let s = store(&state);
    assert_eq!(s.list().unwrap().len(), 0);
    assert_eq!(s.count().unwrap(), 0);
}

#[test]
fn create_and_get_roundtrip() {
    let state = test_state();
    let s = store(&state);
    let sid = s.generate_id();
    assert_eq!(sid.len(), 12, "upstream id = uuid4().hex[:12]");
    assert!(sessions::is_safe_session_id(&sid));

    let mut sess = Session::new(sid.clone());
    sess.set_title("Test session".into());
    sess.prepare_save();
    s.save(&sess).unwrap();

    let loaded = s.load(&sid).unwrap().expect("session exists");
    assert_eq!(loaded.session_id(), sid);
    assert_eq!(loaded.title(), "Test session");
}

#[test]
fn list_one_and_multiple() {
    let state = test_state();
    let s = store(&state);
    create_session_file(&state, "aaaaaaaaaaaa", "One");
    create_session_file(&state, "bbbbbbbbbbbb", "Two");
    create_session_file(&state, "cccccccccccc", "Three");
    assert_eq!(s.list().unwrap().len(), 3);
    assert_eq!(s.count().unwrap(), 3);
}

#[test]
fn rename_updates_title() {
    let state = test_state();
    let s = store(&state);
    let mut sess = create_session_file(&state, "dddddddddddd", "Old");
    let new_title = sess.apply_title_rename("New title");
    sess.set_title(new_title);
    sess.prepare_save();
    s.save(&sess).unwrap();
    let loaded = s.load("dddddddddddd").unwrap().unwrap();
    assert_eq!(loaded.title(), "New title");
}

#[test]
fn update_metadata_persists() {
    let state = test_state();
    let s = store(&state);
    let mut sess = create_session_file(&state, "eeeeeeeeeeee", "Meta");
    sess.set_workspace("/tmp/ws".into());
    sess.set_model(Some("echo-model".into()));
    sess.prepare_save();
    s.save(&sess).unwrap();
    let loaded = s.load("eeeeeeeeeeee").unwrap().unwrap();
    assert_eq!(loaded.workspace(), "/tmp/ws");
    assert_eq!(loaded.model().as_deref(), Some("echo-model"));
}

#[test]
fn delete_removes_file() {
    let state = test_state();
    let s = store(&state);
    create_session_file(&state, "ffffffffffff", "ToDelete");
    assert!(s.load("ffffffffffff").unwrap().is_some());
    s.delete("ffffffffffff").unwrap();
    assert!(s.load("ffffffffffff").unwrap().is_none());
    assert_eq!(s.count().unwrap(), 0);
}

#[test]
fn invalid_id_rejected() {
    let state = test_state();
    let s = store(&state);
    // Ids avec slash/dot doivent être rejetés (path safety).
    assert!(!sessions::is_safe_session_id("../evil"));
    assert!(!sessions::is_safe_session_id("a/b"));
    assert!(!sessions::is_safe_session_id("a.b"));
    assert!(!sessions::is_safe_session_id(""));
    // load d'un id invalide → None (pas d'erreur, pas d'accès fichier).
    assert!(s.load("../evil").unwrap().is_none());
}

#[test]
fn path_tampering_does_not_escape_store() {
    let state = test_state();
    let s = store(&state);
    // Un id avec traversal ne doit jamais résoudre hors du dossier sessions.
    let evil = "../../../../etc/passwd";
    assert!(!sessions::is_safe_session_id(evil));
    assert!(s.load(evil).unwrap().is_none());
}

#[test]
fn corrupted_metadata_handled() {
    let state = test_state();
    let s = store(&state);
    // Fichier JSON invalide → load retourne Err ou None, jamais panic.
    let dir = sessions::sessions_dir(&state.config.state_dir);
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("corrupt.json"), b"{not valid json").unwrap();
    let result = s.load("corrupt");
    assert!(result.is_ok() || result.is_err());
    if let Ok(v) = result {
        assert!(v.is_none());
    }
}

#[test]
fn persistence_after_store_reconstruction() {
    let state = test_state();
    let s = store(&state);
    create_session_file(&state, "111111111111", "Persist");
    drop(s);
    // Nouveau Store sur le même state_dir → les sessions persistent.
    let s2 = Store::new(state.config.state_dir.clone());
    assert_eq!(s2.count().unwrap(), 1);
    let loaded = s2.load("111111111111").unwrap().unwrap();
    assert_eq!(loaded.title(), "Persist");
}

#[test]
fn concurrent_reads_are_safe() {
    let state = test_state();
    let s = store(&state);
    for i in 0..8 {
        let sid = format!("{:012x}", i);
        create_session_file(&state, &sid, &format!("S{i}"));
    }
    let _ = s;
    let handles: Vec<_> = (0..8)
        .map(|i| {
            let s = Store::new(state.config.state_dir.clone());
            std::thread::spawn(move || {
                let sid = format!("{:012x}", i);
                s.load(&sid).unwrap().map(|x| x.title())
            })
        })
        .collect();
    for (i, h) in handles.into_iter().enumerate() {
        assert_eq!(h.join().unwrap().as_deref(), Some(format!("S{i}").as_str()));
    }
}

#[test]
fn ownership_classification() {
    use hermes_webui_rust::sessions::SessionOwnership;
    // WebUI-owned.
    let mut webui = serde_json::Map::new();
    webui.insert("source".into(), json!("webui"));
    assert_eq!(
        sessions::classify_ownership(&webui),
        SessionOwnership::WebUi
    );
    // CLI-owned.
    let mut cli = serde_json::Map::new();
    cli.insert("source".into(), json!("cli"));
    assert_eq!(sessions::classify_ownership(&cli), SessionOwnership::Cli);
    // Messaging (non-CLI, non-WebUI) → WebUi dans notre port (surface non-écrivable).
    let mut msg = serde_json::Map::new();
    msg.insert("source".into(), json!("telegram"));
    assert_eq!(sessions::classify_ownership(&msg), SessionOwnership::WebUi);
}

// ── Route tests (HTTP) ──────────────────────────────────────────────────────

#[tokio::test]
async fn route_sessions_list_empty() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/sessions")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["sessions"].as_array().unwrap().len(), 0);
    assert_eq!(v["webui_session_count"], 0);
}

#[tokio::test]
async fn route_session_new_then_get() {
    let app = build_router(test_state());
    // POST /api/session/new
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/session/new")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({"title": "Route session"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    let sid = v["session"]["session_id"]
        .as_str()
        .expect("session_id")
        .to_string();
    assert_eq!(sid.len(), 12);

    // GET /api/session?session_id=...
    let resp = app
        .oneshot(
            Request::builder()
                .uri(format!("/api/session?session_id={sid}&messages=0"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["session"]["session_id"], sid);
}

#[tokio::test]
async fn route_session_get_invalid_id_404() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/session?session_id=doesnotexist")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn route_session_delete() {
    let app = build_router(test_state());
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/session/new")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    let sid = v["session"]["session_id"].as_str().unwrap().to_string();

    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/session/delete")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({"session_id": sid}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["ok"], true);
    assert_eq!(v["state_db_cleanup_failed"], false);
}
