//! Read-only workspace file browser — faithful port of the read-side of the
//! upstream workspace API (`api/workspace.py` list_dir / read_file_content /
//! safe_resolve_ws, wired in `api/routes.py` `_handle_list_dir` / `_handle_file_read`).
//!
//! Contract (read-only subset):
//! - GET /api/workspace/list      ?path=<rel> → {entries, signature, path, workspace}
//! - GET /api/workspace/read      ?path=<rel> → {path, content, size, lines}
//! - GET /api/workspace/metadata  ?path=<rel> → {name, path, type, size, mtime_ns, ...}
//! - GET /api/workspace/download  ?path=<rel> → raw bytes (Content-Disposition)
//!
//! Upstream defaults the workspace to `~/workspace`; the Rust port deliberately
//! isolates to `STATE_DIR/workspace` (overridable via `HERMES_WEBUI_WORKSPACE_ROOT`
//! for tests) so the read-only browser can never touch arbitrary host paths.
//!
//! Security (absolute priority, mirrors upstream + R1 static_files sandbox):
//! - path traversal: `..`, `/`, backslashes, absolute paths, `~`, embedded null
//!   bytes are rejected lexically BEFORE touching the filesystem;
//! - encoded traversal (`%2e%2e`, `..%2f`) is neutralised by URL decoding the
//!   query parameter before the lexical gate;
//! - Unicode normalization (NFC) so canonically-equivalent escape spellings
//!   cannot dodge the `..` component check;
//! - symlink escape: the resolved path is canonicalized and must remain under
//!   the workspace root; a symlink whose target escapes is rejected;
//! - TOCTOU: after resolving we re-verify containment and read through the
//!   canonical path, and re-check the final path stays under root.
//!
//! Fail-closed: any traversal / escape attempt returns 404 `{"error": ...}`.

use std::path::{Path, PathBuf};

use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Deserialize;
use serde_json::{json, Value};
use unicode_normalization::UnicodeNormalization;

use crate::state::AppState;

/// Upstream `MAX_FILE_BYTES` (api/config.py:994) — read/download limit.
const MAX_FILE_BYTES: u64 = 400_000;

/// Max entries returned by a directory listing (upstream list_dir caps at 200).
const MAX_LIST_ENTRIES: usize = 200;

#[derive(Debug, Deserialize)]
pub struct PathQuery {
    pub path: Option<String>,
    pub session_id: Option<String>,
}

/// Normalize a user-supplied relative path to posix form and reject any
/// traversal / absolute / encoded-escape spelling. Returns `None` when the
/// path is unusable (fail-closed). Mirrors upstream `_normalize_workspace_rel_path`
/// plus the Unicode + lexical hardening.
fn normalize_rel(raw: &str) -> Option<String> {
    let s = raw.replace('\\', "/");
    // Reject embedded null bytes early (fail-closed, mirrors upstream `_as_posix_path`).
    if s.contains('\0') {
        return None;
    }
    // Absolute paths (posix or windows-style drive letters) are rejected.
    if s.starts_with('/') || s.starts_with('~') {
        return None;
    }
    // Reject drive-letter/UNC forms after backslash normalisation.
    let norm_bytes = s.as_bytes();
    if norm_bytes.len() >= 2 && norm_bytes[1] == b':' {
        return None;
    }
    if s.starts_with("//") {
        return None;
    }
    // Trim, then NFC-normalize so canonically-equivalent `..` spellings are
    // collapsed before the component check.
    let norm: String = s.nfc().collect();
    let norm = norm.trim().to_string();
    if norm.is_empty() || norm == "." {
        return Some(".".to_string());
    }
    // Validate each component lexically. `.` and `..` are rejected outright;
    // empty components (from `//` or trailing `/`) are also rejected to avoid
    // ambiguity with the root.
    let mut out = Vec::new();
    for comp in norm.split('/') {
        match comp {
            "" | "." | ".." => return None,
            _ => out.push(comp.to_string()),
        }
    }
    if out.is_empty() {
        return Some(".".to_string());
    }
    Some(out.join("/"))
}

/// Resolve `rel` under `root`, rejecting any escape. Returns the canonical
/// (symlink-resolved) path guaranteed to live under `root`, or `None`.
fn resolve_within(root: &Path, rel: &str) -> Option<PathBuf> {
    let rel = normalize_rel(rel)?;
    let root_canon = root.canonicalize().ok()?;
    let candidate = root_canon.join(&rel);
    let resolved = candidate.canonicalize().ok()?;
    if resolved.starts_with(&root_canon) {
        Some(resolved)
    } else {
        None
    }
}

/// Reject a resolved path that is not a regular file/dir (e.g. a device node,
/// socket, fifo) — only regular files/dirs are served. Mirrors upstream
/// `stat.S_ISREG` / `S_ISDIR` gates.
fn is_regular_file(meta: &std::fs::Metadata) -> bool {
    meta.file_type().is_file()
}

fn is_dir(meta: &std::fs::Metadata) -> bool {
    meta.is_dir()
}

fn mtime_ns(meta: &std::fs::Metadata) -> Option<u128> {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
}

/// Build one directory-entry object, mirroring upstream `list_dir._process`.
fn build_entry(name: &str, rel: &str, path: &Path, root: &Path) -> Value {
    let display_path = if rel == "." || rel.is_empty() {
        name.to_string()
    } else {
        format!("{rel}/{name}")
    };
    match std::fs::symlink_metadata(path) {
        Ok(meta) => {
            let ft = meta.file_type();
            if ft.is_symlink() {
                // Symlink handling: only expose display-safe fields if it points
                // outside the workspace (never leak the target); otherwise expose
                // target + size/mtime like upstream.
                let link_target = std::fs::read_link(path).ok();
                let mut resolved_link = None;
                if let Some(lt) = &link_target {
                    let joined = if lt.is_absolute() {
                        lt.clone()
                    } else {
                        path.parent()
                            .map(|p| p.join(lt))
                            .unwrap_or_else(|| lt.clone())
                    };
                    resolved_link = joined.canonicalize().ok();
                }
                let outside = match &resolved_link {
                    Some(rt) => !rt.starts_with(root),
                    None => true,
                };
                let mut obj = json!({
                    "name": name,
                    "path": display_path,
                    "type": "symlink",
                    "is_dir": false,
                    "target_outside_workspace": outside,
                    "mtime_ns": mtime_ns(&meta),
                });
                if !outside {
                    if let Some(rt) = &resolved_link {
                        obj["target"] = json!(rt.to_string_lossy());
                        obj["is_dir"] = json!(rt.is_dir());
                        if let Ok(m) = std::fs::metadata(rt) {
                            if m.is_file() {
                                obj["size"] = json!(m.len());
                            }
                        }
                    }
                }
                obj
            } else {
                let is_dir_entry = is_dir(&meta);
                json!({
                    "name": name,
                    "path": display_path,
                    "type": if is_dir_entry { "dir" } else { "file" },
                    "size": if is_dir_entry { Value::Null } else { json!(meta.len()) },
                    "mtime_ns": mtime_ns(&meta),
                })
            }
        }
        Err(_) => {
            // Unreadable/lstat failure → emit a minimal entry, matching upstream
            // behaviour of appending with null metadata rather than dropping.
            json!({
                "name": name,
                "path": display_path,
                "type": "file",
                "size": Value::Null,
                "mtime_ns": Value::Null,
            })
        }
    }
}

/// Sort key matching upstream `_sort_key_de`: symlinks first, then files, then
/// directories; each group case-insensitive by name. Returns a comparable tuple.
fn sort_key(name: &str, path: &Path) -> (u8, String) {
    let is_symlink = std::fs::symlink_metadata(path)
        .map(|m| m.file_type().is_symlink())
        .unwrap_or(false);
    let is_file = std::fs::metadata(path)
        .map(|m| m.is_file())
        .unwrap_or(false);
    let rank = if is_symlink {
        0u8
    } else if is_file {
        1u8
    } else {
        2u8
    };
    (rank, name.to_lowercase())
}

/// GET /api/workspace/list — list a directory inside the workspace.
pub async fn list(State(state): State<AppState>, Query(q): Query<PathQuery>) -> Response {
    let root = state.config.workspace_root();
    if !root.is_dir() {
        return bad(
            StatusCode::NOT_FOUND,
            &format!("Workspace root not found: {}", root.display()),
        );
    }
    let rel = q.path.as_deref().unwrap_or(".");
    let Some(dir) = resolve_within(&root, rel) else {
        return bad(StatusCode::NOT_FOUND, "Path traversal blocked or not found");
    };
    if !dir.is_dir() {
        return bad(StatusCode::NOT_FOUND, &format!("Not a directory: {rel}"));
    }
    let Ok(read_dir) = std::fs::read_dir(&dir) else {
        return bad(
            StatusCode::NOT_FOUND,
            &format!("Cannot read directory: {rel}"),
        );
    };
    let mut entries: Vec<(String, PathBuf)> = read_dir
        .filter_map(|e| e.ok())
        .map(|e| (e.file_name().to_string_lossy().into_owned(), e.path()))
        .collect();
    entries.sort_by_key(|(name, path)| sort_key(name, path));
    entries.truncate(MAX_LIST_ENTRIES);

    let entries_json: Vec<Value> = entries
        .iter()
        .map(|(name, p)| build_entry(name, rel, p, &root))
        .collect();
    let payload = json!({
        "entries": entries_json,
        "path": rel,
        "workspace": root.to_string_lossy(),
        "workspace_recovered": false,
    });
    (StatusCode::OK, Json(payload)).into_response()
}

/// GET /api/workspace/metadata — stat a single file/dir inside the workspace.
pub async fn metadata(State(state): State<AppState>, Query(q): Query<PathQuery>) -> Response {
    let root = state.config.workspace_root();
    if !root.is_dir() {
        return bad(
            StatusCode::NOT_FOUND,
            &format!("Workspace root not found: {}", root.display()),
        );
    }
    let rel = q.path.as_deref().unwrap_or(".");
    let Some(target) = resolve_within(&root, rel) else {
        return bad(StatusCode::NOT_FOUND, "Path traversal blocked or not found");
    };
    let Ok(meta) = std::fs::metadata(&target) else {
        return bad(StatusCode::NOT_FOUND, &format!("Not found: {rel}"));
    };
    let ft = meta.file_type();
    let (kind, is_dir_entry) = if ft.is_dir() {
        ("dir", true)
    } else if ft.is_file() {
        ("file", false)
    } else if ft.is_symlink() {
        ("symlink", false)
    } else {
        ("other", false)
    };
    let name = target
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| rel.to_string());
    let payload = json!({
        "name": name,
        "path": rel,
        "type": kind,
        "is_dir": is_dir_entry,
        "size": if is_dir_entry { Value::Null } else { json!(meta.len()) },
        "mtime_ns": mtime_ns(&meta),
        "workspace": root.to_string_lossy(),
    });
    (StatusCode::OK, Json(payload)).into_response()
}

/// Shared read path for `read` (text) and `download` (bytes). Returns the
/// canonical target plus its bytes, enforcing containment + size limits.
fn read_bytes(state: &AppState, rel: &str) -> Result<(PathBuf, Vec<u8>), StatusCode> {
    let root = state.config.workspace_root();
    if !root.is_dir() {
        return Err(StatusCode::NOT_FOUND);
    }
    let Some(target) = resolve_within(&root, rel) else {
        return Err(StatusCode::NOT_FOUND);
    };
    let Ok(meta) = std::fs::metadata(&target) else {
        return Err(StatusCode::NOT_FOUND);
    };
    if !is_regular_file(&meta) {
        return Err(StatusCode::NOT_FOUND);
    }
    if meta.len() > MAX_FILE_BYTES {
        return Err(StatusCode::BAD_REQUEST);
    }
    let data = std::fs::read(&target).map_err(|_| StatusCode::NOT_FOUND)?;
    // TOCTOU backstop: re-verify the canonical path is still under root after
    // the read (a swapped symlink would now escape).
    if !target
        .canonicalize()
        .map(|c| c.starts_with(&root))
        .unwrap_or(false)
    {
        return Err(StatusCode::NOT_FOUND);
    }
    Ok((target, data))
}

/// GET /api/workspace/read — read a text file as JSON `{path, content, size, lines}`.
pub async fn read(State(state): State<AppState>, Query(q): Query<PathQuery>) -> Response {
    let rel = q.path.as_deref().unwrap_or("");
    let (_target, data) = match read_bytes(&state, rel) {
        Ok(v) => v,
        Err(status) => {
            let msg = if status == StatusCode::BAD_REQUEST {
                format!("File too large (max {MAX_FILE_BYTES} bytes)")
            } else {
                format!("Path traversal blocked or not found: {rel}")
            };
            return (status, Json(json!({ "error": msg }))).into_response();
        }
    };
    let content = String::from_utf8_lossy(&data);
    let lines = content.matches('\n').count() + 1;
    let payload = json!({
        "path": rel,
        "content": content,
        "size": data.len(),
        "lines": lines,
    });
    (StatusCode::OK, Json(payload)).into_response()
}

/// GET /api/workspace/download — serve raw file bytes with Content-Disposition.
pub async fn download(State(state): State<AppState>, Query(q): Query<PathQuery>) -> Response {
    let rel = q.path.as_deref().unwrap_or("");
    let (target, data) = match read_bytes(&state, rel) {
        Ok(v) => v,
        Err(status) => {
            return (status, Json(json!({ "error": "not found" }))).into_response();
        }
    };
    let name = target
        .file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "download".to_string());
    let disposition = format!("attachment; filename=\"{name}\"");
    let mut resp = Response::new(axum::body::Body::from(data));
    *resp.status_mut() = StatusCode::OK;
    resp.headers_mut().insert(
        axum::http::header::CONTENT_DISPOSITION,
        disposition.parse().unwrap(),
    );
    resp.headers_mut().insert(
        axum::http::header::CONTENT_TYPE,
        "application/octet-stream".parse().unwrap(),
    );
    resp.headers_mut().insert(
        axum::http::header::CACHE_CONTROL,
        "no-store".parse().unwrap(),
    );
    resp
}

fn bad(status: StatusCode, msg: &str) -> Response {
    (status, Json(json!({ "error": msg }))).into_response()
}

pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new()
        .route("/api/workspace/list", axum::routing::get(list))
        .route("/api/workspace/read", axum::routing::get(read))
        .route("/api/workspace/metadata", axum::routing::get(metadata))
        .route("/api/workspace/download", axum::routing::get(download))
        // R4 : routes upstream exposées pour la parité (contrat /api/list +
        // /api/file). Le `session_id` est toléré mais non requis : le port
        // isole sur workspace_root() (sandbox), upstream résout le workspace
        // depuis la session. La réponse porte le même shape.
        .route("/api/list", axum::routing::get(list))
        .route("/api/file", axum::routing::get(read))
}
