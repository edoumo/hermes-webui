//! Shared application state.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Instant;

use crate::config::Config;

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
}

impl AppState {
    pub fn new(config: Config, started_at: Instant) -> Self {
        Self {
            config,
            server_started_at: started_at,
            requests_total: Arc::new(AtomicU64::new(0)),
            last_request_at: Arc::new(AtomicU64::new(0)),
            http_client: reqwest::Client::new(),
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
