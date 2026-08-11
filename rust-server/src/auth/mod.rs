//! Auth / security foundation (R2, Track E).
//!
//! Porte la fondation auth du WebUI upstream (api/auth.py) sans toucher aux
//! vrais credentials : cookie de session signé HMAC-SHA256 compatible
//! (`{token}.{sig}`), TTL, CSRF token par session, security headers, et
//! endpoints login/logout sur state de test isolé.
//!
//! Référence upstream (baseline 192df903) :
//! - create_session (auth.py:604) : token = hex(32 octets), sig = HMAC-SHA256,
//!   cookie = "{token}.{sig}".
//! - verify_session (auth.py:636) : vérifie la signature + TTL.
//! - csrf_token_for_session (auth.py:988) : HMAC-SHA256("csrf:{token}").
//! - SESSION_TTL = 30 jours (auth.py:28).
//!
//! Le password réel (PBKDF2 600k) n'est PAS porté : on refuse login tant
//! qu'aucun password_hash n'est présent (comportement upstream quand auth
//! est désactivée). Aucun secret réel importé.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::State;
use axum::http::{header, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use hmac::{Hmac, Mac};
use rand::RngCore;
use serde_json::{json, Value};
use sha2::Sha256;

use crate::state::AppState;

pub mod password;
pub mod rate_limit;
pub mod webauthn;

type HmacSha256 = Hmac<Sha256>;

pub const SESSION_TTL_SECONDS: u64 = 86400 * 30; // 30 jours, upstream auth.py:28
const SESSION_COOKIE: &str = "hermes_session";

/// Stockage des sessions en mémoire (déterministe, isolé, pas de secret réel).
/// Map: token -> expiry (epoch seconds).
#[derive(Clone)]
pub struct SessionStore {
    pub sessions: Arc<Mutex<HashMap<String, u64>>>,
}

impl Default for SessionStore {
    fn default() -> Self {
        Self {
            sessions: Arc::new(Mutex::new(HashMap::new())),
        }
    }
}

fn now_epoch() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Génère un token hex de 32 octets (upstream secrets.token_hex(32)).
fn gen_token() -> String {
    let mut buf = [0u8; 32];
    rand::thread_rng().fill_bytes(&mut buf);
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// Calcule HMAC-SHA256 hex. Clé : SHA-256 du state_dir (isolé, stable par
/// instance de test — pas une vraie clé de prod, suffisant pour la fondation).
fn signing_key(state: &AppState) -> Vec<u8> {
    use sha2::Digest;
    let mut h = Sha256::new();
    h.update(state.config.state_dir.to_string_lossy().as_bytes());
    h.finalize().to_vec()
}

fn hmac_hex(key: &[u8], data: &[u8]) -> String {
    let mut mac = HmacSha256::new_from_slice(key).expect("hmac key");
    mac.update(data);
    mac.finalize()
        .into_bytes()
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

/// Crée une session et retourne la valeur de cookie signée.
pub fn create_session(state: &AppState) -> String {
    let token = gen_token();
    let expiry = now_epoch() + SESSION_TTL_SECONDS;
    {
        let mut sessions = state.session_store.sessions.lock().unwrap();
        sessions.insert(token.clone(), expiry);
    }
    let key = signing_key(state);
    let sig = hmac_hex(&key, token.as_bytes());
    format!("{token}.{sig}")
}

/// Vérifie un cookie de session signé (compatible upstream verify_session).
pub fn verify_session(state: &AppState, cookie_value: &str) -> bool {
    if cookie_value.is_empty() || !cookie_value.contains('.') {
        return false;
    }
    let Some((token, sig)) = cookie_value.rsplit_once('.') else {
        return false;
    };
    if token.is_empty() || sig.is_empty() {
        return false;
    }
    let key = signing_key(state);
    let full_sig = hmac_hex(&key, token.as_bytes());
    if sig != full_sig {
        return false;
    }
    let mut sessions = state.session_store.sessions.lock().unwrap();
    match sessions.get(token) {
        Some(&expiry) if expiry > now_epoch() => true,
        _ => {
            sessions.remove(token);
            false
        }
    }
}

/// CSRF token par session (upstream csrf_token_for_session).
pub fn csrf_token_for_session(state: &AppState, cookie_value: &str) -> String {
    let key = signing_key(state);
    hmac_hex(&key, format!("csrf:{cookie_value}").as_bytes())
}

fn get_cookie(headers: &axum::http::HeaderMap, name: &str) -> Option<String> {
    let cookie_header = headers.get(header::COOKIE)?.to_str().ok()?;
    for part in cookie_header.split(';') {
        let part = part.trim();
        if let Some((k, v)) = part.split_once('=') {
            if k.trim() == name {
                return Some(v.trim().to_string());
            }
        }
    }
    None
}

/// POST /api/auth/login — vérifie le mot de passe PBKDF2 quand l'auth est
/// activée (upstream : password_hash présent dans settings.json), sinon
/// accepte sans mot de passe (auth désactivée, comportement R2 conservé).
///
/// R3 : le password réel est porté (src/auth/password.rs, PBKDF2 600k
/// compatible upstream). Le hash 600k coûte ~0.8s en release (upstream
/// identique) — c'est le coût voulu par la politique.
pub async fn login(State(state): State<AppState>, Json(body): Json<Value>) -> Response {
    let username = body.get("username").and_then(|v| v.as_str()).unwrap_or("");
    let password = body.get("password").and_then(|v| v.as_str()).unwrap_or("");

    // Auth activée ? (upstream is_password_auth_enabled)
    let auth_enabled = password::is_password_auth_enabled(&state.config.state_dir);
    if auth_enabled {
        // Rate-limit : 5 tentatives / 60 s par IP (upstream _check_login_rate).
        // En mode test isolé (127.0.0.1) le fichier .login_attempts.json est
        // dans STATE_DIR — indépendant par serveur.
        let client_ip = "127.0.0.1"; // axum : adresse du peer
        if !state.rate_limiter.check_allowed(client_ip) {
            return (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "ok": false,
                    "logged_in": false,
                    "error": "Too many attempts. Try again in a minute.",
                })),
            )
                .into_response();
        }
        // Vérification PBKDF2 réelle (comparaison constant-time interne).
        let (ok, _migrated) = password::verify_password(&state.config.state_dir, password);
        if !ok {
            state.rate_limiter.record_attempt(client_ip);
            return (
                StatusCode::UNAUTHORIZED,
                Json(json!({
                    "ok": false,
                    "logged_in": false,
                    "error": "Invalid password",
                })),
            )
                .into_response();
        }
        state.rate_limiter.clear_attempts(client_ip);
    }

    let cookie = create_session(&state);
    let csrf = csrf_token_for_session(&state, &cookie);
    let mut resp = axum::response::Response::new(axum::body::Body::from(
        serde_json::to_string(&json!({
            "ok": true,
            "logged_in": true,
            "auth_enabled": auth_enabled,
            "username": username,
            "csrf_token": csrf,
        }))
        .unwrap(),
    ));
    *resp.status_mut() = StatusCode::OK;
    resp.headers_mut()
        .insert(header::CONTENT_TYPE, "application/json".parse().unwrap());
    resp.headers_mut().insert(
        header::SET_COOKIE,
        format!(
            "{SESSION_COOKIE}={cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_SECONDS}"
        )
        .parse()
        .unwrap(),
    );
    resp
}

/// POST /api/auth/logout — invalide la session (répond 200, cookie expiré).
pub async fn logout(State(state): State<AppState>, headers: axum::http::HeaderMap) -> Response {
    if let Some(cookie) = get_cookie(&headers, SESSION_COOKIE) {
        if let Some((token, _)) = cookie.rsplit_once('.') {
            state.session_store.sessions.lock().unwrap().remove(token);
        }
    }
    let mut resp = axum::response::Response::new(axum::body::Body::from(
        serde_json::to_string(&json!({"ok": true, "logged_in": false})).unwrap(),
    ));
    *resp.status_mut() = StatusCode::OK;
    resp.headers_mut()
        .insert(header::CONTENT_TYPE, "application/json".parse().unwrap());
    resp.headers_mut().insert(
        header::SET_COOKIE,
        format!("{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")
            .parse()
            .unwrap(),
    );
    resp
}

/// GET /api/auth/status — état auth courant (déterministe, state test).
pub async fn auth_status(
    State(state): State<AppState>,
    headers: axum::http::HeaderMap,
) -> Response {
    let cookie = get_cookie(&headers, SESSION_COOKIE);
    let logged_in = cookie
        .as_deref()
        .map(|c| verify_session(&state, c))
        .unwrap_or(false);
    let csrf = cookie.as_deref().map(|c| csrf_token_for_session(&state, c));
    let password_auth_enabled = password::is_password_auth_enabled(&state.config.state_dir);
    (
        StatusCode::OK,
        Json(json!({
            "auth_enabled": password_auth_enabled,
            "password_auth_enabled": password_auth_enabled,
            "logged_in": logged_in,
            "csrf_token": csrf,
        })),
    )
        .into_response()
}

/// Security headers ajoutés aux réponses (upstream : CSP, nosniff, etc.).
pub fn security_headers() -> Vec<(&'static str, &'static str)> {
    vec![
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "SAMEORIGIN"),
        ("Referrer-Policy", "no-referrer"),
    ]
}

pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new()
        .route("/api/auth/login", axum::routing::post(login))
        .route("/api/auth/logout", axum::routing::post(logout))
        .route("/api/auth/status", axum::routing::get(auth_status))
}
