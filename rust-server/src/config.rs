//! Application configuration, mirroring the upstream Python env contract
//! (api/config.py) for the subset needed by the R0/R1 bootstrap.

use std::path::{Path, PathBuf};

use serde::Serialize;

use crate::error::AppError;

/// Server configuration resolved once at startup.
#[derive(Clone, Debug, Serialize)]
pub struct Config {
    pub host: String,
    pub port: u16,
    /// Absolute path to the upstream repo root (contains `static/`).
    pub repo_dir: PathBuf,
    /// Version token substituted for `__WEBUI_VERSION__` in index.html.
    pub version: String,
    /// Max upload size in bytes, substituted for `__MAX_UPLOAD_BYTES__`.
    pub max_upload_bytes: u64,
    /// State directory (upstream: HERMES_WEBUI_STATE_DIR, default ~/.hermes/webui).
    pub state_dir: PathBuf,
    /// Channel-scoped display badge (upstream: channel_version_badge()).
    /// Computed once at startup from `git describe` in the repo dir, falling
    /// back to the version token when no channel tag is reachable.
    pub update_channel_version: String,
}

impl Config {
    pub fn new(
        host: String,
        port: u16,
        repo_dir: PathBuf,
        version: String,
        max_upload_mb: u64,
        state_dir: PathBuf,
    ) -> Result<Self, AppError> {
        let repo_dir = repo_dir.canonicalize().map_err(|e| {
            AppError::Config(format!("repo_dir {:?} not accessible: {e}", repo_dir))
        })?;
        let static_dir = repo_dir.join("static");
        if !static_dir.is_dir() {
            return Err(AppError::Config(format!(
                "static/ not found under repo_dir {:?}",
                repo_dir
            )));
        }
        // Upstream channel_version_badge() runs `git describe` in the repo dir
        // (api/updates.py:712). Mirror it: read the describe string once at
        // startup, falling back to the version token when git is unavailable
        // or no tag is reachable (fresh clone / Docker image).
        let update_channel_version = git_describe(&repo_dir).unwrap_or_else(|| version.clone());
        Ok(Self {
            host,
            port,
            repo_dir,
            version,
            max_upload_bytes: max_upload_mb * 1024 * 1024,
            state_dir,
            update_channel_version,
        })
    }

    pub fn static_dir(&self) -> PathBuf {
        self.repo_dir.join("static")
    }

    pub fn index_html_path(&self) -> PathBuf {
        self.static_dir().join("index.html")
    }

    /// Upstream: `SETTINGS_FILE = STATE_DIR / "settings.json"` (api/config.py:89).
    pub fn settings_path(&self) -> PathBuf {
        self.state_dir.join("settings.json")
    }
}

/// Run `git describe` in `dir` and return the first line, or None on any
/// failure (git missing, not a repo, no tag reachable). Mirrors the upstream
/// `channel_version_badge()` (api/updates.py:739): `git describe --tags
/// --match <channel_glob>` with the stable channel glob `v*` (default channel).
fn git_describe(dir: &Path) -> Option<String> {
    let out = std::process::Command::new("git")
        .arg("describe")
        .arg("--tags")
        .arg("--match")
        .arg("v*")
        .current_dir(dir)
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let s = String::from_utf8(out.stdout).ok()?;
    let first = s.lines().next()?.trim().to_string();
    if first.is_empty() {
        None
    } else {
        Some(first)
    }
}
