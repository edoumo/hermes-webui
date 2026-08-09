//! Unit tests for static asset serving (sandbox, MIME, ETag/304, gzip).

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
async fn static_serves_favicon_with_mime() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/static/favicon.ico")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(resp.headers()[header::CONTENT_TYPE], "image/x-icon");
    assert!(resp.headers()[header::ETAG]
        .to_str()
        .unwrap()
        .starts_with("W/"));
    assert!(resp.headers()[header::CACHE_CONTROL]
        .to_str()
        .unwrap()
        .contains("max-age=300"));
}

#[tokio::test]
async fn static_js_gets_charset_and_gzip() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/static/boot.js")
                .header(header::ACCEPT_ENCODING, "gzip")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert_eq!(
        resp.headers()[header::CONTENT_TYPE],
        "application/javascript; charset=utf-8"
    );
    assert_eq!(resp.headers()[header::CONTENT_ENCODING], "gzip");
    assert_eq!(resp.headers()[header::VARY], "Accept-Encoding");
}

#[tokio::test]
async fn static_etag_returns_304() {
    let app = build_router(test_state());
    let first = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/static/favicon.ico")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let etag = first.headers()[header::ETAG].to_str().unwrap().to_string();

    let resp = app
        .oneshot(
            Request::builder()
                .uri("/static/favicon.ico")
                .header(header::IF_NONE_MATCH, etag)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_MODIFIED);
}

#[tokio::test]
async fn static_traversal_is_404() {
    let app = build_router(test_state());
    for uri in [
        "/static/../config.py",
        "/static/%2e%2e/config.py",
        "/static/..%2fconfig.py",
    ] {
        let resp = app
            .clone()
            .oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND, "uri={uri}");
    }
}

#[tokio::test]
async fn static_missing_file_is_404_json() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/static/definitely-not-here.js")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["error"], "not found");
}

/// The in-memory static cache (port of upstream `_STATIC_CACHE`) must populate
/// on the first request and serve correct ETag/gzip/body on subsequent hits
/// without re-reading the disk. Keyed by absolute path, invalidated by
/// (size, mtime_ns).
#[tokio::test]
async fn static_cache_populates_and_serves_correct_hits() {
    let state = test_state();
    let static_root = state.config.static_dir();
    // Deterministic, compressible file that exists in the static/ tree.
    let abs = static_root.join("boot.js").canonicalize().unwrap();

    // First request must populate the cache for boot.js.
    let app = build_router(state.clone());
    let first = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/static/boot.js")
                .header(header::ACCEPT_ENCODING, "gzip")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(first.status(), StatusCode::OK);
    assert_eq!(first.headers()[header::CONTENT_ENCODING], "gzip");
    let first_etag = first.headers()[header::ETAG].to_str().unwrap().to_string();
    let first_body = axum::body::to_bytes(first.into_body(), 1 << 20)
        .await
        .unwrap();

    // The cache must now hold a valid entry keyed by the absolute path.
    let meta = std::fs::metadata(&abs).unwrap();
    let sig = (meta.len(), mtime_ns_of(&abs));
    let entry = state.static_cache.lookup(&abs, sig);
    assert!(
        entry.is_some(),
        "cache should be populated after first request"
    );
    let entry = entry.unwrap();
    assert_eq!(entry.etag, first_etag);
    assert!(
        entry.gz.is_some(),
        "boot.js (>1024B, compressible) must be gzip-cached"
    );
    assert_eq!(
        entry.raw.len(),
        std::fs::metadata(&abs).unwrap().len() as usize,
        "cached raw must match on-disk size"
    );

    // A second request (fresh cache hit) must still return the correct
    // headers/body: same ETag, gzip encoding, identical payload.
    let second = app
        .oneshot(
            Request::builder()
                .uri("/static/boot.js")
                .header(header::ACCEPT_ENCODING, "gzip")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(second.status(), StatusCode::OK);
    assert_eq!(second.headers()[header::ETAG], first_etag);
    assert_eq!(second.headers()[header::CONTENT_ENCODING], "gzip");
    let second_body = axum::body::to_bytes(second.into_body(), 1 << 20)
        .await
        .unwrap();
    assert_eq!(
        second_body, first_body,
        "cache hit must serve identical body"
    );
    // ETag stays stable and correct for a served (non-304) hit.
    assert!(second_body.len() < std::fs::metadata(&abs).unwrap().len() as usize);
}

/// Nanosecond mtime helper (mirrors the handler's `mtime_ns`).
fn mtime_ns_of(path: &std::path::Path) -> u128 {
    std::fs::metadata(path)
        .ok()
        .and_then(|m| m.modified().ok())
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}
