//! Hermes WebUI Rust port — entry point.
//!
//! R0/R1 bootstrap: starts an axum server, exposes `/health`, serves the
//! upstream frontend assets from `static/`, logs structured events, and shuts
//! down cleanly on SIGTERM/SIGINT.
//!
//! Reference baseline: nesquena/hermes-webui @ 192df903 (exp-v0.52.192).

use std::net::SocketAddr;
use std::path::PathBuf;
use std::time::Instant;

use clap::Parser;
use hermes_webui_rust::app;
use hermes_webui_rust::config::Config;
use hermes_webui_rust::state;
use tracing::info;

#[derive(Parser, Debug)]
#[command(name = "hermes-webui-rust", about = "Rust backend for Hermes WebUI")]
struct Cli {
    /// Host/interface to bind (default: 127.0.0.1)
    #[arg(long, env = "HERMES_WEBUI_HOST", default_value = "127.0.0.1")]
    host: String,

    /// Port to listen on (default: 8787)
    #[arg(long, env = "HERMES_WEBUI_PORT", default_value_t = 8787)]
    port: u16,

    /// Path to the upstream repo root (contains static/ and api/)
    #[arg(long, env = "HERMES_WEBUI_REPO_DIR", default_value = "..")]
    repo_dir: PathBuf,

    /// State directory (upstream: HERMES_WEBUI_STATE_DIR)
    #[arg(
        long,
        env = "HERMES_WEBUI_STATE_DIR",
        default_value = "/tmp/hermes-webui-rust-state"
    )]
    state_dir: PathBuf,

    /// WebUI version token substituted into index.html (__WEBUI_VERSION__)
    #[arg(long, env = "HERMES_WEBUI_VERSION", default_value = "exp-v0.52.192")]
    version: String,

    /// Max upload size in bytes (__MAX_UPLOAD_BYTES__ substitution)
    #[arg(long, env = "HERMES_WEBUI_MAX_UPLOAD_MB", default_value_t = 20)]
    max_upload_mb: u64,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "hermes_webui_rust=info,tower_http=info".into()),
        )
        .init();

    let cli = Cli::parse();
    let started_at = Instant::now();
    let config = Config::new(
        cli.host,
        cli.port,
        cli.repo_dir,
        cli.version,
        cli.max_upload_mb,
        cli.state_dir,
    )?;

    let state = state::AppState::new(config, started_at);
    let addr: SocketAddr = format!("{}:{}", state.config.host, state.config.port).parse()?;
    let app = app::build_router(state);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!(%addr, "hermes-webui-rust listening");

    axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await?;

    info!("shutdown complete");
    Ok(())
}

/// Wait for SIGTERM or SIGINT, then return.
async fn shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("failed to install Ctrl+C handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("failed to install SIGTERM handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
    info!("shutdown signal received");
}
