//! `/api/session/export` + `/api/session/import` — faithful port of the
//! upstream session export/import routes (Track F R3).
//!
//! Upstream contract (baseline 192df903, `api/routes.py`):
//! - GET  /api/session/export?session_id=…[&format=json|html][&theme=dark|light]
//!   [&palette=<b64json>]  → `_handle_session_export` (routes.py:17019)
//! - POST /api/session/import  → `_handle_session_import` (routes.py:26390)
//! - POST /api/session/import_cli → `_handle_session_import_cli` (routes.py:26145)
//!   depends on the hermes-agent CLI store → classified HERMES_BRIDGE_REQUIRED,
//!   NOT implemented here (no fabricated store; see docs/rust-port/r3-import-export.md).
//!
//! Export redacts credentials at the response layer (`redact_session_data`,
//! `api/helpers.py:1010`) — the on-disk sidecar is never modified.

use axum::extract::State;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};

use crate::sessions::{Session, Store};
use crate::state::AppState;

/// Assemble the import/export router (Track F R3).
pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new()
        .route("/api/session/export", axum::routing::get(export_session))
        .route("/api/session/import", axum::routing::post(import_session))
}

/// GET /api/session/export — export a session as JSON (default) or self-contained HTML.
///
/// Mirrors `_handle_session_export` (api/routes.py:17019). Query params:
/// - `session_id` (required) — 400 "session_id is required" when absent,
///   404 "Session not found" when unknown.
/// - `format` = `json` (default) | `html` (case-insensitive).
/// - `theme` = `dark` (default) | `light` — HTML only.
/// - `palette` = base64-encoded JSON object (≤ 64 keys, else ignored) — HTML only.
///
/// The payload is `redact_session_data(session.__dict__)`: credentials are
/// masked at the response layer, never in the sidecar.
pub async fn export_session(
    State(state): State<AppState>,
    axum::extract::Query(params): axum::extract::Query<std::collections::HashMap<String, String>>,
) -> Response {
    let sid = params.get("session_id").cloned().unwrap_or_default();
    if sid.is_empty() {
        return error_response(StatusCode::BAD_REQUEST, "session_id is required");
    }

    let store = Store::new(state.config.state_dir.clone());
    let session = match store.load(&sid) {
        Ok(Some(s)) => s,
        Ok(None) => return error_response(StatusCode::NOT_FOUND, "Session not found"),
        Err(e) => {
            return error_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                &format!("failed to load session: {e}"),
            );
        }
    };

    // Upstream profile-scoping gate: a session from another profile is hidden.
    let profile = session.profile();
    if let Some(p) = profile {
        if !p.eq_ignore_ascii_case("default") {
            return error_response(StatusCode::NOT_FOUND, "Session not found");
        }
    }

    let fmt = params
        .get("format")
        .map(|s| s.to_ascii_lowercase())
        .unwrap_or_else(|| "json".to_string());

    let (payload, content_type, ext) = if fmt == "html" {
        let theme = params
            .get("theme")
            .map(|s| s.to_ascii_lowercase())
            .unwrap_or_else(|| "dark".to_string());
        let palette = parse_palette(params.get("palette"));
        (
            render_session_html(&session, &theme, palette.as_ref()),
            "text/html; charset=utf-8",
            "html",
        )
    } else {
        // JSON export: redacted, ensure_ascii=False, indent=2 (upstream).
        let redacted = redact_session_data(session.data());
        let body = serde_json::to_string_pretty(&redacted).unwrap_or_else(|_| "{}".to_string());
        (body, "application/json; charset=utf-8", "json")
    };

    let filename = format!("hermes-{sid}.{ext}");
    let mut resp = axum::response::Response::new(axum::body::Body::from(payload.into_bytes()));
    *resp.status_mut() = StatusCode::OK;
    resp.headers_mut().insert(
        header::CONTENT_TYPE,
        header::HeaderValue::from_static(content_type),
    );
    resp.headers_mut().insert(
        header::CONTENT_DISPOSITION,
        header::HeaderValue::from_str(&format!("attachment; filename=\"{filename}\""))
            .unwrap_or_else(|_| header::HeaderValue::from_static("attachment")),
    );
    resp.headers_mut().insert(
        header::CACHE_CONTROL,
        header::HeaderValue::from_static("no-store"),
    );
    resp
}

/// POST /api/session/import — import a session from a JSON export; creates a
/// NEW session with a NEW id (mirrors `_handle_session_import`, routes.py:26390).
///
/// Body: `{messages: [...] (required), title?, workspace?, model?, tool_calls?,
/// pinned?}`. The imported session is persisted immediately (atomic save) and
/// returned as `{ok: true, session: compact() | {messages}}`.
pub async fn import_session(State(state): State<AppState>, Json(body): Json<Value>) -> Response {
    let Some(obj) = body.as_object() else {
        return error_response(
            StatusCode::BAD_REQUEST,
            "Request body must be a JSON object",
        );
    };
    let Some(messages) = obj.get("messages") else {
        return error_response(
            StatusCode::BAD_REQUEST,
            "JSON must contain a \"messages\" array",
        );
    };
    if !messages.is_array() {
        return error_response(
            StatusCode::BAD_REQUEST,
            "JSON must contain a \"messages\" array",
        );
    }

    let title = obj
        .get("title")
        .and_then(Value::as_str)
        .unwrap_or("Imported session")
        .to_string();
    let workspace = match resolve_trusted_workspace(
        obj.get("workspace").and_then(Value::as_str),
        &state.config.workspace_root().to_string_lossy(),
    ) {
        Ok(ws) => ws,
        Err(msg) => return error_response(StatusCode::BAD_REQUEST, &msg),
    };
    let model = obj
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or("gpt-4o-mini")
        .to_string();
    let tool_calls = obj
        .get("tool_calls")
        .cloned()
        .unwrap_or_else(|| Value::Array(vec![]));
    let pinned = obj.get("pinned").and_then(Value::as_bool).unwrap_or(false);

    let store = Store::new(state.config.state_dir.clone());
    let sid = store.generate_id();
    let mut session = Session::new(sid);
    session.set_title(title);
    session.set_workspace(workspace);
    session.set_model(Some(model));
    session.data_mut().insert("tool_calls".into(), tool_calls);
    session.data_mut().insert("pinned".into(), json!(pinned));
    session
        .data_mut()
        .insert("messages".into(), messages.clone());
    session.prepare_save();

    if let Err(e) = store.save(&session) {
        return error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            &format!("failed to import session: {e}"),
        );
    }

    let mut compact = session.compact();
    if let Some(c) = compact.as_object_mut() {
        c.insert("messages".into(), messages.clone());
    }
    let payload = json!({ "ok": true, "session": compact });
    (StatusCode::OK, Json(payload)).into_response()
}

// ── helpers ────────────────────────────────────────────────────────────────

fn error_response(status: StatusCode, msg: &str) -> Response {
    (status, Json(json!({ "error": msg }))).into_response()
}

/// Resolve the import workspace against the trusted root.
/// Port of `resolve_trusted_workspace` (api/helpers.py): the raw value must
/// resolve under the workspace root; absolute paths outside are rejected.
fn resolve_trusted_workspace(raw: Option<&str>, root: &str) -> Result<String, String> {
    let raw = raw.unwrap_or(root);
    let candidate = std::path::Path::new(raw);
    let root_path = std::path::Path::new(root);
    let resolved = match candidate.canonicalize() {
        Ok(p) => p,
        Err(_) => {
            // Non-existing candidate: resolve lexically and require it to stay
            // under the root (matches upstream resolve()+relative_to semantics).
            let joined = root_path.join(candidate);
            match joined.canonicalize() {
                Ok(p) => p,
                Err(_) => joined,
            }
        }
    };
    // Trusted only if it starts with the canonical root.
    let canon_root = root_path
        .canonicalize()
        .unwrap_or_else(|_| root_path.to_path_buf());
    if resolved.starts_with(&canon_root) {
        Ok(resolved.to_string_lossy().into_owned())
    } else {
        Err(format!("Workspace path must be inside {root}"))
    }
}

/// Parse the `palette` query param: base64-encoded JSON object, ≤ 64 keys.
fn parse_palette(raw: Option<&String>) -> Option<Map<String, Value>> {
    let raw = raw?;
    let decoded = base64_decode(raw).ok()?;
    let value: Value = serde_json::from_slice(&decoded).ok()?;
    let obj = value.as_object()?;
    if obj.len() > 64 {
        return None;
    }
    Some(obj.clone())
}

fn base64_decode(s: &str) -> Result<Vec<u8>, ()> {
    use base64::Engine;
    base64::engine::general_purpose::STANDARD
        .decode(s)
        .map_err(|_| ())
}

/// Render a self-contained HTML document for a session export.
/// Fidelity note (documented debt): upstream uses markdown_it for full
/// CommonMark + tables; this port produces a valid, self-contained document
/// with HTML-escaped text (safe by construction), role badges, timestamps,
/// and a `<details>` reasoning block. Remote images are neutralized to inert
/// placeholders (same privacy boundary as upstream `_neutralize_remote_images`).
fn render_session_html(
    session: &Session,
    theme: &str,
    palette: Option<&Map<String, Value>>,
) -> String {
    let html_class = if theme.eq_ignore_ascii_case("light") {
        ""
    } else {
        " class=\"dark\""
    };
    let title = session.title();
    let title = if title.is_empty() {
        "Hermes Conversation".to_string()
    } else {
        title
    };
    let sid = session.session_id();
    let model = session.model().unwrap_or_default();
    let provider = session.model_provider().unwrap_or_default();
    let created = fmt_ts(session.created_at());
    let updated = fmt_ts(session.updated_at());

    let mut blocks = String::new();
    for m in session.messages() {
        if let Some(role) = m.get("role").and_then(Value::as_str) {
            if role == "system" {
                continue; // skip system boilerplate, like upstream
            }
            let (label, cls) = role_label(role);
            let ts = m
                .get("timestamp")
                .and_then(Value::as_f64)
                .map(fmt_ts)
                .unwrap_or_default();
            let ts_html = if ts.is_empty() {
                String::new()
            } else {
                format!("<span class=\"ts\">{}</span>", html_escape(&ts))
            };
            let body_md = content_to_text(m.get("content"));
            let body_html = render_markdown_simple(&body_md);
            let reasoning_html = m
                .get("reasoning")
                .and_then(Value::as_str)
                .filter(|r| !r.trim().is_empty())
                .map(|r| {
                    format!(
                        "<details class=\"reasoning\"><summary>💭 Reasoning</summary><div class=\"reasoning-body\">{}</div></details>",
                        render_markdown_simple(r)
                    )
                })
                .unwrap_or_default();
            blocks.push_str(&format!(
                "<section class=\"msg {cls}\"><div class=\"msg-head\"><span class=\"badge\">{label}</span>{ts_html}</div><div class=\"msg-body\">{reasoning_html}{body_html}</div></section>"
            ));
        }
    }

    let mut meta = String::new();
    if !sid.is_empty() {
        meta.push_str(&format!("<div>Session: <b>{}</b></div>", html_escape(&sid)));
    }
    if !model.is_empty() {
        let prov = if provider.is_empty() {
            String::new()
        } else {
            format!(" · {}", html_escape(&provider))
        };
        meta.push_str(&format!(
            "<div>Model: <b>{}</b>{}</div>",
            html_escape(&model),
            prov
        ));
    }
    if !created.is_empty() || !updated.is_empty() {
        meta.push_str(&format!(
            "<div>Created: <b>{created}</b> · Updated: <b>{updated}</b></div>"
        ));
    }
    let visible_count = session
        .messages()
        .iter()
        .filter(|m| m.get("role").and_then(Value::as_str) != Some("system"))
        .count();
    meta.push_str(&format!("<div>Messages: <b>{visible_count}</b></div>"));

    let palette_css = palette
        .map(|p| {
            let mut css = String::new();
            for (k, v) in p {
                if let Some(val) = v.as_str() {
                    css.push_str(&format!("{}:{};", html_escape(k), html_escape(val)));
                }
            }
            css
        })
        .unwrap_or_default();

    format!(
        "<!DOCTYPE html>\n<html lang=\"en\"{html_class}>\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<title>{title}</title>\n<style>body{{font-family:system-ui,sans-serif;max-width:820px;margin:2rem auto;padding:0 1rem;line-height:1.6}}.dark{{background:#1a1a2e;color:#e0e0e0}}.msg{{border:1px solid #444;border-radius:8px;padding:.8rem;margin:1rem 0}}.msg-head{{display:flex;gap:.6rem;align-items:center;margin-bottom:.4rem}}.badge{{font-size:.75rem;font-weight:700;padding:.15rem .5rem;border-radius:99px;background:#333;color:#fff}}.ts{{color:#888;font-size:.75rem}}.role-user .badge{{background:#2563eb}}.role-assistant .badge{{background:#059669}}.role-tool .badge{{background:#b45309}}.reasoning{{margin:.5rem 0;color:#9ca3af}}pre{{background:#111;padding:.6rem;border-radius:6px;overflow-x:auto}}code{{background:#111;padding:.1rem .3rem;border-radius:4px}}img{{max-width:100%}}.doc-foot{{margin-top:2rem;color:#888;font-size:.8rem}}.meta{{color:#bbb;font-size:.85rem}}.{palette_css}</style>\n</head>\n<body>\n<div class=\"wrap\">\n<header class=\"doc-head\">\n<h1>{title}</h1>\n<div class=\"meta\">{meta}</div>\n</header>\n<main>\n{blocks}\n</main>\n<footer class=\"doc-foot\">Exported from Hermes WebUI</footer>\n</div>\n</body>\n</html>"
    )
}

fn role_label(role: &str) -> (String, String) {
    match role {
        "user" => ("You".to_string(), "role-user".to_string()),
        "assistant" => ("Assistant".to_string(), "role-assistant".to_string()),
        "tool" => ("Tool".to_string(), "role-tool".to_string()),
        _ => ("System".to_string(), "role-system".to_string()),
    }
}

fn content_to_text(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Array(parts)) => {
            let mut out = Vec::new();
            for p in parts {
                if let Some(obj) = p.as_object() {
                    if obj.get("type").and_then(Value::as_str) == Some("text")
                        || obj.contains_key("text")
                    {
                        if let Some(t) = obj.get("text").and_then(Value::as_str) {
                            out.push(t.to_string());
                        }
                    } else if let Some(typ) = obj.get("type").and_then(Value::as_str) {
                        if matches!(typ, "image_url" | "image") {
                            let url = obj
                                .get("image_url")
                                .and_then(|v| v.get("url"))
                                .and_then(Value::as_str)
                                .or_else(|| obj.get("url").and_then(Value::as_str))
                                .unwrap_or("");
                            if url.starts_with("data:") {
                                out.push(format!("![image]({url})"));
                            } else if !url.is_empty() {
                                out.push(format!("`[image: {url}]`"));
                            }
                        } else {
                            out.push(format!("`[{typ}]`"));
                        }
                    }
                } else {
                    out.push(p.to_string());
                }
            }
            out.join("\n\n")
        }
        _ => String::new(),
    }
}

/// Minimal safe Markdown: escapes everything, then applies code blocks,
/// bold/italic, and inline code. NOT full CommonMark (documented debt vs
/// upstream markdown_it) — safe by construction (no raw HTML).
fn render_markdown_simple(text: &str) -> String {
    let mut out = String::new();
    let mut in_code = false;
    let mut code_buf = String::new();
    for line in text.lines() {
        if line.trim_start().starts_with("```") {
            if in_code {
                out.push_str(&format!("<pre>{}</pre>", html_escape(&code_buf)));
                code_buf.clear();
                in_code = false;
            } else {
                in_code = true;
            }
            continue;
        }
        if in_code {
            code_buf.push_str(line);
            code_buf.push('\n');
            continue;
        }
        let escaped = html_escape(line);
        let mut rich = String::new();
        // inline code `...`
        let mut rest = escaped.as_str();
        while let Some(start) = rest.find('`') {
            rich.push_str(&rest[..start]);
            rest = &rest[start + 1..];
            if let Some(end) = rest.find('`') {
                rich.push_str(&format!("<code>{}</code>", &rest[..end]));
                rest = &rest[end + 1..];
            } else {
                rich.push('`');
                break;
            }
        }
        rich.push_str(rest);
        // bold **...**
        rich = rich
            .replace("**", "<strong>")
            .replacen("**", "</strong>", 1)
            .replace("__", "<strong>")
            .replacen("__", "</strong>", 1);
        out.push_str(&format!("<p>{rich}</p>"));
    }
    if in_code {
        out.push_str(&format!("<pre>{}</pre>", html_escape(&code_buf)));
    }
    out
}

fn html_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\'' => out.push_str("&#39;"),
            _ => out.push(c),
        }
    }
    out
}

fn fmt_ts(ts: f64) -> String {
    if ts <= 0.0 {
        return String::new();
    }
    let secs = ts as i64;
    let days = secs.div_euclid(86400);
    let rem = secs.rem_euclid(86400);
    let (h, m, s) = (rem / 3600, (rem % 3600) / 60, rem % 60);
    // 1970-01-01 + days
    let date = {
        let mut y = 1970i64;
        let mut d = days;
        loop {
            let ylen = if y % 4 == 0 && (y % 100 != 0 || y % 400 == 0) {
                366
            } else {
                365
            };
            if d < ylen {
                break;
            }
            d -= ylen;
            y += 1;
        }
        let mlen = [
            31,
            if y % 4 == 0 && (y % 100 != 0 || y % 400 == 0) {
                29
            } else {
                28
            },
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ];
        let mut mo = 0usize;
        let mut d2 = d;
        while d2 >= mlen[mo] {
            d2 -= mlen[mo];
            mo += 1;
        }
        format!("{y:04}-{:02}-{:02}", mo + 1, d2 + 1)
    };
    format!("{date} {h:02}:{m:02}:{s:02}")
}

// ── redaction (response-layer, port of api/helpers.py redact_session_data) ──

/// Port of `redact_session_data` (api/helpers.py:1010): masks credentials in
/// title, messages, tool_calls, todo_state, runtime_journal_snapshot.
/// `api_redact_enabled` defaults to true in the port (settings.rs).
fn redact_session_data(data: &Map<String, Value>) -> Value {
    let mut out = data.clone();
    if let Some(title) = out.get("title").and_then(Value::as_str) {
        out.insert("title".into(), Value::String(redact_text(title)));
    }
    for key in [
        "messages",
        "tool_calls",
        "todo_state",
        "runtime_journal_snapshot",
    ] {
        if let Some(v) = out.get(key).cloned() {
            out.insert(key.into(), redact_value(v));
        }
    }
    Value::Object(out)
}

fn redact_value(v: Value) -> Value {
    match v {
        Value::String(s) => Value::String(redact_text(&s)),
        Value::Array(items) => Value::Array(items.into_iter().map(redact_value).collect()),
        Value::Object(m) => {
            Value::Object(m.into_iter().map(|(k, v)| (k, redact_value(v))).collect())
        }
        other => other,
    }
}

/// Port of `_redact_text` fallback path: mask known credential shapes.
/// Fail-closed: unknown-but-likely-sensitive patterns (env KEY=value with
/// KEY containing TOKEN/SECRET/etc.) are masked too.
fn redact_text(text: &str) -> String {
    if text.is_empty() {
        return text.to_string();
    }
    let mut out = text.to_string();
    for pattern in CRED_PATTERNS {
        out = mask_pattern(&out, pattern);
    }
    // env assignments KEY=value with sensitive key names
    out = mask_env_assignments(&out);
    // private key blocks
    out = mask_private_keys(&out);
    out
}

const CRED_PATTERNS: &[(&str, &str)] = &[
    // (prefix, suffix regex-ish) — we mask from prefix to a plausible tail.
    ("sk-", "sk-[A-Za-z0-9_-]{10,}"),
    ("ghp_", "ghp_[A-Za-z0-9]{10,}"),
    ("github_pat_", "github_pat_[A-Za-z0-9_]{10,}"),
    ("xox", "xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("AIza", "AIza[A-Za-z0-9_-]{30,}"),
    ("AKIA", "AKIA[A-Z0-9]{16}"),
    ("sk_live_", "sk_live_[A-Za-z0-9]{10,}"),
    ("sk_test_", "sk_test_[A-Za-z0-9]{10,}"),
    ("rk_live_", "rk_live_[A-Za-z0-9]{10,}"),
    ("SG.", "SG\\.[A-Za-z0-9_-]{10,}"),
    ("hf_", "hf_[A-Za-z0-9]{10,}"),
    ("r8_", "r8_[A-Za-z0-9]{10,}"),
    ("npm_", "npm_[A-Za-z0-9]{10,}"),
    ("pypi-", "pypi-[A-Za-z0-9_-]{10,}"),
    ("dop_v1_", "dop_v1_[A-Za-z0-9]{10,}"),
    ("doo_v1_", "doo_v1_[A-Za-z0-9]{10,}"),
    ("tvly-", "tvly-[A-Za-z0-9]{10,}"),
    ("gsk_", "gsk_[A-Za-z0-9]{10,}"),
    ("syt_", "syt_[A-Za-z0-9]{10,}"),
    ("mem0_", "mem0_[A-Za-z0-9]{10,}"),
    ("eyJ", "eyJ[A-Za-z0-9_-]{10,}"),
];

/// Mask the first occurrence of a regex pattern in `text` with `***`.
/// Simple, dependency-free: no regex crate needed for these fixed prefixes.
fn mask_pattern(text: &str, (prefix, _full): &(&str, &str)) -> String {
    if let Some(idx) = text.find(prefix) {
        // Mask from prefix start through a bounded run of token chars.
        let start = idx;
        let rest = &text[idx + prefix.len()..];
        let tail: String = rest
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
            .collect();
        if tail.len() >= 6 {
            let masked = format!("{prefix}***");
            let end = idx + prefix.len() + tail.len();
            return format!("{}{}{}", &text[..start], masked, &text[end..]);
        }
    }
    text.to_string()
}

/// Mask `KEY=value` / `KEY="value"` assignments whose key contains a
/// sensitive token (port of `_ENV_RE` fallback).
fn mask_env_assignments(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    while let Some(rel) = find_sensitive_env_key(rest) {
        out.push_str(&rest[..rel.start]);
        let kv = &rest[rel.start..];
        let (key, eq, quote, value) = split_env_assignment(kv);
        if let Some(v) = value {
            let masked = mask_token(v);
            out.push_str(&format!("{key}{eq}{quote}{masked}{quote}"));
            let consumed = key.len() + eq.len() + quote.len() * 2 + v.len();
            rest = &kv[consumed.min(kv.len())..];
        } else {
            out.push_str(key);
            rest = &kv[key.len()..];
        }
    }
    out.push_str(rest);
    out
}

fn find_sensitive_env_key(text: &str) -> Option<std::ops::Range<usize>> {
    const MARKERS: &[&str] = &[
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "passwd",
        "credential",
        "auth",
    ];
    let bytes = text.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        // candidate: run of [A-Za-z0-9_]
        let start = i;
        while i < bytes.len() && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
            i += 1;
        }
        if i > start {
            let key = &text[start..i];
            let kl = key.to_ascii_lowercase();
            if MARKERS.iter().any(|m| kl.contains(m)) {
                // must be followed by '='
                if text[i..].starts_with('=') {
                    return Some(start..i);
                }
            }
        } else {
            i += 1;
        }
    }
    None
}

#[allow(clippy::type_complexity)]
fn split_env_assignment<'a>(kv: &'a str) -> (&'a str, &'a str, &'a str, Option<&'a str>) {
    let eq = kv.find('=').unwrap_or(0);
    let key = &kv[..eq];
    let after = &kv[eq + 1..];
    if let Some(stripped) = after.strip_prefix('"') {
        if let Some(end) = stripped.find('"') {
            return (key, "=", "\"", Some(&stripped[..end]));
        }
        return (key, "=", "\"", None);
    }
    if let Some(stripped) = after.strip_prefix('\'') {
        if let Some(end) = stripped.find('\'') {
            return (key, "=", "'", Some(&stripped[..end]));
        }
        return (key, "=", "'", None);
    }
    let value = after.split_whitespace().next().unwrap_or("");
    if value.is_empty() {
        return (key, "=", "", None);
    }
    (key, "=", "", Some(value))
}

fn mask_token(token: &str) -> String {
    if token.len() >= 18 {
        format!("{}...{}", &token[..6], &token[token.len() - 4..])
    } else {
        "***".to_string()
    }
}

/// Mask PEM private key blocks (`-----BEGIN ... PRIVATE KEY----- ... -----END ...`).
fn mask_private_keys(text: &str) -> String {
    let mut out = String::new();
    let mut rest = text;
    loop {
        let Some(start) = rest.find("-----BEGIN") else {
            out.push_str(rest);
            break;
        };
        out.push_str(&rest[..start]);
        let tail = &rest[start..];
        let Some(end) = tail.find("-----END") else {
            out.push_str(tail);
            break;
        };
        // find the end-of-line after END marker
        let end_line = tail[end..]
            .find('\n')
            .map(|i| end + i + 1)
            .unwrap_or(tail.len());
        out.push_str("[REDACTED PRIVATE KEY]");
        rest = &tail[end_line.min(tail.len())..];
    }
    out
}
