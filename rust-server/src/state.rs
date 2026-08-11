//! Shared application state.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use crate::config::Config;

/// In-memory static-file cache, faithful port of the upstream `_STATIC_CACHE`
/// (api/routes.py:16960). Keyed by absolute file path; each entry carries the
/// filesystem signature `(size, mtime_ns)` used to invalidate on change. A hit
/// reuses `raw` / `gz` / `etag` without touching the disk. Entries are shared
/// via `Arc` so concurrent hits never deep-copy the (potentially large) payload.
#[derive(Clone, Default)]
pub struct StaticCache {
    map: Arc<Mutex<HashMap<PathBuf, Arc<StaticCacheEntry>>>>,
}

pub struct StaticCacheEntry {
    pub sig: (u64, u128),
    pub raw: Vec<u8>,
    pub gz: Option<Vec<u8>>,
    pub etag: String,
}

impl StaticCache {
    pub fn new() -> Self {
        Self::default()
    }

    /// Return a cached entry for `path` only if its stored signature matches
    /// `sig` (i.e. the file is unchanged since it was cached). Mirrors
    /// `if cached and cached[0] == sig` in the upstream lookup. Returns an
    /// `Arc` so the caller shares the payload rather than copying it.
    pub fn lookup(&self, path: &PathBuf, sig: (u64, u128)) -> Option<Arc<StaticCacheEntry>> {
        let map = self.map.lock().unwrap();
        map.get(path).filter(|e| e.sig == sig).map(Arc::clone)
    }

    /// Store `(sig, raw, gz, etag)` under `path`. Mirrors
    /// `_STATIC_CACHE[cache_key] = (sig, raw, gz, etag)`.
    pub fn insert(&self, path: PathBuf, entry: StaticCacheEntry) {
        let mut map = self.map.lock().unwrap();
        map.insert(path, Arc::new(entry));
    }
}

/// Process-wide state, mirroring the upstream Python globals
/// (SERVER_START_TIME, accept-loop counters) for the R0/R1 subset.
#[derive(Clone)]
pub struct AppState {
    pub config: Config,
    pub server_started_at: Instant,
    /// Monotonic request counter (upstream: accept_loop_requests_total).
    pub requests_total: Arc<AtomicU64>,
    /// Unix timestamp (seconds) of the last accepted request.
    pub last_request_at: Arc<AtomicU64>,
    /// HTTP client partagé (réutilisé par le client bridge).
    pub http_client: reqwest::Client,
    /// Session store auth (fondation R2, state test isolé).
    pub session_store: crate::auth::SessionStore,
    /// Challenge store WebAuthn (mémoire, TTL 90s, single-use).
    pub challenge_store: crate::auth::webauthn::ChallengeStore,
    /// Login rate limiter (R4) — 5 tentatives / 60 s, fichier .login_attempts.json.
    pub rate_limiter: crate::auth::rate_limit::LoginRateLimiter,
    /// In-memory static-file cache (port of upstream `_STATIC_CACHE`).
    pub static_cache: StaticCache,
}

impl AppState {
    pub fn new(config: Config, started_at: Instant) -> Self {
        let state_dir = config.state_dir.clone();
        Self {
            config,
            server_started_at: started_at,
            requests_total: Arc::new(AtomicU64::new(0)),
            last_request_at: Arc::new(AtomicU64::new(0)),
            http_client: reqwest::Client::new(),
            session_store: crate::auth::SessionStore::default(),
            challenge_store: crate::auth::webauthn::ChallengeStore::default(),
            rate_limiter: crate::auth::rate_limit::LoginRateLimiter::new(state_dir),
            static_cache: StaticCache::new(),
        }
    }

    pub fn record_request(&self) {
        self.requests_total.fetch_add(1, Ordering::Relaxed);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0);
        self.last_request_at.store(now as u64, Ordering::Relaxed);
    }
}
