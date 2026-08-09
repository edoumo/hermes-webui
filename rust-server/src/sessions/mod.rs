//! Sessions domain (Track B R2) — faithful Rust port of the upstream WebUI
//! JSON session store (`api/models.py` `Session` + `api/config.py` `SESSION_DIR`).
//!
//! ## On-disk format compatibility (critical)
//! Upstream stores each session as `STATE_DIR/sessions/<sid>.json`: a flat JSON
//! object whose metadata fields (title, workspace, model, …) sit at the top
//! level, followed by `messages`, `tool_calls` and the full session payload.
//! The session id is `uuid.uuid4().hex[:12]` (12 lowercase hex chars).
//!
//! We read/write **that exact shape** — the session object is kept as a
//! `serde_json::Map` and round-tripped verbatim, so a Rust-written sidecar
//! remains readable by the upstream Python server and vice-versa. No
//! Rust-only format is introduced. Atomic save (tmp + `fsync` + rename)
//! mirrors `Session.save()`.
//!
//! ## Ownership model (WebUI-owned vs Agent-owned vs CLI)
//! The upstream splits session state across two stores that are **never
//! merged**:
//!   * **WebUI-owned** — JSON sidecars created by this WebUI server
//!     (`/api/session/new`). They carry no CLI/agent source markers.
//!   * **CLI/import-owned** — sidecars imported from the agent CLI / external
//!     agent transcripts, marked by `source_tag`/`raw_source`/`session_source`
//!     containing `cli`/`tui`/`acp`/`external_agent`. They are read-only.
//!   * **Agent-owned** — the hermes-agent `state.db` rows (see
//!     `api/agent_sessions.py`). Those live in the agent's HOME, NOT in the
//!     WebUI JSON store, and are reached through the Hermes bridge (a later
//!     track). This module never fabricates or merges them into the JSON store.
//!
//! Each JSON sidecar is classified via `classify_ownership()` (a faithful port
//! of `is_cli_session_row` in `api/agent_sessions.py`). The `/api/sessions`
//! counts expose the WebUI/CLI split and never include Agent-owned state.

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value};

/// Metadata fields persisted by upstream `Session.save()` (`api/models.py:1392`),
/// listed in write order. Keys present in the object are preserved on round-trip;
/// we use the list only to seed a freshly-created session with upstream defaults.
const METADATA_FIELDS: &[&str] = &[
    "session_id",
    "title",
    "workspace",
    "created_workspace",
    "model",
    "model_provider",
    "model_explicit_pick_signature",
    "created_at",
    "updated_at",
    "pinned",
    "archived",
    "project_id",
    "profile",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "cache_read_tokens",
    "cache_write_tokens",
    "personality",
    "active_stream_id",
    "pending_user_message",
    "pending_attachments",
    "pending_started_at",
    "pending_user_source",
    "compression_anchor_visible_idx",
    "compression_anchor_message_key",
    "compression_anchor_summary",
    "pre_compression_snapshot",
    "context_engine",
    "compression_anchor_engine",
    "compression_anchor_mode",
    "compression_anchor_details",
    "context_engine_state",
    "context_length",
    "threshold_tokens",
    "last_prompt_tokens",
    "post_compression_context_tokens_estimate",
    "compression_recovery",
    "recommended_recovery_action",
    "compression_recovery_source_session_id",
    "compression_recovery_action",
    "truncation_watermark",
    "truncation_boundary",
    "clear_generation",
    "gateway_routing",
    "gateway_routing_history",
    "llm_title_generated",
    "manual_title",
    "parent_session_id",
    "worktree_path",
    "worktree_branch",
    "worktree_repo_root",
    "worktree_created_at",
    "is_cli_session",
    "source_tag",
    "raw_source",
    "session_source",
    "source_label",
    "read_only",
    "enabled_toolsets",
    "composer_draft",
    "process_wakeup_pause",
    "share_token",
    "share_created_at",
];

/// Source values that mark a row as non-CLI (kept out of the writable/CLI path).
/// Port of `MESSAGING_SOURCES | {cron, webhook, kanban, tool, api, ...}` from
/// `is_cli_session_row` (`api/agent_sessions.py`).
const NON_CLI_SOURCES: &[&str] = &[
    "discord",
    "email",
    "wecom",
    "wecom_callback",
    "slack",
    "telegram",
    "weixin",
    "matrix",
    "cron",
    "webhook",
    "kanban",
    "tool",
    "api",
    "api_server",
    "subagent",
];

/// Sources treated as interactive local clients → CLI-owned.
const CLI_INTERACTIVE_SOURCES: &[&str] = &["acp", "cli", "tui"];

/// Ownership of a session sidecar. Mirrors the upstream WebUI/CLI split.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SessionOwnership {
    /// Session created/owned by this WebUI (JSON sidecar, no CLI markers).
    WebUi,
    /// Session imported from the agent CLI / external agent (read-only).
    Cli,
    /// Agent-owned state lives in the hermes `state.db`, not in the JSON store.
    /// It is never surfaced by this module — the value is reserved so callers
    /// can branch explicitly rather than silently merging the two stores.
    Agent,
}

impl SessionOwnership {
    pub fn is_cli(self) -> bool {
        matches!(self, SessionOwnership::Cli)
    }
}

/// A loaded WebUI session sidecar.
#[derive(Debug, Clone)]
pub struct Session {
    /// The full JSON object read from disk (metadata + messages + tool_calls).
    data: Map<String, Value>,
}

impl Session {
    pub fn new(session_id: String) -> Self {
        let now = unix_now();
        let mut data = Map::new();
        for key in METADATA_FIELDS {
            data.insert((*key).to_string(), Value::Null);
        }
        data.insert("session_id".into(), Value::String(session_id));
        data.insert("title".into(), Value::String("Untitled".into()));
        data.insert("created_at".into(), json!(now));
        data.insert("updated_at".into(), json!(now));
        data.insert("messages".into(), Value::Array(vec![]));
        data.insert("tool_calls".into(), Value::Array(vec![]));
        data.insert("message_count".into(), json!(0));
        data.insert("anchor_scene_index".into(), Value::Object(Map::new()));
        Session { data }
    }

    /// The file's backing path is not stored; this is just an accessor helper.
    pub fn from_data(data: Map<String, Value>) -> Self {
        Session { data }
    }

    pub fn data(&self) -> &Map<String, Value> {
        &self.data
    }

    /// Mutable accessor for the raw JSON object (used by route mutation).
    pub fn data_mut(&mut self) -> &mut Map<String, Value> {
        &mut self.data
    }

    pub fn session_id(&self) -> String {
        as_string(self.data.get("session_id"))
    }

    pub fn title(&self) -> String {
        let t = as_string(self.data.get("title"));
        if t.is_empty() {
            "Untitled".to_string()
        } else {
            t
        }
    }

    pub fn workspace(&self) -> String {
        as_string(self.data.get("workspace"))
    }

    pub fn model(&self) -> Option<String> {
        opt_string(self.data.get("model"))
    }

    pub fn model_provider(&self) -> Option<String> {
        opt_string(self.data.get("model_provider"))
    }

    pub fn created_at(&self) -> f64 {
        as_f64(self.data.get("created_at"))
    }

    pub fn updated_at(&self) -> f64 {
        as_f64(self.data.get("updated_at"))
    }

    pub fn pinned(&self) -> bool {
        as_bool(self.data.get("pinned"))
    }

    pub fn archived(&self) -> bool {
        as_bool(self.data.get("archived"))
    }

    pub fn project_id(&self) -> Option<String> {
        opt_string(self.data.get("project_id"))
    }

    pub fn profile(&self) -> Option<String> {
        opt_string(self.data.get("profile"))
    }

    pub fn input_tokens(&self) -> i64 {
        as_i64(self.data.get("input_tokens"))
    }

    pub fn output_tokens(&self) -> i64 {
        as_i64(self.data.get("output_tokens"))
    }

    pub fn cache_read_tokens(&self) -> i64 {
        as_i64(self.data.get("cache_read_tokens"))
    }

    pub fn cache_write_tokens(&self) -> i64 {
        as_i64(self.data.get("cache_write_tokens"))
    }

    pub fn read_only(&self) -> bool {
        as_bool(self.data.get("read_only"))
    }

    pub fn messages(&self) -> &[Value] {
        match self.data.get("messages") {
            Some(Value::Array(a)) => a,
            _ => &[],
        }
    }

    pub fn set_title(&mut self, title: String) {
        self.data.insert("title".into(), Value::String(title));
    }

    pub fn set_workspace(&mut self, workspace: String) {
        self.data
            .insert("workspace".into(), Value::String(workspace));
    }

    pub fn set_model(&mut self, model: Option<String>) {
        self.data.insert(
            "model".into(),
            model.map(Value::String).unwrap_or(Value::Null),
        );
    }

    pub fn set_model_provider(&mut self, provider: Option<String>) {
        self.data.insert(
            "model_provider".into(),
            provider.map(Value::String).unwrap_or(Value::Null),
        );
    }

    /// Apply user-driven rename semantics (port of `apply_session_title_rename`,
    /// `api/session_ops.py`). Non-empty custom titles are protected from
    /// adaptive refresh; clearing / resetting to an auto label re-arms it.
    pub fn apply_title_rename(&mut self, raw_title: &str) -> String {
        let title = raw_title.trim();
        let title = if title.is_empty() { "Untitled" } else { title };
        let truncated = &title[..title.char_indices().take(80).count().min(title.len())];
        let manual_title = !matches!(truncated.to_lowercase().as_str(), "untitled" | "new chat");
        self.data
            .insert("title".into(), Value::String(truncated.to_string()));
        self.data
            .insert("manual_title".into(), Value::Bool(manual_title));
        self.data
            .insert("llm_title_generated".into(), Value::Bool(false));
        truncated.to_string()
    }

    /// Set `updated_at` to now and `message_count` to the message-array length
    /// (mirrors `Session.save` touch_updated_at + `meta['message_count']`).
    pub fn prepare_save(&mut self) {
        self.data.insert("updated_at".into(), json!(unix_now()));
        let count = self.messages().len();
        self.data.insert("message_count".into(), json!(count));
    }

    /// Number of `role == "user"` messages (port of `_compute_user_message_count`).
    pub fn user_message_count(&self) -> usize {
        self.messages()
            .iter()
            .filter(|m| matches!(m.get("role"), Some(Value::String(r)) if r == "user"))
            .count()
    }

    /// Timestamp of the last non-tool, non-empty-partial message, else None.
    /// Port of `_last_message_timestamp` with a bounded tail-window scan.
    pub fn last_message_timestamp(&self) -> Option<f64> {
        let msgs = self.messages();
        if msgs.is_empty() {
            return None;
        }
        let n = msgs.len();
        let start = n.saturating_sub(8);
        for m in msgs[start..].iter().rev() {
            if let Some(ts) = message_timestamp(m) {
                if !is_skipped_activity(m) {
                    return Some(ts);
                }
            }
        }
        // Full-scan fallback for long trailing tool clusters.
        for m in msgs.iter().rev() {
            if let Some(ts) = message_timestamp(m) {
                if !is_skipped_activity(m) {
                    return Some(ts);
                }
            }
        }
        None
    }

    /// `cache_hit_percent` (port of `api/usage.py:prompt_cache_hit_percent`).
    pub fn cache_hit_percent(&self) -> Option<f64> {
        let read = self.cache_read_tokens();
        let input = self.input_tokens();
        if read <= 0 || input <= 0 {
            return None;
        }
        Some((read as f64 / input as f64 * 100.0).round().min(100.0))
    }

    /// Compact serialized payload (`Session.compact()`, `api/models.py:1719`),
    /// with `messages` merged in (the callers append `| {"messages": ...}`).
    pub fn compact(&self) -> Value {
        let mut m = Map::new();
        m.insert("session_id".into(), Value::String(self.session_id()));
        m.insert("title".into(), Value::String(self.title()));
        m.insert("workspace".into(), Value::String(self.workspace()));
        m.insert(
            "model".into(),
            self.model().map(Value::String).unwrap_or(Value::Null),
        );
        m.insert(
            "model_provider".into(),
            self.model_provider()
                .map(Value::String)
                .unwrap_or(Value::Null),
        );
        m.insert(
            "message_count".into(),
            json!(self
                .messages()
                .len()
                .max(if has_pending(self) { 1 } else { 0 })),
        );
        m.insert("created_at".into(), json!(self.created_at()));
        m.insert("updated_at".into(), json!(self.updated_at()));
        m.insert(
            "last_message_at".into(),
            json!(self
                .last_message_timestamp()
                .unwrap_or_else(|| self.updated_at())),
        );
        m.insert("pinned".into(), json!(self.pinned()));
        m.insert("archived".into(), json!(self.archived()));
        m.insert(
            "project_id".into(),
            self.project_id().map(Value::String).unwrap_or(Value::Null),
        );
        m.insert(
            "profile".into(),
            self.profile().map(Value::String).unwrap_or(Value::Null),
        );
        m.insert("input_tokens".into(), json!(self.input_tokens()));
        m.insert("output_tokens".into(), json!(self.output_tokens()));
        m.insert("cache_read_tokens".into(), json!(self.cache_read_tokens()));
        m.insert(
            "cache_write_tokens".into(),
            json!(self.cache_write_tokens()),
        );
        m.insert(
            "cache_hit_percent".into(),
            self.cache_hit_percent()
                .map(|v| json!(v))
                .unwrap_or(Value::Null),
        );
        m.insert(
            "personality".into(),
            self.data.get("personality").cloned().unwrap_or(Value::Null),
        );
        m.insert(
            "context_length".into(),
            self.data
                .get("context_length")
                .cloned()
                .unwrap_or(Value::Null),
        );
        m.insert(
            "threshold_tokens".into(),
            self.data
                .get("threshold_tokens")
                .cloned()
                .unwrap_or(Value::Null),
        );
        m.insert(
            "last_prompt_tokens".into(),
            self.data
                .get("last_prompt_tokens")
                .cloned()
                .unwrap_or(Value::Null),
        );
        m.insert(
            "manual_title".into(),
            json!(as_bool(self.data.get("manual_title"))),
        );
        m.insert(
            "user_message_count".into(),
            json!(self.user_message_count()),
        );
        m.insert(
            "active_stream_id".into(),
            self.data
                .get("active_stream_id")
                .cloned()
                .unwrap_or(Value::Null),
        );
        m.insert(
            "pending_user_message".into(),
            self.data
                .get("pending_user_message")
                .cloned()
                .unwrap_or(Value::Null),
        );
        m.insert("has_pending_user_message".into(), json!(has_pending(self)));
        let ownership = classify_ownership(&self.data);
        m.insert("is_cli_session".into(), json!(ownership.is_cli()));
        m.insert(
            "source_tag".into(),
            self.data.get("source_tag").cloned().unwrap_or(Value::Null),
        );
        m.insert(
            "raw_source".into(),
            self.data.get("raw_source").cloned().unwrap_or(Value::Null),
        );
        m.insert(
            "session_source".into(),
            self.data
                .get("session_source")
                .cloned()
                .unwrap_or(Value::Null),
        );
        m.insert(
            "source_label".into(),
            self.data
                .get("source_label")
                .cloned()
                .unwrap_or(Value::Null),
        );
        m.insert("read_only".into(), json!(self.read_only()));
        m.insert(
            "is_streaming".into(),
            json!(false), // no in-process stream registry in the R2 port
        );
        m.insert("messages".into(), Value::Array(self.messages().to_vec()));
        Value::Object(m)
    }

    /// Bounded `/api/sessions` sidebar row (subset of `compact()` allowed by
    /// `_SIDEBAR_SESSION_RESPONSE_FIELDS`). Used by the list endpoint.
    pub fn sidebar_item(&self) -> Value {
        let compact = self.compact();
        let obj = compact
            .as_object()
            .expect("compact() always returns an object");
        let mut out = Map::new();
        for key in SIDEBAR_FIELDS {
            if let Some(v) = obj.get(*key) {
                out.insert((*key).to_string(), v.clone());
            }
        }
        // `attention` is set by the route (no runtime activity in R2).
        Value::Object(out)
    }
}

/// Fields surfaced on `/api/sessions` sidebar rows (port of the upstream
/// `_SIDEBAR_SESSION_RESPONSE_FIELDS` allowlist, minus agent-only additions).
const SIDEBAR_FIELDS: &[&str] = &[
    "session_id",
    "title",
    "workspace",
    "model",
    "model_provider",
    "message_count",
    "user_message_count",
    "created_at",
    "updated_at",
    "last_message_at",
    "pinned",
    "archived",
    "project_id",
    "profile",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "cache_read_tokens",
    "cache_write_tokens",
    "cache_hit_percent",
    "personality",
    "context_length",
    "source_tag",
    "raw_source",
    "session_source",
    "source_label",
    "is_cli_session",
    "is_streaming",
    "active_stream_id",
    "has_pending_user_message",
    "worktree_path",
    "worktree_branch",
    "parent_session_id",
    "pre_compression_snapshot",
    "read_only",
    "gateway_routing",
];

/// Path-safety contract for session ids (port of `is_safe_session_id`,
/// `api/models.py:214`). Accept alphanumerics, underscore and hyphen only;
/// dots/slashes are rejected so the id can never name a parent directory.
pub fn is_safe_session_id(sid: &str) -> bool {
    !sid.is_empty()
        && sid
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

/// Directory holding the JSON session store (`STATE_DIR/sessions`).
pub fn sessions_dir(state_dir: &Path) -> PathBuf {
    state_dir.join("sessions")
}

/// Classify a session sidecar's ownership. Faithful port of `is_cli_session_row`
/// (`api/agent_sessions.py:211`) narrowed to the JSON-store facts available here.
pub fn classify_ownership(data: &Map<String, Value>) -> SessionOwnership {
    let source = normalize_source(data.get("source"));
    let session_source = normalize_source(data.get("session_source"));
    let source_tag = normalize_source(data.get("source_tag"));
    let raw_source = normalize_source(data.get("raw_source"));
    let source_label = normalize_source(data.get("source_label"));

    // WebUI explicitly owned → never CLI.
    let all = [
        &source,
        &session_source,
        &source_tag,
        &raw_source,
        &source_label,
    ];
    if all.iter().any(|s| s.contains("webui")) {
        return SessionOwnership::WebUi;
    }
    // Non-CLI background/messaging sources → not CLI (but not WebUI either;
    // upstream treats them as a separate non-writable surface).
    if all.iter().any(|s| NON_CLI_SOURCES.contains(&s.as_str())) {
        return SessionOwnership::WebUi;
    }
    if source == "messaging" {
        return SessionOwnership::WebUi;
    }
    if source == "cli" {
        return SessionOwnership::Cli;
    }
    if matches!(source.as_str(), "external_agent" | "external-agent") {
        return SessionOwnership::Cli;
    }
    if all
        .iter()
        .any(|s| CLI_INTERACTIVE_SOURCES.contains(&s.as_str()))
    {
        return SessionOwnership::Cli;
    }
    // Legacy imported rows carry an is_cli_session flag + default CLI title.
    if as_bool(data.get("is_cli_session")) && looks_like_default_cli_title(data, &source) {
        return SessionOwnership::Cli;
    }
    SessionOwnership::WebUi
}

fn looks_like_default_cli_title(data: &Map<String, Value>, source: &str) -> bool {
    let title = normalize_source(data.get("title"));
    if title.is_empty() || title == "untitled" {
        return true;
    }
    if matches!(title.as_str(), "cli" | "cli session") {
        return true;
    }
    let source_name = normalize_source(data.get("source_name"));
    let session_source = normalize_source(data.get("session_source"));
    let source_tag = normalize_source(data.get("source_tag"));
    let raw_source = normalize_source(data.get("raw_source"));
    let source_label = normalize_source(data.get("source_label"));
    let mut candidates = vec![
        source.to_string(),
        source_name,
        session_source,
        source_tag,
        raw_source,
        source_label,
    ];
    candidates.retain(|c| !c.is_empty());
    candidates.push("cli".to_string());
    candidates.iter().any(|c| title == format!("{c} session"))
}

// ── helpers ────────────────────────────────────────────────────────────────

fn has_pending(s: &Session) -> bool {
    opt_string(s.data.get("pending_user_message")).is_some()
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

pub fn as_string(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

fn opt_string(v: Option<&Value>) -> Option<String> {
    match v {
        Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
        Some(Value::Number(n)) => Some(n.to_string()),
        _ => None,
    }
}

fn as_f64(v: Option<&Value>) -> f64 {
    match v {
        Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
        Some(Value::String(s)) => s.parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

fn as_i64(v: Option<&Value>) -> i64 {
    match v {
        Some(Value::Number(n)) => n.as_i64().unwrap_or_else(|| n.as_u64().unwrap_or(0) as i64),
        _ => 0,
    }
}

fn as_bool(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Bool(b)) => *b,
        Some(Value::String(s)) => matches!(s.to_lowercase().as_str(), "1" | "true" | "yes"),
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0) != 0,
        _ => false,
    }
}

fn normalize_source(v: Option<&Value>) -> String {
    as_string(v).trim().to_lowercase()
}

fn message_timestamp(m: &Value) -> Option<f64> {
    let raw = m.get("_ts").or_else(|| m.get("timestamp"));
    match raw {
        Some(Value::Number(n)) => n.as_f64(),
        Some(Value::String(s)) => s.parse().ok(),
        _ => None,
    }
}

/// True for cancelled/recovered partial activity rows with no reply text
/// (port of `_is_empty_partial_activity_message`).
fn is_skipped_activity(m: &Value) -> bool {
    if m.get("role").and_then(Value::as_str) != Some("assistant") {
        return false;
    }
    if !as_bool(m.get("_partial")) {
        return false;
    }
    match m.get("content") {
        Some(Value::String(s)) => s.trim().is_empty(),
        Some(Value::Array(parts)) => parts.iter().all(|p| {
            let text = match p.get("text") {
                Some(Value::String(t)) => t.trim().to_string(),
                _ => String::new(),
            };
            text.is_empty()
        }),
        _ => true,
    }
}

// Re-export `json!` for convenience at call sites in this module.
#[allow(unused_imports)]
use serde_json::json;

// ── JSON store operations ──────────────────────────────────────────────────
//
// All mutations operate under an exclusive `Store` guard so concurrent
// read-modify-write cycles on the same sidecar are serialised (upstream uses
// the module-level `LOCK` + per-session agent lock). Reads are unlocked and
// return owned snapshots.

use std::fs;
use std::io::Write;
use std::sync::Mutex;

use rand::Rng;

/// Serialises mutations of the JSON session store for one process.
pub struct Store {
    state_dir: PathBuf,
    lock: Mutex<()>,
}

impl Store {
    pub fn new(state_dir: PathBuf) -> Self {
        Store {
            state_dir,
            lock: Mutex::new(()),
        }
    }

    fn dir(&self) -> PathBuf {
        sessions_dir(&self.state_dir)
    }

    /// Path for a session sidecar. Returns None for unsafe ids (caller must
    /// reject before touching the filesystem).
    fn path_for(&self, sid: &str) -> Option<PathBuf> {
        if !is_safe_session_id(sid) {
            return None;
        }
        Some(self.dir().join(format!("{sid}.json")))
    }

    /// Generate an upstream-compatible session id (`uuid4().hex[:12]`).
    pub fn generate_id(&self) -> String {
        let mut rng = rand::thread_rng();
        (0..12)
            .map(|_| format!("{:x}", rng.gen_range(0..16)))
            .collect()
    }

    /// Load a full session sidecar. Returns None when missing or when the id
    /// is unsafe.
    pub fn load(&self, sid: &str) -> std::io::Result<Option<Session>> {
        let Some(path) = self.path_for(sid) else {
            return Ok(None);
        };
        if !path.exists() {
            return Ok(None);
        }
        let raw = fs::read_to_string(&path)?;
        let value: Value = serde_json::from_str(&raw)?;
        let data = value.as_object().cloned().unwrap_or_default();
        Ok(Some(Session { data }))
    }

    /// Atomically persist a session sidecar (tmp + fsync + rename, mirroring
    /// upstream `Session.save`). Returns io::Error on failure.
    pub fn save(&self, session: &Session) -> std::io::Result<()> {
        let sid = session.session_id();
        let Some(path) = self.path_for(&sid) else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                format!("unsafe session_id {sid:?}"),
            ));
        };
        let _guard = self.lock.lock().unwrap_or_else(|p| p.into_inner());
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let tmp = path.with_extension("json.tmp");
        let payload = serde_json::to_string_pretty(&session.data)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        {
            let mut f = fs::File::create(&tmp)?;
            f.write_all(payload.as_bytes())?;
            f.sync_all()?;
        }
        fs::rename(&tmp, &path)?;
        Ok(())
    }

    /// Delete a session sidecar plus its `.bak` (mirrors upstream delete).
    /// Returns the io error if the primary unlink failed; missing files are OK.
    pub fn delete(&self, sid: &str) -> std::io::Result<()> {
        let Some(path) = self.path_for(sid) else {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                format!("unsafe session_id {sid:?}"),
            ));
        };
        let _guard = self.lock.lock().unwrap_or_else(|p| p.into_inner());
        if path.exists() {
            fs::remove_file(&path)?;
        }
        let bak = path.with_extension("json.bak");
        if bak.exists() {
            let _ = fs::remove_file(&bak);
        }
        Ok(())
    }

    /// List all session sidecar paths (`.json` only, skipping `.bak`/`.tmp`).
    fn list_paths(&self) -> std::io::Result<Vec<PathBuf>> {
        let dir = self.dir();
        if !dir.exists() {
            return Ok(vec![]);
        }
        let mut out = Vec::new();
        for entry in fs::read_dir(&dir)? {
            let entry = entry?;
            let path = entry.path();
            if path.is_file() && path.extension().map(|e| e == "json").unwrap_or(false) {
                out.push(path);
            }
        }
        Ok(out)
    }

    /// Load all sidecars, skipping unreadable/corrupt files. Preserves on-disk
    /// ordering (directory read order, matching upstream glob).
    pub fn list(&self) -> std::io::Result<Vec<Session>> {
        let mut out = Vec::new();
        for path in self.list_paths()? {
            let sid = path
                .file_stem()
                .and_then(|s| s.to_str())
                .unwrap_or_default();
            if let Ok(Some(s)) = self.load(sid) {
                out.push(s);
            }
        }
        Ok(out)
    }

    /// Return the number of sessions that exist on disk (for count endpoints).
    pub fn count(&self) -> std::io::Result<usize> {
        Ok(self.list_paths()?.len())
    }

    /// Directory containing the store (exposed for tests).
    pub fn dir_path(&self) -> PathBuf {
        self.dir()
    }
}
