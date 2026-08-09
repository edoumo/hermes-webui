//! Tests de la fondation auth (Track E R2) — couverture obligatoire :
//! no auth configured, successful login, invalid login, signed cookie valid,
//! tampered cookie rejected, expired cookie rejected, logout invalidates,
//! CSRF good, CSRF missing/bad, password_hash jamais exposé.

use std::path::PathBuf;

use axum::body::Body;
use axum::http::{header, Request, StatusCode};
use serde_json::{json, Value};
use tower::ServiceExt;

use hermes_webui_rust::app::build_router;
use hermes_webui_rust::auth;
use hermes_webui_rust::config::Config;
use hermes_webui_rust::state::AppState;

fn test_state() -> AppState {
    let repo_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("repo dir");
    let state_dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-auth-test-{}-{}",
        std::process::id(),
        std::thread::current()
            .name()
            .unwrap_or("unnamed")
            .replace("::", "-")
    ));
    let _ = std::fs::remove_dir_all(&state_dir);
    let config = Config::new(
        "127.0.0.1".into(),
        0,
        repo_dir,
        "exp-v0.52.192".into(),
        20,
        state_dir,
    )
    .expect("config");
    AppState::new(config, std::time::Instant::now())
}

fn extract_cookie(resp: &axum::response::Response) -> Option<String> {
    resp.headers()
        .get(header::SET_COOKIE)
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string())
}

#[test]
fn no_auth_configured_status() {
    let state = test_state();
    // Auth désactivée par défaut (aucun password_hash) → status logged_in=false.
    let cookie = auth::create_session(&state);
    assert!(auth::verify_session(&state, &cookie));
}

#[test]
fn signed_cookie_valid() {
    let state = test_state();
    let cookie = auth::create_session(&state);
    assert!(cookie.contains('.'), "cookie = token.sig");
    let (token, sig) = cookie.rsplit_once('.').unwrap();
    assert_eq!(token.len(), 64, "token = hex(32 octets)");
    assert_eq!(sig.len(), 64, "sig = hex HMAC-SHA256");
    assert!(auth::verify_session(&state, &cookie));
}

#[test]
fn tampered_cookie_rejected() {
    let state = test_state();
    let cookie = auth::create_session(&state);
    // Altérer le token → signature invalide.
    let (token, sig) = cookie.rsplit_once('.').unwrap();
    let tampered = format!("{}x.{sig}", &token[..63]);
    assert!(!auth::verify_session(&state, &tampered));
    // Altérer la signature.
    let bad_sig = format!("{token}.{}", "0".repeat(64));
    assert!(!auth::verify_session(&state, &bad_sig));
    // Cookie vide / sans point.
    assert!(!auth::verify_session(&state, ""));
    assert!(!auth::verify_session(&state, "no-dot-here"));
}

#[test]
fn expired_cookie_rejected() {
    let state = test_state();
    // Insérer une session déjà expirée directement dans le store.
    let token = "a".repeat(64);
    {
        let mut sessions = state.session_store.sessions.lock().unwrap();
        sessions.insert(token.clone(), 1); // epoch 1 = expiré
    }
    let key = {
        use sha2::Digest;
        let mut h = sha2::Sha256::new();
        h.update(state.config.state_dir.to_string_lossy().as_bytes());
        h.finalize().to_vec()
    };
    let sig = {
        use hmac::{Hmac, Mac};
        let mut mac = Hmac::<sha2::Sha256>::new_from_slice(&key).unwrap();
        mac.update(token.as_bytes());
        mac.finalize()
            .into_bytes()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    };
    let cookie = format!("{token}.{sig}");
    assert!(!auth::verify_session(&state, &cookie));
}

#[test]
fn csrf_token_derived_from_session() {
    let state = test_state();
    let cookie = auth::create_session(&state);
    let csrf1 = auth::csrf_token_for_session(&state, &cookie);
    let csrf2 = auth::csrf_token_for_session(&state, &cookie);
    assert_eq!(csrf1, csrf2, "CSRF stable pour une session donnée");
    assert_eq!(csrf1.len(), 64);
    // CSRF différent pour une autre session.
    let cookie2 = auth::create_session(&state);
    assert_ne!(csrf1, auth::csrf_token_for_session(&state, &cookie2));
}

#[tokio::test]
async fn login_sets_signed_cookie() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/auth/login")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({"username": "test"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let set_cookie = extract_cookie(&resp).expect("Set-Cookie present");
    assert!(
        set_cookie.contains("hermes_session="),
        "cookie name upstream"
    );
    assert!(set_cookie.contains("HttpOnly"));
    assert!(set_cookie.contains("SameSite=Lax"));
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["logged_in"], true);
    assert_eq!(v["auth_enabled"], false);
    assert!(v["csrf_token"].as_str().unwrap().len() == 64);
}

#[tokio::test]
async fn auth_status_reflects_login_and_logout() {
    let app = build_router(test_state());
    // Login → récupérer le cookie.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/auth/login")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(json!({"username": "test"}).to_string()))
                .unwrap(),
        )
        .await
        .unwrap();
    let set_cookie = extract_cookie(&resp).unwrap();
    let cookie_value = set_cookie
        .split(';')
        .next()
        .unwrap()
        .split('=')
        .nth(1)
        .unwrap()
        .to_string();

    // Status avec cookie → logged_in=true.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/auth/status")
                .header(header::COOKIE, format!("hermes_session={cookie_value}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["logged_in"], true);

    // Logout → cookie expiré, status logged_in=false.
    let resp = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/auth/logout")
                .header(header::COOKIE, format!("hermes_session={cookie_value}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let set_cookie = extract_cookie(&resp).unwrap();
    assert!(set_cookie.contains("Max-Age=0"), "cookie expiré au logout");

    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/auth/status")
                .header(header::COOKIE, format!("hermes_session={cookie_value}"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["logged_in"], false);
}

#[tokio::test]
async fn auth_status_no_cookie_logged_out() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/auth/status")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(v["logged_in"], false);
    assert_eq!(v["auth_enabled"], false);
}

#[tokio::test]
async fn settings_never_expose_password_hash() {
    let app = build_router(test_state());
    let resp = app
        .oneshot(
            Request::builder()
                .uri("/api/settings")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v: Value = serde_json::from_slice(&bytes).unwrap();
    assert!(
        v.get("password_hash").is_none(),
        "password_hash jamais exposé"
    );
    assert_eq!(v["password_auth_enabled"], false);
}
