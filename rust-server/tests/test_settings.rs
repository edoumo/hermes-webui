//! Unit tests for `/api/settings` (GET defaults + POST persistence).

use axum::body::Body;
use axum::http::{header, Request, StatusCode};
use serde_json::{json, Value};
use tower::ServiceExt;

use hermes_webui_rust::app::build_router;
use hermes_webui_rust::config::Config;
use hermes_webui_rust::state::AppState;

fn test_state() -> AppState {
    let repo_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("repo dir");
    let state_dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-test-{}-{}",
        std::process::id(),
        // Unique per test: the test name is the last #[test] attribute path.
        std::thread::current()
            .name()
            .unwrap_or("unnamed")
            .replace("::", "-")
    ));
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

#[tokio::test]
async fn settings_get_returns_defaults_without_secrets() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(resp.headers()[header::CONTENT_TYPE], "application/json");
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["theme"], "dark");
    assert_eq!(v["language"], "en");
    assert_eq!(v["bot_name"], "Hermes");
    assert_eq!(v["send_key"], "enter");
    assert_eq!(v["auth_enabled"], false);
    assert_eq!(v["password_auth_enabled"], false);
    assert_eq!(v["webui_version"], "exp-v0.52.192");
    assert!(
        v.get("password_hash").is_none(),
        "password_hash must never be exposed"
    );
    assert!(v["max_tokens"].is_null());
}

#[tokio::test]
async fn settings_post_persists_and_returns_merged() {
    let app = build_router(test_state());
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/settings")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(
                    json!({"theme": "light", "bot_name": "  Diane  ", "language": "fr"})
                        .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["theme"], "light");
    assert_eq!(v["bot_name"], "Diane", "bot_name must be stripped");
    assert_eq!(v["language"], "fr");

    // A second GET must reflect the persisted values.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["theme"], "light");
    assert_eq!(v["bot_name"], "Diane");
}

#[tokio::test]
async fn settings_post_password_change_refused_409() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/settings")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({"_set_password": "hunter2"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::CONFLICT);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert!(v["error"]
        .as_str()
        .unwrap()
        .contains("HERMES_WEBUI_PASSWORD"));
}

#[tokio::test]
async fn settings_post_control_keys_not_persisted() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/settings")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(
                    json!({"theme": "system", "max_tokens": 9999, "_current_password": "x"})
                        .to_string(),
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["theme"], "system");
    assert!(
        v["max_tokens"].is_null(),
        "max_tokens must be null, not 9999"
    );
}
