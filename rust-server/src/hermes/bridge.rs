//! Client HTTP du bridge Hermes Agent (protocole v1, voir bridge/PROTOCOL.md).
//!
//! Le bridge est un serveur Python localhost (HTTP + SSE) qui wrappe AIAgent.
//! Rust l'appelle en HTTP POST /v1/chat (SSE) et relaye les events au client.

use axum::body::Body;
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use serde_json::{json, Value};

use crate::state::AppState;

/// Appelle le bridge (POST /v1/chat) et relève la réponse HTTP.
/// Le bridge renvoie un flux SSE ; le corps est transmis tel quel (chunked).
pub async fn proxy_chat(state: &AppState, body: Value) -> Result<Response, crate::error::AppError> {
    let bridge_url = state.config.bridge_url();
    let client = state.http_client.clone();

    let request = client
        .post(format!("{bridge_url}/v1/chat"))
        .header(header::CONTENT_TYPE, "application/json")
        .json(&body)
        .send()
        .await
        .map_err(|e| {
            crate::error::AppError::HermesUnavailable(format!("bridge unreachable: {e}"))
        })?;

    let status = request.status();
    let content_type = request
        .headers()
        .get(header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("text/event-stream")
        .to_string();
    let bytes = request.bytes().await.map_err(|e| {
        crate::error::AppError::HermesProtocolError(format!("bridge read error: {e}"))
    })?;

    let mut resp = Response::new(Body::from(bytes));
    *resp.status_mut() = status;
    resp.headers_mut()
        .insert(header::CONTENT_TYPE, content_type.parse().unwrap());
    resp.headers_mut()
        .insert(header::CACHE_CONTROL, "no-cache".parse().unwrap());
    Ok(resp)
}

/// GET /v1/health du bridge.
pub async fn bridge_health(state: &AppState) -> Result<Value, crate::error::AppError> {
    let bridge_url = state.config.bridge_url();
    let client = state.http_client.clone();
    let resp = client
        .get(format!("{bridge_url}/v1/health"))
        .send()
        .await
        .map_err(|e| {
            crate::error::AppError::HermesUnavailable(format!("bridge unreachable: {e}"))
        })?;
    let status = resp.status();
    let json: Value = resp.json().await.map_err(|e| {
        crate::error::AppError::HermesProtocolError(format!("bridge bad health json: {e}"))
    })?;
    if !status.is_success() {
        return Ok(json!({
            "status": "error",
            "status_code": status.as_u16(),
            "service": "hermes-bridge",
            "error": json.get("error").cloned().unwrap_or(Value::Null),
        }));
    }
    Ok(json)
}

/// Réponse d'erreur standardisée pour les échecs bridge (jamais de backtrace).
pub fn bridge_error_response(error_class: &str, message: &str) -> Response {
    let (status, msg) = match error_class {
        "BadRequest" => (StatusCode::BAD_REQUEST, message.to_string()),
        "Unauthorized" => (StatusCode::UNAUTHORIZED, message.to_string()),
        "Forbidden" => (StatusCode::FORBIDDEN, message.to_string()),
        "NotFound" => (StatusCode::NOT_FOUND, message.to_string()),
        "Conflict" => (StatusCode::CONFLICT, message.to_string()),
        "PayloadTooLarge" => (StatusCode::PAYLOAD_TOO_LARGE, message.to_string()),
        "HermesUnavailable" => (StatusCode::SERVICE_UNAVAILABLE, message.to_string()),
        "HermesProtocolError" => (StatusCode::BAD_GATEWAY, message.to_string()),
        _ => (StatusCode::INTERNAL_SERVER_ERROR, message.to_string()),
    };
    (status, axum::Json(json!({"error": msg}))).into_response()
}

/// Headers utiles à la réponse (sécurité, observabilité).
pub fn default_headers() -> HeaderMap {
    let mut h = HeaderMap::new();
    h.insert("X-Content-Type-Options", "nosniff".parse().unwrap());
    h
}
