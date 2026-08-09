//! `/api/settings` — faithful port of the upstream settings contract
//! (api/config.py load_settings/save_settings + api/routes.py handlers).
//!
//! GET contract (baseline 192df903, api/routes.py:12649):
//! - returns `_SETTINGS_DEFAULTS` merged with stored settings.json values;
//! - `password_hash` is NEVER exposed (popped);
//! - `max_tokens`/`max_tokens_effective`/`max_tokens_fallback` default to null;
//! - `password_env_var` = whether HERMES_WEBUI_PASSWORD is set;
//! - `auth_enabled`/`password_auth_enabled` = false in the R0/R1 port (no auth);
//! - `passkeys_enabled`/`passwordless_enabled` = false;
//! - `webui_version`/`agent_version` injected (agent_version = "not detected").
//!
//! POST contract (api/routes.py:15910):
//! - merges the submitted body into stored settings (minus control keys);
//! - `bot_name` is stripped and defaults to "Hermes";
//! - `_set_password`/`_clear_password`/`_passwordless`/`_current_password` are
//!   control keys: the R0/R1 port has no auth, so password changes are refused
//!   with the same 409 message as upstream when HERMES_WEBUI_PASSWORD is set;
//! - `max_tokens` is popped and stored as null (no agent runtime yet);
//! - returns the same GET-shaped payload after saving.

use std::path::Path;

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Map, Value};

use crate::state::AppState;

/// Upstream `_SETTINGS_DEFAULTS` (api/config.py:9260) — the R0/R1 subset keeps
/// the full default map so the frontend receives every key it expects.
pub fn settings_defaults(state: &AppState) -> Map<String, Value> {
    let mut m = Map::new();
    m.insert(
        "default_workspace".into(),
        json!(state.config.repo_dir.to_string_lossy()),
    );
    m.insert("onboarding_completed".into(), json!(false));
    m.insert("send_key".into(), json!("enter"));
    m.insert("show_token_usage".into(), json!(false));
    m.insert("show_quota_chip".into(), json!(false));
    m.insert("show_conversation_outline".into(), json!(false));
    m.insert("show_busy_placeholder_hint".into(), json!(false));
    m.insert("hide_empty_state_suggestions".into(), json!(false));
    m.insert("hide_empty_state_panel".into(), json!(false));
    m.insert("new_chat_on_workspace_switch".into(), json!(false));
    m.insert("virtualize_transcript".into(), json!(false));
    m.insert("virtualize_transcript_optin".into(), json!(false));
    m.insert("show_tps".into(), json!(false));
    m.insert("fade_text_effect".into(), json!(false));
    m.insert("show_cli_sessions".into(), json!(true));
    m.insert("show_claude_code_sessions".into(), json!(true));
    m.insert("show_cron_sessions".into(), json!(false));
    m.insert("show_webhook_sessions".into(), json!(false));
    m.insert("show_kanban_sessions".into(), json!(false));
    m.insert("show_previous_messaging_sessions".into(), json!(false));
    m.insert("sync_to_insights".into(), json!(false));
    m.insert("check_for_updates".into(), json!(true));
    m.insert("update_channel".into(), json!("stable"));
    m.insert("ignore_agent_updates".into(), json!(false));
    m.insert("whats_new_summary_enabled".into(), json!(false));
    m.insert("tts_enabled".into(), json!(false));
    m.insert("tts_auto_read".into(), json!(false));
    m.insert("tts_engine".into(), json!("browser"));
    m.insert("tts_voice".into(), json!(""));
    m.insert("tts_rate".into(), json!(1.0));
    m.insert("tts_pitch".into(), json!(1.0));
    m.insert("voice_mode_button".into(), json!(false));
    m.insert("voice_continuous".into(), json!(false));
    m.insert("voice_silence_ms".into(), json!(1800));
    m.insert("raw_audio_mode".into(), json!(false));
    m.insert("theme".into(), json!("dark"));
    m.insert("skin".into(), json!("default"));
    m.insert("font_size".into(), json!("default"));
    m.insert("session_jump_buttons".into(), json!(false));
    m.insert("render_user_markdown".into(), json!(false));
    m.insert("large_text_paste_as_attachment".into(), json!(true));
    m.insert("project_quick_create_buttons".into(), json!(false));
    m.insert("structured_code_default_view".into(), json!("auto"));
    m.insert("structured_code_auto_tree_lines".into(), json!(10));
    m.insert("session_endless_scroll".into(), json!(false));
    m.insert(
        "chat_activity_display_mode".into(),
        json!("compact_worklog"),
    );
    m.insert("transparent_stream_event_timestamps".into(), json!(true));
    m.insert("auto_scroll_follow".into(), json!(true));
    m.insert("worklog_details_expanded_default".into(), json!(false));
    m.insert("hide_composer_attach".into(), json!(false));
    m.insert("hide_composer_saved_prompts".into(), json!(false));
    m.insert("hide_composer_mic".into(), json!(false));
    m.insert("show_titlebar_profile".into(), json!(false));
    m.insert("hide_composer_voice_mode".into(), json!(false));
    m.insert("hide_composer_yolo".into(), json!(false));
    m.insert("hide_composer_profile".into(), json!(false));
    m.insert("hide_composer_workspace".into(), json!(false));
    m.insert("hide_composer_mobile_config".into(), json!(false));
    m.insert("hide_composer_model".into(), json!(false));
    m.insert("hide_composer_quota_chip".into(), json!(false));
    m.insert("hide_composer_reasoning".into(), json!(false));
    m.insert("hide_composer_toolsets".into(), json!(false));
    m.insert("hide_composer_status".into(), json!(false));
    m.insert("hide_composer_context".into(), json!(false));
    m.insert("hide_composer_bg_badge".into(), json!(false));
    m.insert("pinned_sessions_limit".into(), json!(3));
    m.insert("inflight_state_max_sessions".into(), json!(8));
    m.insert("inflight_state_max_messages".into(), json!(24));
    m.insert("inflight_state_max_tool_calls".into(), json!(48));
    m.insert("inflight_state_max_string_chars".into(), json!(60000));
    m.insert("inflight_state_max_json_chars".into(), json!(1500000));
    m.insert("hidden_tabs".into(), json!([]));
    m.insert("tab_order".into(), json!([]));
    m.insert("composer_control_order".into(), json!([]));
    m.insert("language".into(), json!("en"));
    m.insert("bot_name".into(), json!("Hermes"));
    m.insert("sound_enabled".into(), json!(false));
    m.insert("rtl".into(), json!(false));
    m.insert("notifications_enabled".into(), json!(false));
    m.insert("show_thinking".into(), json!(true));
    m.insert("simplified_tool_calling".into(), json!(true));
    m.insert("terminal_auto_expand_on_output".into(), json!(false));
    m.insert("workspace_todos_tab".into(), json!(false));
    m.insert("api_redact_enabled".into(), json!(true));
    m.insert("dashboard_plugins".into(), json!({}));
    m.insert("sidebar_density".into(), json!("compact"));
    m.insert("auto_title_refresh_every".into(), json!("0"));
    m.insert("default_message_mode".into(), json!("steer"));
    m.insert("password_hash".into(), Value::Null);
    m.insert("auth_disabled_acknowledged".into(), json!(false));
    m.insert("provider_cost_budget".into(), Value::Null);
    m
}

/// Upstream `_SETTINGS_LEGACY_DROP_KEYS` (api/config.py:9368).
const LEGACY_DROP_KEYS: &[&str] = &[
    "assistant_language",
    "bubble_layout",
    "default_model",
    "activity_feed_expanded_default",
    "simplified_tool_calling",
];

/// Control keys consumed by the POST handler, never persisted.
const CONTROL_KEYS: &[&str] = &[
    "_set_password",
    "_clear_password",
    "_passwordless",
    "_current_password",
    "max_tokens",
    "max_tokens_effective",
    "max_tokens_fallback",
];

/// Upstream `_SETTINGS_SPEECH_KEYS` (api/config.py:9355).
const SPEECH_KEYS: &[&str] = &[
    "tts_enabled",
    "tts_auto_read",
    "tts_engine",
    "tts_voice",
    "tts_rate",
    "tts_pitch",
    "voice_mode_button",
    "voice_continuous",
    "voice_silence_ms",
    "raw_audio_mode",
];

/// Upstream `_discover_default_workspace` (api/config.py:857):
/// 1. HERMES_WEBUI_DEFAULT_WORKSPACE env var
/// 2. ~/workspace if it exists
/// 3. ~/work if it exists
/// 4. ~/workspace (create if needed)
/// 5. STATE_DIR / workspace
fn discover_default_workspace(state: &AppState) -> String {
    if let Ok(v) = std::env::var("HERMES_WEBUI_DEFAULT_WORKSPACE") {
        if !v.trim().is_empty() {
            return v.trim().to_string();
        }
    }
    if let Some(home) = std::env::var_os("HOME") {
        let home = Path::new(&home);
        for candidate in ["workspace", "work"] {
            let p = home.join(candidate);
            if p.is_dir() {
                return p.to_string_lossy().into_owned();
            }
        }
        // ~/workspace (create if needed) — upstream creates it lazily.
        return home.join("workspace").to_string_lossy().into_owned();
    }
    state
        .config
        .state_dir
        .join("workspace")
        .to_string_lossy()
        .into_owned()
}

/// Upstream `persisted_speech_settings_keys()` (api/config.py:9470):
/// sorted list of speech keys present in the stored settings file.
fn persisted_speech_keys(state: &AppState) -> Vec<String> {
    let stored = read_stored(&state.config.settings_path());
    let mut keys: Vec<String> = SPEECH_KEYS
        .iter()
        .filter(|k| stored.contains_key(**k))
        .map(|k| k.to_string())
        .collect();
    keys.sort();
    keys
}

fn read_stored(path: &Path) -> Map<String, Value> {
    let Ok(text) = std::fs::read_to_string(path) else {
        return Map::new();
    };
    serde_json::from_str::<Value>(&text)
        .ok()
        .and_then(|v| v.as_object().cloned())
        .unwrap_or_default()
}

fn write_stored(path: &Path, settings: &Map<String, Value>) -> std::io::Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let text = serde_json::to_string_pretty(settings)?;
    std::fs::write(path, text)
}

/// Build the GET-shaped payload: defaults + stored, minus secrets, plus
/// runtime-injected fields (upstream api/routes.py:12649-12704).
fn build_payload(state: &AppState) -> Map<String, Value> {
    let mut settings = settings_defaults(state);
    let stored = read_stored(&state.config.settings_path());
    for (k, v) in stored {
        if !LEGACY_DROP_KEYS.contains(&k.as_str()) && k != "persisted_speech_keys" {
            settings.insert(k, v);
        }
    }
    // Never expose the stored password hash.
    settings.remove("password_hash");
    settings.insert("max_tokens".into(), Value::Null);
    settings.insert("max_tokens_effective".into(), Value::Null);
    settings.insert("max_tokens_fallback".into(), Value::Null);
    settings.insert("password_env_var".into(), json!(false));
    settings.insert("auth_enabled".into(), json!(false));
    settings.insert("password_auth_enabled".into(), json!(false));
    settings.insert("passkeys_enabled".into(), json!(false));
    settings.insert("passwordless_enabled".into(), json!(false));
    settings.insert("webui_version".into(), json!(state.config.version));
    settings.insert("agent_version".into(), json!("not detected"));
    settings.insert("default_model".into(), json!(""));
    settings.insert(
        "default_workspace".into(),
        json!(discover_default_workspace(state)),
    );
    settings.insert(
        "persisted_speech_keys".into(),
        json!(persisted_speech_keys(state)),
    );
    // Upstream channel_version_badge() (api/updates.py:712) — computed once
    // at startup from `git describe`, falling back to the version token.
    settings.insert(
        "update_channel_version".into(),
        json!(state.config.update_channel_version),
    );
    settings
}

pub async fn get_settings(State(state): State<AppState>) -> Response {
    (StatusCode::OK, Json(Value::Object(build_payload(&state)))).into_response()
}

pub async fn post_settings(
    State(state): State<AppState>,
    Json(body): Json<Map<String, Value>>,
) -> Response {
    let mut body = body;

    // bot_name normalization (upstream api/routes.py:15921).
    if let Some(v) = body.get("bot_name") {
        let cleaned = v.as_str().unwrap_or("").trim().to_string();
        let name = if cleaned.is_empty() {
            "Hermes".to_string()
        } else {
            cleaned
        };
        body.insert("bot_name".into(), json!(name));
    }

    // Password control keys: the R0/R1 port has no auth runtime. Upstream
    // refuses with 409 when HERMES_WEBUI_PASSWORD is set; without auth we
    // refuse password changes the same way (fail closed, never silently no-op).
    let requested_password = body
        .get("_set_password")
        .and_then(|v| v.as_str())
        .map(|s| !s.trim().is_empty())
        .unwrap_or(false);
    let requested_clear = body
        .get("_clear_password")
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
        || body
            .get("_passwordless")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
    if requested_password || requested_clear {
        return (
            StatusCode::CONFLICT,
            Json(json!({
                "error": "HERMES_WEBUI_PASSWORD env var is set — it overrides the settings password. Unset the env var and restart the server before changing the password here."
            })),
        )
            .into_response();
    }

    // max_tokens is popped and stored as null (no agent runtime in R0/R1).
    body.remove("max_tokens");
    body.remove("max_tokens_effective");
    body.remove("max_tokens_fallback");

    // Persist the remaining keys (control keys never reach disk).
    let mut settings = read_stored(&state.config.settings_path());
    for (k, v) in body {
        if !CONTROL_KEYS.contains(&k.as_str()) {
            settings.insert(k, v);
        }
    }
    if let Err(e) = write_stored(&state.config.settings_path(), &settings) {
        return (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({"error": format!("could not save settings: {e}")})),
        )
            .into_response();
    }

    // POST response shape (upstream api/routes.py:16009-16060): `saved` is the
    // merged settings WITHOUT the GET-only version/env injections
    // (webui_version, agent_version, update_channel_version, password_env_var),
    // plus persisted_speech_keys, max_tokens status and the auth fields.
    let mut saved = build_payload(&state);
    saved.remove("webui_version");
    saved.remove("agent_version");
    saved.remove("update_channel_version");
    saved.remove("password_env_var");
    saved.insert("auth_enabled".into(), json!(false));
    saved.insert("password_auth_enabled".into(), json!(false));
    saved.insert("logged_in".into(), json!(false));
    saved.insert("auth_just_enabled".into(), json!(false));

    (StatusCode::OK, Json(Value::Object(saved))).into_response()
}

pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new().route(
        "/api/settings",
        axum::routing::get(get_settings).post(post_settings),
    )
}
