//! Integration tests for the read-only workspace API (`/api/workspace/*`).
//!
//! Covers every security case the task mandates: path traversal (`..`, `/`,
//! backslashes, absolute paths, `~`), encoded traversal (`%2e%2e`, `..%2f`),
//! symlink escape, Unicode normalization, size limits, and non-regular files.
//!
//! Each test builds an isolated `AppState` whose `workspace_root()` resolves to
//! `state_dir/workspace` (a throwaway temp dir), so nothing outside that root is
//! reachable. Files are created there at test time.

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use axum::body::Body;
use axum::http::{header, Request, StatusCode};
use tower::ServiceExt;

use hermes_webui_rust::app::build_router;
use hermes_webui_rust::config::Config;
use hermes_webui_rust::state::AppState;

/// Unique suffix per test invocation so parallel tokio tests do not share the
/// same state_dir / workspace root (which would race on file writes).
static DIR_SEQ: AtomicU64 = AtomicU64::new(0);

/// Create an AppState whose workspace root is an isolated temp dir.
fn test_state() -> AppState {
    let repo_dir = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("repo dir");
    let seq = DIR_SEQ.fetch_add(1, Ordering::Relaxed);
    let state_dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-ws-test-{}-{seq}",
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

/// Percent-encode a query value so arbitrary bytes (null, backslash, space,
/// non-ASCII) can be placed into a URI without http::Uri rejecting them.
fn qencode(rel: &str) -> String {
    let mut out = String::new();
    for b in rel.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            b'/' => out.push('/'),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

fn ws_root(state: &AppState) -> PathBuf {
    state.config.workspace_root()
}

fn write_workspace_file(state: &AppState, rel: &str, content: &str) -> PathBuf {
    let p = ws_root(state).join(rel);
    std::fs::create_dir_all(p.parent().unwrap()).unwrap();
    std::fs::write(&p, content).unwrap();
    p
}

async fn get_body_bytes(resp: axum::response::Response) -> Vec<u8> {
    axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap()
        .to_vec()
}

async fn get_json(resp: axum::response::Response) -> serde_json::Value {
    let b = get_body_bytes(resp).await;
    serde_json::from_slice(&b).unwrap()
}

fn uri(rel: &str) -> String {
    format!("/api/workspace/list?path={rel}")
}

// ── Happy paths ─────────────────────────────────────────────────────────────

#[tokio::test]
async fn list_returns_entries() {
    let state = test_state();
    write_workspace_file(&state, "hello.txt", "hi\n");
    write_workspace_file(&state, "sub/nested.txt", "deep\n");
    let app = build_router(state.clone());
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri(uri("."))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let v = get_json(resp).await;
    assert_eq!(
        v["workspace"],
        serde_json::Value::String(ws_root(&state).to_string_lossy().into_owned())
    );
    assert_eq!(v["path"], ".");
    let entries = v["entries"].as_array().unwrap();
    assert!(entries.iter().any(|e| e["name"] == "hello.txt"));
    assert!(entries.iter().any(|e| e["name"] == "sub"));
}

#[tokio::test]
async fn list_subdirectory_uses_rel_paths() {
    let state = test_state();
    write_workspace_file(&state, "sub/nested.txt", "deep\n");
    let app = build_router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .uri(uri("sub"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let v = get_json(resp).await;
    assert_eq!(v["path"], "sub");
    assert_eq!(v["entries"][0]["name"], "nested.txt");
    assert_eq!(v["entries"][0]["path"], "sub/nested.txt");
}

#[tokio::test]
async fn read_returns_text_content() {
    let state = test_state();
    write_workspace_file(&state, "note.md", "# Title\nline2\n");
    let app = build_router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/workspace/read?path=note.md")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let v = get_json(resp).await;
    assert_eq!(v["path"], "note.md");
    assert_eq!(v["content"], "# Title\nline2\n");
    assert_eq!(v["lines"], 3);
    assert_eq!(v["size"], 14);
}

#[tokio::test]
async fn metadata_returns_type_and_size() {
    let state = test_state();
    write_workspace_file(&state, "file.txt", "abcd");
    write_workspace_file(&state, "adir/.keep", "");
    let app = build_router(state.clone());
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/workspace/metadata?path=file.txt")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let v = get_json(resp).await;
    assert_eq!(v["name"], "file.txt");
    assert_eq!(v["type"], "file");
    assert_eq!(v["is_dir"], false);
    assert_eq!(v["size"], 4);

    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/workspace/metadata?path=adir")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let v = get_json(resp).await;
    assert_eq!(v["type"], "dir");
    assert_eq!(v["is_dir"], true);
}

#[tokio::test]
async fn download_returns_bytes_with_disposition() {
    let state = test_state();
    write_workspace_file(&state, "bin.dat", "raw-bytes-123");
    let app = build_router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/workspace/download?path=bin.dat")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    assert!(resp.headers()[header::CONTENT_DISPOSITION]
        .to_str()
        .unwrap()
        .starts_with("attachment;"));
    let b = get_body_bytes(resp).await;
    assert_eq!(b, b"raw-bytes-123");
}

// ── Traversal & absolute paths ─────────────────────────────────────────────

#[tokio::test]
async fn traversal_is_blocked() {
    let state = test_state();
    write_workspace_file(&state, "secret.txt", "inside");
    let app = build_router(state.clone());
    // A file outside the workspace root to ensure traversal cannot reach it.
    let outside = std::env::temp_dir().join(format!("outside-{}", std::process::id()));
    std::fs::write(&outside, "should-not-leak").unwrap();

    let attempts = [
        "../outside",
        "../../outside",
        "..",
        "../",
        "..%2foutside",
        "%2e%2e/outside",
        "%2e%2e%2foutside",
        "sub/../../outside",
        "sub/..%2f..%2foutside",
        "/etc/passwd",
        "C:/Windows/system32",
        "~/outside",
        "..\\..\\outside",
        "sub\\..\\..\\outside",
        "//etc/passwd",
        "a/..%2f..%2foutside",
    ];
    for rel in attempts {
        // URL-encode the path so raw traversal/encoded spellings reach the
        // handler through a valid URI.
        let encoded = qencode(rel);
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/workspace/read?path={encoded}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND, "read rel={rel}");
    }
    // Same for list.
    for rel in attempts {
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/workspace/list?path={}", qencode(rel)))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND, "list rel={rel}");
    }
    // Cleanup the outside file.
    let _ = std::fs::remove_file(&outside);
}

#[tokio::test]
async fn encoded_dotdot_slashes_are_blocked() {
    let state = test_state();
    let app = build_router(state);
    for rel in [
        "..%2f",
        "%2e%2e%2f",
        "%2e%2e",
        "%252e%252e%252f", // double-encoded
        "..%2f..%2f",
        "%2e%2e/..%2f",
    ] {
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/workspace/list?path={rel}"))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND, "rel={rel}");
    }
}

// ── Symlink escape ─────────────────────────────────────────────────────────

#[tokio::test]
async fn symlink_escape_is_blocked() {
    let state = test_state();
    let root = ws_root(&state);
    std::fs::create_dir_all(&root).unwrap();

    // A real file outside the workspace.
    let outside = std::env::temp_dir().join(format!("ws-outside-{}", std::process::id()));
    std::fs::write(&outside, "top-secret").unwrap();

    // Symlink inside the workspace pointing outside.
    let link = root.join("leak.txt");
    let _ = std::fs::remove_file(&link);
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside, &link).unwrap();

    let app = build_router(state);
    // read through the escaping symlink must be blocked.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/workspace/read?path=leak.txt")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "read through escaping symlink"
    );

    // download through it too.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/workspace/download?path=leak.txt")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "download through escaping symlink"
    );

    // metadata through it.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/workspace/metadata?path=leak.txt")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "metadata through escaping symlink"
    );

    let _ = std::fs::remove_file(&outside);
    let _ = std::fs::remove_file(&link);
}

#[tokio::test]
async fn symlink_within_workspace_is_allowed() {
    let state = test_state();
    let root = ws_root(&state);
    std::fs::create_dir_all(&root).unwrap();
    write_workspace_file(&state, "target.txt", "in-workspace");
    let link = root.join("alias.txt");
    #[cfg(unix)]
    std::os::unix::fs::symlink(root.join("target.txt"), &link).unwrap();

    let app = build_router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/workspace/read?path=alias.txt")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "in-workspace symlink should resolve"
    );
    let v = get_json(resp).await;
    assert_eq!(v["content"], "in-workspace");
}

// ── Unicode normalization ──────────────────────────────────────────────────

#[tokio::test]
async fn unicode_normalized_escape_spellings_blocked() {
    let state = test_state();
    let app = build_router(state);
    // These contain the canonical-equivalent of ".." composed differently,
    // or dot components hidden behind combining characters. They must be
    // normalized (NFC) and rejected as traversal.
    for rel in [
        ". .",
        ".。",
        "a/..",
        "a\u{2044}b", // fraction slash as separator
        "a\u{2215}b", // division slash
    ] {
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/workspace/list?path={}", qencode(rel)))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND, "unicode rel={rel:?}");
    }
}

// ── Size limits & non-regular files ────────────────────────────────────────

#[tokio::test]
async fn oversized_file_is_rejected() {
    let state = test_state();
    let big = "x".repeat(500_000);
    write_workspace_file(&state, "big.txt", &big);
    let app = build_router(state);
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/workspace/read?path=big.txt")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::BAD_REQUEST,
        "oversized read must be 400"
    );

    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/workspace/download?path=big.txt")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(
        resp.status(),
        StatusCode::BAD_REQUEST,
        "oversized download must be 400"
    );
}

#[tokio::test]
async fn directory_read_is_rejected() {
    let state = test_state();
    write_workspace_file(&state, "dir/sub.txt", "x");
    let app = build_router(state);
    // Reading a directory is not a file → 404.
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/workspace/read?path=dir")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn missing_file_is_not_found() {
    let state = test_state();
    let app = build_router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/workspace/read?path=does-not-exist.txt")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    let v = get_json(resp).await;
    assert!(v["error"].is_string());
}

// ── Null bytes & drive-letter ──────────────────────────────────────────────

#[tokio::test]
async fn null_bytes_and_windows_drives_rejected() {
    let state = test_state();
    let app = build_router(state);
    for rel in [
        "a\0b",
        "C:\\Windows\\win.ini",
        "C:/Windows/win.ini",
        "\\etc\\passwd",
    ] {
        let resp = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri(format!("/api/workspace/read?path={}", qencode(rel)))
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(resp.status(), StatusCode::NOT_FOUND, "rel={rel:?}");
    }
}
