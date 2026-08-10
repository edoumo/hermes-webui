//! Router assembly.

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::Value;

use crate::hermes::bridge;
use crate::state::AppState;

/// GET /api/bridge/health — santé du bridge Hermes (mode réel ou mock).
pub async fn bridge_health(State(state): State<AppState>) -> Response {
    match bridge::bridge_health(&state).await {
        Ok(payload) => (StatusCode::OK, Json(payload)).into_response(),
        Err(e) => bridge::bridge_error_response("HermesUnavailable", &e.to_string()),
    }
}

/// POST /api/chat/proxy — relaye un message au bridge (flux SSE).
pub async fn chat_proxy(State(state): State<AppState>, body: Json<Value>) -> Response {
    match bridge::proxy_chat(&state, body.0).await {
        Ok(resp) => resp,
        Err(e) => bridge::bridge_error_response("HermesUnavailable", &e.to_string()),
    }
}

/// Build the full HTTP router for the Rust port.
pub fn build_router(state: AppState) -> axum::Router {
    axum::Router::new()
        .merge(crate::routes::health::router())
        .merge(crate::routes::static_files::router())
        .merge(crate::routes::index::router())
        .merge(crate::routes::settings::router())
        .merge(crate::routes::workspace::router())
        .merge(crate::routes::sessions::router())
        .merge(crate::routes::uploads::router())
        .merge(crate::auth::router())
        .merge(
            axum::Router::new()
                .route("/api/bridge/health", axum::routing::get(bridge_health))
                .route("/api/chat/proxy", axum::routing::post(chat_proxy)),
        )
        .with_state(state)
        .layer(tower_http::trace::TraceLayer::new_for_http())
}
