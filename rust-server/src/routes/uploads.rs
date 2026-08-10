//! `/api/upload` — faithful port of the upstream attachment upload handler
//! (`api/upload.py` `handle_upload`, route `/api/upload` in `api/routes.py`).
//!
//! Contract (verified against upstream HEAD bd91b649):
//! - multipart form with `session_id` field + `file` field;
//! - `Content-Length` > `MAX_UPLOAD_BYTES` (20 MiB) → 413;
//! - missing `file` field → 400; empty filename → 400;
//! - unknown session → 404;
//! - filename sanitized: `[^\w.\-]` → `_`, basename only, max 200 chars;
//! - destination: `STATE_DIR/attachments/<sanitized_session_id>/<safe_name>`,
//!   dedup with `-1`, `-2`, … suffix (bounded 1000);
//! - response: `{filename, path, size, mime, is_image}`.
//!
//! Security: never trust the client filename; session id sanitized the same
//! way; destination re-checked with `is_relative_to` (no traversal, no
//! symlink escape); oversized body rejected before parsing.

use std::path::{Path, PathBuf};

use axum::extract::{Multipart, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;

use crate::sessions::Store;
use crate::state::AppState;

/// Upstream `MAX_UPLOAD_BYTES` (api/config.py, 20 MiB default).
pub const MAX_UPLOAD_BYTES: u64 = 20 * 1024 * 1024;

/// Upstream `_sanitize_upload_name` (api/upload.py:105): keep `\w . -`,
/// basename only, cap 200 chars.
fn sanitize_upload_name(filename: &str) -> Option<String> {
    let base = Path::new(filename)
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let mut safe = String::with_capacity(base.len());
    for c in base.chars() {
        if c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-' {
            safe.push(c);
        } else {
            safe.push('_');
        }
    }
    safe.truncate(200);
    if safe.is_empty() || safe.trim_matches('.').is_empty() {
        return None;
    }
    Some(safe)
}

/// Upstream `_session_attachment_dir` (api/upload.py:144): sanitize the
/// session id the same way, cap 120 chars, resolve under the root.
fn session_attachment_dir(root: &Path, session_id: &str) -> PathBuf {
    let mut safe = String::with_capacity(session_id.len());
    for c in session_id.chars() {
        if c.is_ascii_alphanumeric() || c == '_' || c == '.' || c == '-' {
            safe.push(c);
        } else {
            safe.push('_');
        }
    }
    safe.truncate(120);
    if safe.is_empty() {
        safe = "session".to_string();
    }
    root.join(safe)
}

/// Upstream `_upload_destination` (api/upload.py:125): dedup with `-N`
/// suffix, bounded 1000, re-check containment.
fn upload_destination(root: &Path, session_id: &str, safe_name: &str) -> Result<PathBuf, String> {
    let dest_dir = session_attachment_dir(root, session_id);
    std::fs::create_dir_all(&dest_dir).map_err(|e| format!("cannot create attachment dir: {e}"))?;
    let dest_dir = dest_dir.canonicalize().unwrap_or_else(|_| dest_dir.clone());
    let dest = dest_dir.join(safe_name);
    if !dest.starts_with(&dest_dir) {
        return Err("Invalid upload destination".into());
    }
    if !dest.exists() {
        return Ok(dest);
    }
    let stem = dest
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let suffix = dest
        .extension()
        .map(|s| format!(".{}", s.to_string_lossy()))
        .unwrap_or_default();
    for idx in 1..1000u32 {
        let candidate = dest_dir.join(format!("{stem}-{idx}{suffix}"));
        if !candidate.starts_with(&dest_dir) {
            return Err("Invalid upload destination".into());
        }
        if !candidate.exists() {
            return Ok(candidate);
        }
    }
    Err("Too many uploads with the same filename".into())
}

/// POST /api/upload — multipart upload handler (upstream `handle_upload`).
pub async fn handle_upload(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
    mut multipart: Multipart,
) -> Response {
    // Content-Length check first (upstream api/upload.py:211): reject
    // oversized bodies before any parsing.
    if let Some(len) = headers
        .get(axum::http::header::CONTENT_LENGTH)
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.parse::<u64>().ok())
    {
        if len > MAX_UPLOAD_BYTES {
            return error_response(
                StatusCode::PAYLOAD_TOO_LARGE,
                &format!("File too large (max {}MB)", MAX_UPLOAD_BYTES / 1024 / 1024),
            );
        }
    }

    let mut session_id: Option<String> = None;
    let mut filename: Option<String> = None;
    let mut file_bytes: Option<Vec<u8>> = None;

    while let Ok(Some(field)) = multipart.next_field().await {
        let name = field.name().unwrap_or("").to_string();
        match name.as_str() {
            "session_id" => {
                if let Ok(v) = field.text().await {
                    session_id = Some(v.trim().to_string());
                }
            }
            "file" => {
                filename = field.file_name().map(|s| s.to_string());
                match field.bytes().await {
                    Ok(bytes) => {
                        if bytes.len() as u64 > MAX_UPLOAD_BYTES {
                            return error_response(
                                StatusCode::PAYLOAD_TOO_LARGE,
                                &format!(
                                    "File too large (max {}MB)",
                                    MAX_UPLOAD_BYTES / 1024 / 1024
                                ),
                            );
                        }
                        file_bytes = Some(bytes.to_vec());
                    }
                    Err(e) => {
                        return error_response(
                            StatusCode::BAD_REQUEST,
                            &format!("malformed multipart: {e}"),
                        );
                    }
                }
            }
            _ => {}
        }
    }

    let session_id = session_id.unwrap_or_default();
    // Upstream order (api/upload.py:215-219): missing file field first,
    // then empty filename.
    let file_bytes = match file_bytes {
        Some(b) => b,
        None => return error_response(StatusCode::BAD_REQUEST, "No file field in request"),
    };
    let filename = match filename {
        Some(f) if !f.is_empty() => f,
        _ => return error_response(StatusCode::BAD_REQUEST, "No filename in upload"),
    };

    // Session must exist (upstream get_session → 404).
    let store = Store::new(state.config.state_dir.clone());
    let session_exists = match store.load(&session_id) {
        Ok(Some(_)) => true,
        Ok(None) => false,
        Err(_) => false,
    };
    if !session_exists {
        return error_response(StatusCode::NOT_FOUND, "Session not found");
    }

    let safe_name = match sanitize_upload_name(&filename) {
        Some(n) => n,
        None => return error_response(StatusCode::BAD_REQUEST, "Invalid filename"),
    };

    let root = state.config.state_dir.join("attachments");
    let dest = match upload_destination(&root, &session_id, &safe_name) {
        Ok(d) => d,
        Err(e) => return error_response(StatusCode::BAD_REQUEST, &e),
    };

    if let Err(e) = std::fs::write(&dest, &file_bytes) {
        return error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            &format!("Upload failed: {e}"),
        );
    }

    let mime = mime_guess(&safe_name);
    let is_image = mime.starts_with("image/");
    let size = std::fs::metadata(&dest).map(|m| m.len()).unwrap_or(0);

    (
        StatusCode::OK,
        Json(json!({
            "filename": dest.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default(),
            "path": dest.to_string_lossy(),
            "size": size,
            "mime": mime,
            "is_image": is_image,
        })),
    )
        .into_response()
}

/// Minimal MIME guess (upstream `mimetypes.guess_type` fallback).
fn mime_guess(filename: &str) -> String {
    let lower = filename.to_lowercase();
    let mime = if lower.ends_with(".png") {
        "image/png"
    } else if lower.ends_with(".jpg") || lower.ends_with(".jpeg") {
        "image/jpeg"
    } else if lower.ends_with(".gif") {
        "image/gif"
    } else if lower.ends_with(".webp") {
        "image/webp"
    } else if lower.ends_with(".svg") {
        "image/svg+xml"
    } else if lower.ends_with(".pdf") {
        "application/pdf"
    } else if lower.ends_with(".txt") || lower.ends_with(".md") {
        "text/plain"
    } else if lower.ends_with(".json") {
        "application/json"
    } else if lower.ends_with(".zip") {
        "application/zip"
    } else if lower.ends_with(".tar") {
        "application/x-tar"
    } else if lower.ends_with(".gz") {
        "application/gzip"
    } else {
        "application/octet-stream"
    };
    mime.to_string()
}

fn error_response(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({ "error": message }))).into_response()
}

/// Router for upload routes.
pub fn router() -> axum::Router<AppState> {
    axum::Router::new().route("/api/upload", axum::routing::post(handle_upload))
}
