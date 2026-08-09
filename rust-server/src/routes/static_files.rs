//! Static asset serving — faithful port of `_serve_static` (api/routes.py:16945).
//!
//! Upstream contract (baseline 192df903):
//! - Path must start with `/static/`; the remainder is resolved against the
//!   static root and sandboxed via resolve()+relative_to (path traversal → 404).
//! - MIME table from `_STATIC_MIME`; text types get `; charset=utf-8`.
//! - Weak ETag `W/"<size:hex>-<mtime_ns:hex>"`; 304 on If-None-Match match.
//! - gzip pre-compression for compressible MIME types > 1024 bytes, served when
//!   `Accept-Encoding` contains gzip, with `Vary: Accept-Encoding`.
//! - Cache-Control: `public, max-age=31536000, immutable` when `?v=` present,
//!   else `public, max-age=300`.
//! - Missing/random paths → 404 JSON `{"error":"not found"}`.

use std::path::{Path, PathBuf};

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use flate2::write::GzEncoder;
use flate2::Compression;
use serde_json::json;

use crate::state::AppState;

const STATIC_MIME: &[(&str, &str)] = &[
    ("css", "text/css"),
    ("js", "application/javascript"),
    ("html", "text/html"),
    ("svg", "image/svg+xml"),
    ("png", "image/png"),
    ("jpg", "image/jpeg"),
    ("jpeg", "image/jpeg"),
    ("ico", "image/x-icon"),
    ("gif", "image/gif"),
    ("webp", "image/webp"),
    ("woff", "font/woff"),
    ("woff2", "font/woff2"),
];

const TEXT_MIME_TYPES: &[&str] = &[
    "text/css",
    "application/javascript",
    "text/html",
    "image/svg+xml",
    "text/plain",
];

const COMPRESSIBLE_MIME: &[&str] = &[
    "text/css",
    "application/javascript",
    "text/html",
    "image/svg+xml",
    "application/json",
    "text/plain",
];

fn mime_for(ext: &str) -> &'static str {
    STATIC_MIME
        .iter()
        .find(|(e, _)| *e == ext)
        .map(|(_, m)| *m)
        .unwrap_or("text/plain")
}

/// Resolve `rel` under `root`, rejecting any escape (mirrors
/// `Path.resolve() + relative_to()` in the upstream sandbox).
fn sandboxed(root: &Path, rel: &str) -> Option<PathBuf> {
    let candidate = root.join(rel);
    let resolved = candidate.canonicalize().ok()?;
    if resolved.starts_with(root) {
        Some(resolved)
    } else {
        None
    }
}

fn gzip_if_worth(data: &[u8], mime: &str) -> Option<Vec<u8>> {
    if data.len() <= 1024 || !COMPRESSIBLE_MIME.contains(&mime) {
        return None;
    }
    let mut enc = GzEncoder::new(Vec::new(), Compression::new(6));
    std::io::Write::write_all(&mut enc, data).ok()?;
    enc.finish().ok()
}

pub async fn static_file(State(state): State<AppState>, req: Request) -> Response {
    let path = req.uri().path();
    let rel = path.strip_prefix("/static/").unwrap_or(path);
    let root = state.config.static_dir().to_path_buf();

    let Some(file) = sandboxed(&root, rel) else {
        return (StatusCode::NOT_FOUND, Json(json!({"error": "not found"}))).into_response();
    };

    let ext = file
        .extension()
        .map(|e| e.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let mime = mime_for(&ext);
    let content_type = if TEXT_MIME_TYPES.contains(&mime) {
        format!("{mime}; charset=utf-8")
    } else {
        mime.to_string()
    };

    // Cache lookup keyed by absolute path; invalidated by (size, mtime_ns),
    // mirroring the upstream `_STATIC_CACHE` contract. On a valid hit we reuse
    // `raw` / `gz` / `etag` from memory (shared via `Arc`) without touching the
    // disk again; on a miss we read + precompress once and store it.
    let Some(meta) = std::fs::metadata(&file).ok().filter(|m| m.is_file()) else {
        return (StatusCode::NOT_FOUND, Json(json!({"error": "not found"}))).into_response();
    };
    let sig = (meta.len(), mtime_ns(&meta));

    let entry: std::sync::Arc<crate::state::StaticCacheEntry> =
        match state.static_cache.lookup(&file, sig) {
            Some(entry) => entry,
            None => {
                let Ok(raw) = std::fs::read(&file) else {
                    return (StatusCode::NOT_FOUND, Json(json!({"error": "not found"})))
                        .into_response();
                };
                let etag = format!("W/\"{:x}-{:x}\"", sig.0, sig.1);
                let gz = gzip_if_worth(&raw, mime);
                let entry = crate::state::StaticCacheEntry { sig, raw, gz, etag };
                state.static_cache.insert(
                    file.clone(),
                    crate::state::StaticCacheEntry {
                        sig: entry.sig,
                        raw: entry.raw.clone(),
                        gz: entry.gz.clone(),
                        etag: entry.etag.clone(),
                    },
                );
                // Return the local owned entry (equivalent payload, avoids an
                // extra Arc round-trip through the cache map).
                std::sync::Arc::new(entry)
            }
        };

    let etag = &entry.etag;
    let gz = entry.gz.as_ref();
    let raw = &entry.raw;

    let query = req.uri().query().unwrap_or("");
    let has_fingerprint = query
        .split('&')
        .any(|kv| kv.split('=').next() == Some("v") && kv.len() > 2);
    let cache_control = if has_fingerprint {
        "public, max-age=31536000, immutable"
    } else {
        "public, max-age=300"
    };

    let if_none_match = req
        .headers()
        .get(header::IF_NONE_MATCH)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_string();

    if if_none_match == *etag {
        let mut resp = Response::new(Body::empty());
        *resp.status_mut() = StatusCode::NOT_MODIFIED;
        resp.headers_mut()
            .insert(header::ETAG, etag.parse().unwrap());
        resp.headers_mut()
            .insert(header::CACHE_CONTROL, cache_control.parse().unwrap());
        if gz.is_some() {
            resp.headers_mut()
                .insert(header::VARY, "Accept-Encoding".parse().unwrap());
        }
        return resp;
    }

    let accept_encoding = req
        .headers()
        .get(header::ACCEPT_ENCODING)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
        .to_lowercase();
    let use_gzip = gz.is_some() && accept_encoding.contains("gzip");
    let body: &[u8] = if use_gzip {
        gz.expect("use_gzip implies gz").as_slice()
    } else {
        raw.as_slice()
    };

    let mut resp = Response::new(Body::from(body.to_vec()));
    *resp.status_mut() = StatusCode::OK;
    resp.headers_mut()
        .insert(header::CONTENT_TYPE, content_type.parse().unwrap());
    resp.headers_mut().insert(
        header::CONTENT_LENGTH,
        body.len().to_string().parse().unwrap(),
    );
    resp.headers_mut()
        .insert(header::ETAG, etag.parse().unwrap());
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, cache_control.parse().unwrap());
    if gz.is_some() {
        resp.headers_mut()
            .insert(header::VARY, "Accept-Encoding".parse().unwrap());
    }
    if use_gzip {
        resp.headers_mut()
            .insert(header::CONTENT_ENCODING, "gzip".parse().unwrap());
    }
    resp
}

fn mtime_ns(meta: &std::fs::Metadata) -> u128 {
    meta.modified()
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos())
        .unwrap_or(0)
}

pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new().route("/static/{*path}", axum::routing::get(static_file))
}
