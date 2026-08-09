//! Unit tests for the app-shell (`/`, `/index.html`, `/session/<id>`).

use axum::body::Body;
use axum::http::{header, Request, StatusCode};
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
async fn index_serves_shell_with_substitutions() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(Request::builder().uri("/").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(
        resp.headers()[header::CONTENT_TYPE],
        "text/html; charset=utf-8"
    );
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let html = String::from_utf8(bytes.to_vec()).unwrap();
    // Process-constant tokens must be substituted, not left raw.
    assert!(
        !html.contains("__WEBUI_VERSION__"),
        "version token not substituted"
    );
    assert!(
        !html.contains("__MAX_UPLOAD_BYTES__"),
        "upload token not substituted"
    );
    assert!(
        !html.contains("__CSRF_TOKEN_JSON__"),
        "csrf token not substituted"
    );
    // The version token is URL-encoded (upstream quote(..., safe="")).
    assert!(html.contains("exp-v0.52.192") || html.contains("exp-v0%2E52%2E192"));
}

#[tokio::test]
async fn index_html_route_works() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/index.html")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
}

#[tokio::test]
async fn session_route_serves_shell() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/session/abc123")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(
        resp.headers()[header::CONTENT_TYPE],
        "text/html; charset=utf-8"
    );
}
