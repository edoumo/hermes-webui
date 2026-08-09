//! Unit tests for the `/health` endpoint.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use tower::ServiceExt;

use hermes_webui_rust::app::build_router;
use hermes_webui_rust::config::Config;
use hermes_webui_rust::state::AppState;

fn test_state() -> AppState {
    let repo_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("repo dir");
    let state_dir =
        std::env::temp_dir().join(format!("hermes-webui-rust-test-{}", std::process::id()));
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
async fn health_returns_200_ok_shape() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["status"], "ok");
    assert_eq!(v["sessions"], 0);
    assert_eq!(v["active_streams"], 0);
    assert_eq!(v["active_runs"], 0);
    assert!(v["runs"].is_array());
    assert!(v["server_started_at"].is_number());
    assert!(v["uptime_seconds"].is_number());
    assert!(v["accept_loop"]["requests_total"].is_number());
    assert!(v["accept_loop"]["last_request_at"].is_number());
}

#[tokio::test]
async fn health_deep_adds_checks() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/health?deep=1")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert!(v["checks"].is_object());
    assert_eq!(v["checks"]["streams_lock"]["status"], "ok");
}

#[tokio::test]
async fn health_unknown_route_is_404() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/does-not-exist")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}
