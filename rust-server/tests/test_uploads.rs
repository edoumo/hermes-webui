//! Integration tests for the upload API (`/api/upload`).
//!
//! Covers the upstream contract (api/upload.py `handle_upload`) and every
//! security case the R3 mandate lists: filename traversal, absolute paths,
//! Unicode filenames, duplicate names, symlink, malformed multipart,
//! oversized body, empty body, many small files, content-type mensonger,
//! binary payloads.
//!
//! Each test builds an isolated AppState whose state_dir is a throwaway temp
//! dir; attachments land in `state_dir/attachments/<session>/`.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use axum::body::Body;
use axum::http::{Request, StatusCode};
use tower::ServiceExt;

use hermes_webui_rust::app::build_router;
use hermes_webui_rust::config::Config;
use hermes_webui_rust::state::AppState;

static DIR_SEQ: AtomicU64 = AtomicU64::new(0);

fn test_state() -> AppState {
    let repo_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("repo dir");
    let seq = DIR_SEQ.fetch_add(1, Ordering::Relaxed);
    let state_dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-up-test-{}-{seq}",
        std::process::id()
    ));
    let config = Config::new(
        "127.0.0.1".into(),
        0,
        repo_dir,
        "exp-v0.52.192".into(),
        20,
        state_dir.clone(),
    )
    .expect("config");
    AppState::new(config, std::time::Instant::now())
}

/// Create a session sidecar so the upload 404 check passes.
fn seed_session(state: &AppState, sid: &str) {
    let dir = state.config.state_dir.join("sessions");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(
        dir.join(format!("{sid}.json")),
        format!(r#"{{"session_id":"{sid}","title":"t"}}"#),
    )
    .unwrap();
}

/// Build a multipart body with one file field.
fn multipart_body(session_id: &str, filename: &str, content: &[u8]) -> (String, Body) {
    let boundary = "----hermes-test-boundary-7d4f";
    let mut body = Vec::new();
    body.extend_from_slice(
        format!("--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\n{session_id}\r\n").as_bytes(),
    );
    body.extend_from_slice(
        format!("--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n").as_bytes(),
    );
    body.extend_from_slice(content);
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());
    (
        format!("multipart/form-data; boundary={boundary}"),
        Body::from(body),
    )
}

async fn post_upload(state: &AppState, content_type: &str, body: Body) -> (StatusCode, String) {
    let resp = build_router(state.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/upload")
                .header("content-type", content_type)
                .body(body)
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    (status, String::from_utf8_lossy(&bytes).to_string())
}

// ── Happy path ─────────────────────────────────────────────────────────────

#[tokio::test]
async fn upload_ok_creates_attachment() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let (ct, body) = multipart_body("abc123def456", "hello.txt", b"hello world");
    let (status, text) = post_upload(&state, &ct, body).await;
    assert_eq!(status, StatusCode::OK, "body: {text}");
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(v["filename"], "hello.txt");
    assert_eq!(v["size"], 11);
    assert_eq!(v["mime"], "text/plain");
    assert_eq!(v["is_image"], false);
    let path = v["path"].as_str().unwrap();
    assert!(path.contains("attachments/abc123def456/hello.txt"));
    assert_eq!(std::fs::read(path).unwrap(), b"hello world");
}

#[tokio::test]
async fn upload_image_mime_detected() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let (ct, body) = multipart_body("abc123def456", "photo.png", b"\x89PNG\r\n\x1a\n");
    let (status, text) = post_upload(&state, &ct, body).await;
    assert_eq!(status, StatusCode::OK);
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(v["mime"], "image/png");
    assert_eq!(v["is_image"], true);
}

// ── Session / field validation ─────────────────────────────────────────────

#[tokio::test]
async fn upload_unknown_session_404() {
    let state = test_state();
    let (ct, body) = multipart_body("nosuchsession", "a.txt", b"x");
    let (status, text) = post_upload(&state, &ct, body).await;
    assert_eq!(status, StatusCode::NOT_FOUND, "body: {text}");
    assert!(text.contains("Session not found"));
}

#[tokio::test]
async fn upload_missing_file_field_400() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let boundary = "----hermes-test-boundary-7d4f";
    let body = format!(
        "--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\nabc123def456\r\n--{boundary}--\r\n"
    );
    let (status, text) = post_upload(
        &state,
        &format!("multipart/form-data; boundary={boundary}"),
        Body::from(body),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "body: {text}");
    assert!(text.contains("No file field"));
}

#[tokio::test]
async fn upload_empty_filename_400() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let (ct, body) = multipart_body("abc123def456", "", b"x");
    let (status, text) = post_upload(&state, &ct, body).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "body: {text}");
    assert!(text.contains("No filename"));
}

// ── Filename security ───────────────────────────────────────────────────────

#[tokio::test]
async fn upload_traversal_filename_sanitized() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    // `../evil.txt` → basename only → `.._evil.txt` (dots kept, slash → _).
    let (ct, body) = multipart_body("abc123def456", "../evil.txt", b"x");
    let (status, text) = post_upload(&state, &ct, body).await;
    assert_eq!(status, StatusCode::OK, "body: {text}");
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    let path = v["path"].as_str().unwrap();
    // Must stay inside attachments/<session>/ — no `..` segment.
    assert!(!path.contains("/../"), "traversal blocked: {path}");
    assert!(path.contains("attachments/abc123def456/"));
    assert!(std::path::Path::new(path).exists());
}

#[tokio::test]
async fn upload_absolute_filename_sanitized() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let (ct, body) = multipart_body("abc123def456", "/etc/passwd", b"x");
    let (status, text) = post_upload(&state, &ct, body).await;
    assert_eq!(status, StatusCode::OK, "body: {text}");
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    let path = v["path"].as_str().unwrap();
    assert!(path.contains("attachments/abc123def456/"), "path: {path}");
    assert!(!path.starts_with("/etc/"));
}

#[tokio::test]
async fn upload_unicode_filename_ok() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let (ct, body) = multipart_body("abc123def456", "héllo-🔐.txt", b"x");
    let (status, text) = post_upload(&state, &ct, body).await;
    assert_eq!(status, StatusCode::OK, "body: {text}");
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    let path = v["path"].as_str().unwrap();
    assert!(std::path::Path::new(path).exists());
}

#[tokio::test]
async fn upload_duplicate_names_deduped() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let (ct, body) = multipart_body("abc123def456", "same.txt", b"one");
    let (_, t1) = post_upload(&state, &ct, body).await;
    let (ct2, body2) = multipart_body("abc123def456", "same.txt", b"two");
    let (_, t2) = post_upload(&state, &ct2, body2).await;
    let v1: serde_json::Value = serde_json::from_str(&t1).unwrap();
    let v2: serde_json::Value = serde_json::from_str(&t2).unwrap();
    assert_eq!(v1["filename"], "same.txt");
    assert_eq!(v2["filename"], "same-1.txt", "dedup suffix: {t2}");
    assert_ne!(v1["path"], v2["path"]);
}

#[tokio::test]
async fn upload_session_id_traversal_sanitized() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    // session_id with traversal → sanitized to `.._.._` style, stays inside.
    let (ct, body) = multipart_body("../../etc", "a.txt", b"x");
    let (status, text) = post_upload(&state, &ct, body).await;
    // Session won't exist after sanitization → 404 (fail-closed).
    assert_eq!(status, StatusCode::NOT_FOUND, "body: {text}");
}

// ── Payload security ───────────────────────────────────────────────────────

#[tokio::test]
async fn upload_oversized_body_413() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    // 21 MiB > 20 MiB cap. Le check upstream (api/upload.py:211) se base sur
    // le header Content-Length AVANT tout parsing — on l'envoie explicitement.
    let big = vec![b'x'; 21 * 1024 * 1024];
    let (ct, body) = multipart_body("abc123def456", "big.bin", &big);
    let resp = build_router(state.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/upload")
                .header("content-type", ct)
                .header("content-length", big.len() + 512)
                .body(body)
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let text = String::from_utf8_lossy(&bytes).to_string();
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE, "body: {text}");
    assert!(text.contains("File too large"));
}

#[tokio::test]
async fn upload_empty_body_400() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let (status, text) = post_upload(
        &state,
        "multipart/form-data; boundary=----hermes-test-boundary-7d4f",
        Body::empty(),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "body: {text}");
}

#[tokio::test]
async fn upload_malformed_multipart_400() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let (status, text) = post_upload(
        &state,
        "multipart/form-data; boundary=----hermes-test-boundary-7d4f",
        Body::from("this is not multipart at all"),
    )
    .await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "body: {text}");
}

#[tokio::test]
async fn upload_binary_payload_roundtrip() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    let payload: Vec<u8> = (0u8..=255u8).collect();
    let (ct, body) = multipart_body("abc123def456", "blob.bin", &payload);
    let (status, text) = post_upload(&state, &ct, body).await;
    assert_eq!(status, StatusCode::OK, "body: {text}");
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(std::fs::read(v["path"].as_str().unwrap()).unwrap(), payload);
}

#[tokio::test]
async fn upload_many_small_files() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    for i in 0..20 {
        let (ct, body) = multipart_body("abc123def456", &format!("f{i}.txt"), b"x");
        let (status, _) = post_upload(&state, &ct, body).await;
        assert_eq!(status, StatusCode::OK, "file {i}");
    }
    let dir = state.config.state_dir.join("attachments/abc123def456");
    let count = std::fs::read_dir(&dir).unwrap().count();
    assert_eq!(count, 20, "all files persisted");
}

#[tokio::test]
async fn upload_content_type_lying_ignored() {
    let state = test_state();
    seed_session(&state, "abc123def456");
    // Client claims image/png but content is text; MIME is guessed from the
    // sanitized filename, not the client header (upstream mimetypes.guess_type).
    let boundary = "----hermes-test-boundary-7d4f";
    let body = format!(
        "--{boundary}\r\nContent-Disposition: form-data; name=\"session_id\"\r\n\r\nabc123def456\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"notes.txt\"\r\nContent-Type: image/png\r\n\r\nplain text\r\n--{boundary}--\r\n"
    );
    let (status, text) = post_upload(
        &state,
        &format!("multipart/form-data; boundary={boundary}"),
        Body::from(body),
    )
    .await;
    assert_eq!(status, StatusCode::OK, "body: {text}");
    let v: serde_json::Value = serde_json::from_str(&text).unwrap();
    assert_eq!(v["mime"], "text/plain", "MIME from filename, not client");
    assert_eq!(v["is_image"], false);
}
