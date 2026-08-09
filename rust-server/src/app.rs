//! Router assembly.

use axum::Router;
use tower_http::trace::TraceLayer;

use crate::routes;
use crate::state::AppState;

/// Build the full HTTP router for the Rust port.
pub fn build_router(state: AppState) -> Router {
    Router::new()
        .merge(routes::health::router())
        .merge(routes::static_files::router())
        .merge(routes::index::router())
        .merge(routes::settings::router())
        .with_state(state)
        .layer(TraceLayer::new_for_http())
}
