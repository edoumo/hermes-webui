//! Tests WebAuthn / passkeys (Track C R3).
//!
//! Couverture :
//! - Feature flag off → 404 sur toutes les routes passkey.
//! - Auth désactivée → 400.
//! - Routes : options sans passkeys → 400, login credential inconnu → 401,
//!   delete inconnu → 404, delete dernier passkey sans password → 409,
//!   liste → {credentials: []}.
//! - Challenge store : single-use, TTL, eviction par contexte/global.
//! - Credential store : round-trip JSON format upstream, delete.
//!
//! Le round-trip crypto complet (attestation/assertion réelles) nécessite un
//! authenticator : couvert par la recette navigateur documentée dans
//! docs/rust-port/r3-webauthn.md (webauthn-rs fournit le fake generator pour
//! les CredentialIDs, pas d'attestation complète).

use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

use axum::body::Body;
use axum::http::{Request, StatusCode};
use serde_json::{json, Value};
use tower::ServiceExt;

use hermes_webui_rust::app::build_router;
use hermes_webui_rust::auth::webauthn::{ChallengeStore, PasskeyStore};
use hermes_webui_rust::config::Config;
use hermes_webui_rust::state::AppState;

static DIR_SEQ: AtomicU64 = AtomicU64::new(0);

/// Les tests qui manipulent HERMES_WEBUI_PASSKEY / HERMES_WEBUI_PASSWORD
/// doivent être sérialisés (env var globale, course entre tests parallèles).
static ENV_LOCK: std::sync::OnceLock<tokio::sync::Mutex<()>> = std::sync::OnceLock::new();

fn env_lock() -> &'static tokio::sync::Mutex<()> {
    ENV_LOCK.get_or_init(|| tokio::sync::Mutex::new(()))
}

fn test_state() -> AppState {
    std::env::remove_var("HERMES_WEBUI_PASSWORD");
    std::env::remove_var("HERMES_WEBUI_PASSKEY");
    let repo_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .canonicalize()
        .expect("repo dir");
    let seq = DIR_SEQ.fetch_add(1, Ordering::Relaxed);
    let state_dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-wa-test-{}-{seq}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&state_dir);
    let config = Config::new(
        "127.0.0.1".into(),
        0,
        repo_dir,
        "exp-v0.52.192".into(),
        20,
        state_dir.clone(),
    )
    .expect("config");
    AppState::new(config, std::time::Instant::now())
}

async fn get(state: &AppState, path: &str) -> (StatusCode, Value) {
    let resp = build_router(state.clone())
        .oneshot(
            Request::builder()
                .method("GET")
                .uri(path)
                .header("host", "localhost")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, v)
}

async fn post(state: &AppState, path: &str, body: Value) -> (StatusCode, Value) {
    let resp = build_router(state.clone())
        .oneshot(
            Request::builder()
                .method("POST")
                .uri(path)
                .header("host", "localhost")
                .header("content-type", "application/json")
                .body(Body::from(serde_json::to_vec(&body).unwrap()))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = resp.status();
    let bytes = axum::body::to_bytes(resp.into_body(), 1 << 20)
        .await
        .unwrap();
    let v = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    (status, v)
}

// ── Feature flag ────────────────────────────────────────────────────────────

#[tokio::test]
async fn flag_off_all_routes_404() {
    let _guard = env_lock().lock().await;
    let state = test_state();
    std::env::set_var("HERMES_WEBUI_PASSKEY", "0");
    let (s1, _) = get(&state, "/api/auth/passkey/options").await;
    assert_eq!(s1, StatusCode::NOT_FOUND);
    let (s2, _) = post(&state, "/api/auth/passkey/login", json!({})).await;
    assert_eq!(s2, StatusCode::NOT_FOUND);
    let (s3, _) = get(&state, "/api/auth/passkey/register/options").await;
    assert_eq!(s3, StatusCode::NOT_FOUND);
    let (s4, _) = post(&state, "/api/auth/passkey/register", json!({})).await;
    assert_eq!(s4, StatusCode::NOT_FOUND);
    let (s5, _) = post(&state, "/api/auth/passkey/delete", json!({})).await;
    assert_eq!(s5, StatusCode::NOT_FOUND);
    // Liste : disabled, pas 404 (upstream).
    let (s6, v6) = get(&state, "/api/auth/passkeys").await;
    assert_eq!(s6, StatusCode::OK);
    assert_eq!(v6["disabled"], true);
    assert_eq!(v6["credentials"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn flag_on_auth_off_400() {
    let _guard = env_lock().lock().await;
    let state = test_state();
    std::env::set_var("HERMES_WEBUI_PASSKEY", "1");
    // Auth désactivée (pas de password_hash) → 400 (upstream is_auth_enabled).
    let (s1, _) = get(&state, "/api/auth/passkey/options").await;
    assert_eq!(s1, StatusCode::BAD_REQUEST);
    let (s2, _) = get(&state, "/api/auth/passkey/register/options").await;
    assert_eq!(s2, StatusCode::BAD_REQUEST);
}

// ── Routes (flag on, auth on) ───────────────────────────────────────────────

#[tokio::test]
async fn options_without_passkeys_400() {
    let _guard = env_lock().lock().await;
    let state = test_state();
    std::env::set_var("HERMES_WEBUI_PASSKEY", "1");
    // Active l'auth : écrit un password_hash dans settings.json.
    let settings_dir = state.config.state_dir.clone();
    std::fs::create_dir_all(&settings_dir).unwrap();
    std::fs::write(
        settings_dir.join("settings.json"),
        r#"{"password_hash":"deadbeef"}"#,
    )
    .unwrap();
    let (s, v) = get(&state, "/api/auth/passkey/options").await;
    assert_eq!(s, StatusCode::BAD_REQUEST, "body: {v}");
    assert!(v["error"].as_str().unwrap().contains("No passkeys"));
}

#[tokio::test]
async fn login_unknown_credential_401() {
    let _guard = env_lock().lock().await;
    let state = test_state();
    std::env::set_var("HERMES_WEBUI_PASSKEY", "1");
    let settings_dir = state.config.state_dir.clone();
    std::fs::create_dir_all(&settings_dir).unwrap();
    std::fs::write(
        settings_dir.join("settings.json"),
        r#"{"password_hash":"deadbeef"}"#,
    )
    .unwrap();
    let (s, v) = post(
        &state,
        "/api/auth/passkey/login",
        json!({"id": "unknown-cred", "response": {"clientDataJSON": "e30="}}),
    )
    .await;
    assert_eq!(s, StatusCode::UNAUTHORIZED, "body: {v}");
    assert!(v["error"].as_str().unwrap().contains("Unknown passkey"));
}

#[tokio::test]
async fn delete_unknown_404() {
    let _guard = env_lock().lock().await;
    let state = test_state();
    std::env::set_var("HERMES_WEBUI_PASSKEY", "1");
    let (s, v) = post(&state, "/api/auth/passkey/delete", json!({"id": "nope"})).await;
    assert_eq!(s, StatusCode::NOT_FOUND, "body: {v}");
}

#[tokio::test]
async fn delete_last_passkey_without_password_409() {
    let _guard = env_lock().lock().await;
    let state = test_state();
    std::env::set_var("HERMES_WEBUI_PASSKEY", "1");
    // Un passkey enregistré, pas de password → 409 (upstream).
    let store = PasskeyStore::new(&state.config.state_dir);
    std::fs::create_dir_all(state.config.state_dir.join("sessions")).unwrap();
    store
        .save(&[json!({"id": "only-one", "label": "Passkey"})])
        .unwrap();
    let (s, v) = post(
        &state,
        "/api/auth/passkey/delete",
        json!({"id": "only-one"}),
    )
    .await;
    assert_eq!(s, StatusCode::CONFLICT, "body: {v}");
    assert!(v["error"].as_str().unwrap().contains("Set a password"));
}

#[tokio::test]
async fn delete_last_passkey_with_password_ok() {
    let _guard = env_lock().lock().await;
    let state = test_state();
    std::env::set_var("HERMES_WEBUI_PASSKEY", "1");
    let settings_dir = state.config.state_dir.clone();
    std::fs::create_dir_all(&settings_dir).unwrap();
    std::fs::write(
        settings_dir.join("settings.json"),
        r#"{"password_hash":"deadbeef"}"#,
    )
    .unwrap();
    let store = PasskeyStore::new(&state.config.state_dir);
    store
        .save(&[json!({"id": "only-one", "label": "Passkey"})])
        .unwrap();
    let (s, v) = post(
        &state,
        "/api/auth/passkey/delete",
        json!({"id": "only-one"}),
    )
    .await;
    assert_eq!(s, StatusCode::OK, "body: {v}");
    assert_eq!(v["credentials"].as_array().unwrap().len(), 0);
}

#[tokio::test]
async fn passkeys_list_empty() {
    let _guard = env_lock().lock().await;
    let state = test_state();
    std::env::set_var("HERMES_WEBUI_PASSKEY", "1");
    let (s, v) = get(&state, "/api/auth/passkeys").await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(v["credentials"].as_array().unwrap().len(), 0);
}

// ── Challenge store (unit) ──────────────────────────────────────────────────

#[test]
fn challenge_single_use() {
    let store = ChallengeStore::default();
    store.store("chal-1", "login", "localhost", "http://localhost");
    assert!(store.consume("chal-1", "login").is_some());
    // Second consume → None (single-use).
    assert!(store.consume("chal-1", "login").is_none());
}

#[test]
fn challenge_kind_mismatch_rejected() {
    let store = ChallengeStore::default();
    store.store("chal-1", "register", "localhost", "http://localhost");
    // Consommer avec le mauvais kind → None (et l'entrée est retirée).
    assert!(store.consume("chal-1", "login").is_none());
}

#[test]
fn challenge_ttl_expiry() {
    let store = ChallengeStore::default();
    store.store("chal-old", "login", "localhost", "http://localhost");
    // Force l'expiration : on écrit une entrée avec ts dans le passé.
    {
        let mut data = store.inner.lock().unwrap();
        data.insert(
            "chal-old".into(),
            json!({"kind": "login", "rp_id": "localhost", "origin": "http://localhost", "ts": 0.0}),
        );
    }
    // prune au prochain store → l'ancienne est retirée.
    store.store("chal-new", "login", "localhost", "http://localhost");
    let data = store.inner.lock().unwrap();
    assert!(!data.contains_key("chal-old"), "TTL expiré doit être purgé");
    assert!(data.contains_key("chal-new"));
}

#[test]
fn challenge_eviction_per_context() {
    let store = ChallengeStore::default();
    // 9 challenges même contexte (max 8) → le plus ancien est évincé.
    for i in 0..9 {
        store.store(&format!("c{i}"), "login", "localhost", "http://localhost");
    }
    let data = store.inner.lock().unwrap();
    let ctx_count = data
        .values()
        .filter(|v| v.get("kind").and_then(|k| k.as_str()) == Some("login"))
        .count();
    assert!(ctx_count <= 8, "max 8 par contexte, got {ctx_count}");
}

// ── Credential store (unit) ────────────────────────────────────────────────

#[test]
fn credential_store_roundtrip() {
    let dir =
        std::env::temp_dir().join(format!("hermes-webui-rust-wa-store-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let store = PasskeyStore::new(&dir);
    store
        .save(&[json!({
            "id": "abc123",
            "label": "Ma clé",
            "sign_count": 3,
            "created_at": 1234.0,
            "last_used_at": Value::Null,
        })])
        .unwrap();
    let creds = store.registered_credentials();
    assert_eq!(creds.len(), 1);
    assert_eq!(creds[0]["id"], "abc123");
    assert_eq!(creds[0]["label"], "Ma clé");
    assert_eq!(creds[0]["sign_count"], 3);
    // La clé publique n'est jamais exposée.
    assert!(creds[0].get("public_key_pem").is_none());
    assert!(creds[0].get("webauthn_rs").is_none());
    // Delete.
    assert!(store.delete("abc123"));
    assert!(!store.delete("abc123"));
    assert_eq!(store.registered_credentials().len(), 0);
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn credential_store_ignores_malformed() {
    let dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-wa-store-malformed-{}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let store = PasskeyStore::new(&dir);
    // Fichier corrompu → liste vide, pas de panic.
    std::fs::write(store.path.clone(), "not json at all").unwrap();
    assert_eq!(store.registered_credentials().len(), 0);
    // Fichier avec entrées invalides → filtrées.
    std::fs::write(
        store.path.clone(),
        r#"[{"id":"ok"},{"label":"no-id"},42,"str"]"#,
    )
    .unwrap();
    assert_eq!(store.registered_credentials().len(), 1);
    let _ = std::fs::remove_dir_all(&dir);
}
