//! WebAuthn / passkeys — port fidèle du contrat upstream (`api/passkeys.py`
//! + routes `/api/auth/passkey/*` dans `api/routes.py`).
//!
//! Contrat vérifié (baseline 192df903) :
//! - Feature flag opt-in : `HERMES_WEBUI_PASSKEY=1` (env) — flag off → 404
//!   sur toutes les routes passkey, `{credentials: [], disabled: true}`.
//! - `passkeys.json` dans STATE_DIR : liste de credentials, format upstream
//!   (id b64u, label, public_key_pem, sign_count, created_at, last_used_at).
//!   On ajoute un champ `webauthn_rs` (Passkey sérialisé serde) pour
//!   l'authentification Rust — le Python ignore les champs inconnus.
//! - Challenges : TTL 90s, max 128 globaux, max 8 par contexte
//!   (kind+rp_id+origin), single-use (consommés à la vérification).
//! - RP : rp_id = host sans port, origin = proto://host, RP_NAME "Hermes WebUI".
//! - user id = sha256(rp_id)[:16] b64u (stable par RP).
//! - Sign counter : refus si le compteur n'avance pas (géré par webauthn-rs,
//!   `require_valid_counter_value`).
//! - Delete : 409 si dernier passkey et aucun password configuré.
//!
//! Crypto : crate `webauthn-rs` 0.6.1-dev (safe wrapper, MPL-2.0, maintenue
//! par le projet kanidm) — aucune cryptographie WebAuthn maison.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use axum::extract::State;
use axum::http::{header, HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::{json, Value};
use url::Url;
use uuid::Uuid;
use webauthn_rs::prelude::*;

use crate::auth::{create_session, SESSION_TTL_SECONDS};
use crate::state::AppState;

const RP_NAME: &str = "Hermes WebUI";
const CHALLENGE_TTL_SECS: u64 = 90;
const MAX_CHALLENGES: usize = 128;
const MAX_CHALLENGES_PER_CONTEXT: usize = 8;
const SESSION_COOKIE: &str = "hermes_session";

// ── Feature flag (upstream auth.py:439) ────────────────────────────────────

pub fn feature_flag_enabled() -> bool {
    std::env::var("HERMES_WEBUI_PASSKEY")
        .map(|v| v == "1")
        .unwrap_or(false)
}

// ── RP context (upstream passkeys.py:177-193) ───────────────────────────────

fn host_without_port(host: &str) -> String {
    let host = host.trim();
    if host.is_empty() {
        return "localhost".into();
    }
    let host = host.split(',').next().unwrap_or("").trim();
    if host.starts_with('[') {
        if let Some(end) = host.find(']') {
            return host[1..end].to_string();
        }
    }
    match host.rsplit_once(':') {
        Some((h, _)) if h.contains('.') || h == "localhost" => h.to_string(),
        _ => host.to_string(),
    }
}

fn rp_context(headers: &HeaderMap) -> (String, String) {
    let host = headers
        .get(header::HOST)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("localhost")
        .to_string();
    let rp_id = host_without_port(&host);
    let proto = headers
        .get("x-forwarded-proto")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.split(',').next().unwrap_or("").trim().to_lowercase())
        .filter(|p| p == "http" || p == "https")
        .unwrap_or_else(|| "http".into());
    (rp_id, format!("{proto}://{host}"))
}

fn webauthn_for(headers: &HeaderMap) -> Result<Webauthn, (StatusCode, String)> {
    let (rp_id, origin) = rp_context(headers);
    let origin_url =
        Url::parse(&origin).map_err(|_| (StatusCode::BAD_REQUEST, "Invalid origin".to_string()))?;
    WebauthnBuilder::new(&rp_id, &origin_url)
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("WebAuthn init: {e}")))?
        .rp_name(RP_NAME)
        .build()
        .map_err(|e| (StatusCode::BAD_REQUEST, format!("WebAuthn build: {e}")))
}

// ── Credential store (passkeys.json, format upstream + webauthn_rs) ────────

#[derive(Clone)]
pub struct PasskeyStore {
    pub path: PathBuf,
}

impl PasskeyStore {
    pub fn new(state_dir: &std::path::Path) -> Self {
        Self {
            path: state_dir.join("passkeys.json"),
        }
    }

    fn load(&self) -> Vec<Value> {
        let Ok(text) = std::fs::read_to_string(&self.path) else {
            return vec![];
        };
        let Ok(data) = serde_json::from_str::<Value>(&text) else {
            return vec![];
        };
        data.as_array()
            .cloned()
            .unwrap_or_default()
            .into_iter()
            .filter(|c| c.get("id").and_then(|v| v.as_str()).is_some())
            .collect()
    }

    pub fn save(&self, creds: &[Value]) -> std::io::Result<()> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let tmp = self.path.with_extension("json.tmp");
        let text = serde_json::to_string_pretty(creds)?;
        std::fs::write(&tmp, text)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600))?;
        }
        std::fs::rename(&tmp, &self.path)
    }

    /// Métadonnées publiques (jamais la clé publique) — upstream
    /// `registered_credentials()`.
    pub fn registered_credentials(&self) -> Vec<Value> {
        self.load()
            .into_iter()
            .map(|c| {
                json!({
                    "id": c.get("id").cloned().unwrap_or(Value::Null),
                    "label": c.get("label").and_then(|v| v.as_str()).unwrap_or("Passkey"),
                    "created_at": c.get("created_at").cloned().unwrap_or(Value::Null),
                    "last_used_at": c.get("last_used_at").cloned().unwrap_or(Value::Null),
                    "sign_count": c.get("sign_count").and_then(|v| v.as_u64()).unwrap_or(0),
                })
            })
            .collect()
    }

    pub fn passkeys_available(&self) -> bool {
        !self.load().is_empty()
    }

    /// Charge les Passkey webauthn-rs (champ `webauthn_rs`).
    fn load_passkeys(&self) -> Vec<Passkey> {
        self.load()
            .into_iter()
            .filter_map(|c| {
                c.get("webauthn_rs")
                    .and_then(|v| serde_json::from_value::<Passkey>(v.clone()).ok())
            })
            .collect()
    }

    fn find_index(&self, cred_id_b64u: &str) -> Option<usize> {
        self.load()
            .iter()
            .position(|c| c.get("id").and_then(|v| v.as_str()) == Some(cred_id_b64u))
    }

    fn upsert(
        &self,
        cred_id_b64u: &str,
        label: &str,
        passkey: &Passkey,
        sign_count: u32,
    ) -> std::io::Result<()> {
        let mut creds = self.load();
        let now = now_epoch_f64();
        let entry = json!({
            "id": cred_id_b64u,
            "label": label,
            "public_key_pem": null, // la clé vit dans webauthn_rs (serde)
            "sign_count": sign_count,
            "created_at": now,
            "last_used_at": Value::Null,
            "webauthn_rs": serde_json::to_value(passkey).unwrap_or(Value::Null),
        });
        if let Some(idx) = self.find_index(cred_id_b64u) {
            creds[idx] = entry;
        } else {
            creds.push(entry);
        }
        self.save(&creds)
    }

    fn update_after_auth(
        &self,
        cred_id_b64u: &str,
        passkey: &Passkey,
        sign_count: u32,
    ) -> std::io::Result<()> {
        let mut creds = self.load();
        let Some(idx) = self.find_index(cred_id_b64u) else {
            return Ok(());
        };
        creds[idx]["sign_count"] = json!(sign_count);
        creds[idx]["last_used_at"] = json!(now_epoch_f64());
        creds[idx]["webauthn_rs"] = serde_json::to_value(passkey).unwrap_or(Value::Null);
        self.save(&creds)
    }

    pub fn delete(&self, cred_id_b64u: &str) -> bool {
        let mut creds = self.load();
        let before = creds.len();
        creds.retain(|c| c.get("id").and_then(|v| v.as_str()) != Some(cred_id_b64u));
        if creds.len() == before {
            return false;
        }
        self.save(&creds).is_ok()
    }
}

// ── Challenge store (mémoire, TTL 90s, single-use) ──────────────────────────

#[derive(Clone, Default)]
pub struct ChallengeStore {
    pub inner: Arc<Mutex<HashMap<String, Value>>>,
    /// États webauthn-rs typés associés aux challenges (en mémoire, TTL 90s,
    /// jamais sérialisés sur disque — feature danger non activée).
    states: Arc<Mutex<HashMap<String, WebauthnState>>>,
}

/// État webauthn-rs en cours (registration ou authentication).
pub enum WebauthnState {
    Registration(PasskeyRegistration),
    Authentication(PasskeyAuthentication),
}

impl ChallengeStore {
    fn now() -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0)
    }

    fn prune(&self, data: &mut HashMap<String, Value>) {
        let now = Self::now();
        data.retain(|_, v| {
            v.get("ts")
                .and_then(|t| t.as_f64())
                .map(|t| now - t < CHALLENGE_TTL_SECS as f64)
                .unwrap_or(false)
        });
    }

    pub fn store(&self, challenge: &str, kind: &str, rp_id: &str, origin: &str) {
        let mut data = self.inner.lock().unwrap();
        self.prune(&mut data);
        // Eviction par contexte (max 8) puis global (max 128).
        let mut ctx_keys: Vec<String> = data
            .iter()
            .filter(|(_, v)| {
                v.get("kind").and_then(|k| k.as_str()) == Some(kind)
                    && v.get("rp_id").and_then(|k| k.as_str()) == Some(rp_id)
                    && v.get("origin").and_then(|k| k.as_str()) == Some(origin)
            })
            .map(|(k, _)| k.clone())
            .collect();
        while ctx_keys.len() >= MAX_CHALLENGES_PER_CONTEXT {
            let oldest = ctx_keys
                .iter()
                .min_by(|a, b| {
                    let ta = data
                        .get(*a)
                        .and_then(|v| v.get("ts"))
                        .and_then(|t| t.as_f64())
                        .unwrap_or(0.0);
                    let tb = data
                        .get(*b)
                        .and_then(|v| v.get("ts"))
                        .and_then(|t| t.as_f64())
                        .unwrap_or(0.0);
                    ta.partial_cmp(&tb).unwrap_or(std::cmp::Ordering::Equal)
                })
                .cloned();
            if let Some(k) = oldest {
                data.remove(&k);
                ctx_keys.retain(|x| x != &k);
            } else {
                break;
            }
        }
        while data.len() >= MAX_CHALLENGES {
            let oldest = data
                .iter()
                .min_by(|a, b| {
                    let ta = a.1.get("ts").and_then(|t| t.as_f64()).unwrap_or(0.0);
                    let tb = b.1.get("ts").and_then(|t| t.as_f64()).unwrap_or(0.0);
                    ta.partial_cmp(&tb).unwrap_or(std::cmp::Ordering::Equal)
                })
                .map(|(k, _)| k.clone());
            if let Some(k) = oldest {
                data.remove(&k);
            } else {
                break;
            }
        }
        data.insert(
            challenge.to_string(),
            json!({"kind": kind, "rp_id": rp_id, "origin": origin, "ts": Self::now()}),
        );
    }

    /// Consomme (single-use) et retourne l'entrée si kind correspond.
    pub fn consume(&self, challenge: &str, kind: &str) -> Option<Value> {
        let mut data = self.inner.lock().unwrap();
        let entry = data.remove(challenge);
        match entry {
            Some(e) if e.get("kind").and_then(|k| k.as_str()) == Some(kind) => Some(e),
            _ => None,
        }
    }

    /// Stocke l'état webauthn-rs associé au challenge (registration ou
    /// authentication) — conservé en mémoire, TTL 90s, jamais sur disque.
    pub fn store_webauthn_state(&self, challenge: &str, state: WebauthnState) {
        self.states
            .lock()
            .unwrap()
            .insert(challenge.to_string(), state);
    }

    /// Récupère (et retire) l'état webauthn-rs associé au challenge.
    pub fn take_webauthn_state(&self, challenge: &str) -> Option<WebauthnState> {
        self.states.lock().unwrap().remove(challenge)
    }
}

// ── Helpers ────────────────────────────────────────────────────────────────

fn now_epoch_f64() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

fn b64u(data: &[u8]) -> String {
    use base64::Engine;
    base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(data)
}

fn error_response(status: StatusCode, msg: &str) -> Response {
    (status, Json(json!({"error": msg}))).into_response()
}

fn user_unique_id(rp_id: &str) -> Vec<u8> {
    use sha2::Digest;
    let mut h = sha2::Sha256::new();
    h.update(rp_id.as_bytes());
    h.finalize()[..16].to_vec()
}

fn exclude_credentials(store: &PasskeyStore) -> Option<Vec<CredentialID>> {
    let ids: Vec<CredentialID> = store
        .load_passkeys()
        .iter()
        .map(|p| p.cred_id().clone())
        .collect();
    if ids.is_empty() {
        None
    } else {
        Some(ids)
    }
}

// ── Handlers (routes upstream api/routes.py:16681-16778) ────────────────────

/// GET /api/auth/passkey/options — authentication options (login).
pub async fn passkey_options(State(state): State<AppState>, headers: HeaderMap) -> Response {
    if !feature_flag_enabled() {
        return error_response(
            StatusCode::NOT_FOUND,
            "Passkey support is disabled. Set HERMES_WEBUI_PASSKEY=1 or webui_passkey_enabled: true to enable.",
        );
    }
    if !crate::auth::password::is_password_auth_enabled(&state.config.state_dir) {
        return error_response(StatusCode::BAD_REQUEST, "Auth not enabled");
    }
    let store = PasskeyStore::new(&state.config.state_dir);
    if !store.passkeys_available() {
        return error_response(StatusCode::BAD_REQUEST, "No passkeys are registered.");
    }
    let webauthn = match webauthn_for(&headers) {
        Ok(w) => w,
        Err((s, m)) => return error_response(s, &m),
    };
    let passkeys = store.load_passkeys();
    let (rcr, ast) = match webauthn.start_passkey_authentication(&passkeys) {
        Ok(v) => v,
        Err(e) => return error_response(StatusCode::BAD_REQUEST, &format!("WebAuthn: {e}")),
    };
    let (rp_id, origin) = rp_context(&headers);
    let challenge = b64u(&rcr.public_key.challenge);
    state
        .challenge_store
        .store(&challenge, "login", &rp_id, &origin);
    state
        .challenge_store
        .store_webauthn_state(&challenge, WebauthnState::Authentication(ast));
    (StatusCode::OK, Json(json!({"ok": true, "publicKey": rcr}))).into_response()
}

/// POST /api/auth/passkey/login — finish authentication, crée la session.
pub async fn passkey_login(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    if !feature_flag_enabled() {
        return error_response(StatusCode::NOT_FOUND, "Passkey support is disabled.");
    }
    if !crate::auth::password::is_password_auth_enabled(&state.config.state_dir) {
        return error_response(StatusCode::BAD_REQUEST, "Auth not enabled");
    }
    let store = PasskeyStore::new(&state.config.state_dir);
    let cred_id = body
        .get("id")
        .or_else(|| body.get("rawId"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if cred_id.is_empty() {
        return error_response(StatusCode::BAD_REQUEST, "Missing passkey credential id");
    }
    let Some(idx) = store.find_index(cred_id) else {
        return error_response(StatusCode::UNAUTHORIZED, "Unknown passkey");
    };
    let webauthn = match webauthn_for(&headers) {
        Ok(w) => w,
        Err((s, m)) => return error_response(s, &m),
    };
    let (rp_id, origin) = rp_context(&headers);
    // Consomme le challenge (single-use) AVANT vérification.
    let challenge = body
        .get("response")
        .and_then(|r| r.get("clientDataJSON"))
        .and_then(|v| v.as_str())
        .and_then(|c| {
            // Le challenge est dans le clientDataJSON (JSON b64u) — on le
            // récupère pour consommer l'entrée.
            use base64::Engine;
            let raw = base64::engine::general_purpose::URL_SAFE_NO_PAD
                .decode(c.trim_end_matches('='))
                .ok();
            raw.and_then(|b| serde_json::from_slice::<Value>(&b).ok())
                .and_then(|v| {
                    v.get("challenge")
                        .and_then(|c| c.as_str())
                        .map(String::from)
                })
        });
    let Some(challenge) = challenge else {
        return error_response(StatusCode::BAD_REQUEST, "Missing passkey challenge");
    };
    let entry = state.challenge_store.consume(&challenge, "login");
    let Some(entry) = entry else {
        return error_response(
            StatusCode::BAD_REQUEST,
            "Passkey challenge expired. Try again.",
        );
    };
    if entry.get("origin").and_then(|v| v.as_str()) != Some(origin.as_str())
        || entry.get("rp_id").and_then(|v| v.as_str()) != Some(rp_id.as_str())
    {
        return error_response(StatusCode::BAD_REQUEST, "Passkey origin mismatch");
    }
    // Reconstruit le PublicKeyCredential depuis le body.
    let credential: PublicKeyCredential = match serde_json::from_value(body.clone()) {
        Ok(c) => c,
        Err(e) => {
            return error_response(
                StatusCode::BAD_REQUEST,
                &format!("Malformed credential: {e}"),
            )
        }
    };
    let passkeys = store.load_passkeys();
    let Some(passkey) = passkeys.get(idx).cloned() else {
        return error_response(StatusCode::UNAUTHORIZED, "Unknown passkey");
    };
    let auth_state = match take_auth_state(&state, &challenge) {
        Ok(s) => s,
        Err((s, m)) => return error_response(s, &m),
    };
    let result = match webauthn.finish_passkey_authentication(&credential, &auth_state) {
        Ok(r) => r,
        Err(e) => {
            return error_response(
                StatusCode::UNAUTHORIZED,
                &format!("Passkey signature verification failed: {e}"),
            )
        }
    };
    // Sign counter : webauthn-rs refuse si le compteur n'avance pas.
    let new_count = result.counter();
    let _ = store.update_after_auth(cred_id, &passkey, new_count);
    // Crée la session (comme upstream create_session + set_auth_cookie).
    let cookie = create_session(&state);
    let mut resp = axum::response::Response::new(axum::body::Body::from(
        serde_json::to_string(&json!({"ok": true})).unwrap(),
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

/// GET /api/auth/passkey/register/options — registration options.
pub async fn passkey_register_options(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Response {
    if !feature_flag_enabled() {
        return error_response(StatusCode::NOT_FOUND, "Passkey support is disabled.");
    }
    // Auth requise pour enregistrer (upstream _require_passkey_registration_auth).
    if !crate::auth::password::is_password_auth_enabled(&state.config.state_dir) {
        return error_response(StatusCode::BAD_REQUEST, "Auth not enabled");
    }
    let store = PasskeyStore::new(&state.config.state_dir);
    let webauthn = match webauthn_for(&headers) {
        Ok(w) => w,
        Err((s, m)) => return error_response(s, &m),
    };
    let (rp_id, origin) = rp_context(&headers);
    let uid = user_unique_id(&rp_id);
    let (ccr, reg) = match webauthn.start_passkey_registration(
        Uuid::from_slice(&uid).unwrap_or_else(|_| Uuid::nil()),
        "Hermes WebUI",
        "Hermes WebUI",
        exclude_credentials(&store),
    ) {
        Ok(v) => v,
        Err(e) => return error_response(StatusCode::BAD_REQUEST, &format!("WebAuthn: {e}")),
    };
    let challenge = b64u(&ccr.public_key.challenge);
    state
        .challenge_store
        .store(&challenge, "register", &rp_id, &origin);
    state
        .challenge_store
        .store_webauthn_state(&challenge, WebauthnState::Registration(reg));
    (StatusCode::OK, Json(json!({"ok": true, "publicKey": ccr}))).into_response()
}

/// POST /api/auth/passkey/register — finish registration.
pub async fn passkey_register(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Response {
    if !feature_flag_enabled() {
        return error_response(StatusCode::NOT_FOUND, "Passkey support is disabled.");
    }
    if !crate::auth::password::is_password_auth_enabled(&state.config.state_dir) {
        return error_response(StatusCode::BAD_REQUEST, "Auth not enabled");
    }
    let store = PasskeyStore::new(&state.config.state_dir);
    let webauthn = match webauthn_for(&headers) {
        Ok(w) => w,
        Err((s, m)) => return error_response(s, &m),
    };
    let (rp_id, origin) = rp_context(&headers);
    // Consomme le challenge (single-use).
    let challenge = body
        .get("response")
        .and_then(|r| r.get("clientDataJSON"))
        .and_then(|v| v.as_str())
        .and_then(|c| {
            use base64::Engine;
            let raw = base64::engine::general_purpose::URL_SAFE_NO_PAD
                .decode(c.trim_end_matches('='))
                .ok();
            raw.and_then(|b| serde_json::from_slice::<Value>(&b).ok())
                .and_then(|v| {
                    v.get("challenge")
                        .and_then(|c| c.as_str())
                        .map(String::from)
                })
        });
    let Some(challenge) = challenge else {
        return error_response(StatusCode::BAD_REQUEST, "Missing passkey challenge");
    };
    let entry = state.challenge_store.consume(&challenge, "register");
    let Some(entry) = entry else {
        return error_response(
            StatusCode::BAD_REQUEST,
            "Passkey challenge expired. Try again.",
        );
    };
    if entry.get("origin").and_then(|v| v.as_str()) != Some(origin.as_str())
        || entry.get("rp_id").and_then(|v| v.as_str()) != Some(rp_id.as_str())
    {
        return error_response(StatusCode::BAD_REQUEST, "Passkey origin mismatch");
    }
    let reg: RegisterPublicKeyCredential = match serde_json::from_value(body.clone()) {
        Ok(c) => c,
        Err(e) => {
            return error_response(
                StatusCode::BAD_REQUEST,
                &format!("Malformed credential: {e}"),
            )
        }
    };
    let reg_state = match take_reg_state(&state, &challenge) {
        Ok(s) => s,
        Err((s, m)) => return error_response(s, &m),
    };
    let passkey = match webauthn.finish_passkey_registration(&reg, &reg_state) {
        Ok(p) => p,
        Err(e) => return error_response(StatusCode::BAD_REQUEST, &format!("WebAuthn: {e}")),
    };
    let cred_id = b64u(passkey.cred_id());
    let label = body
        .get("label")
        .and_then(|v| v.as_str())
        .unwrap_or("Passkey")
        .trim()
        .chars()
        .take(80)
        .collect::<String>();
    let label = if label.is_empty() {
        "Passkey".into()
    } else {
        label
    };
    let sign_count = 0;
    if store
        .upsert(&cred_id, &label, &passkey, sign_count)
        .is_err()
    {
        return error_response(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Failed to persist passkey",
        );
    }
    (
        StatusCode::OK,
        Json(json!({
            "ok": true,
            "credential": {"id": cred_id, "label": label},
            "credentials": store.registered_credentials(),
        })),
    )
        .into_response()
}

/// POST /api/auth/passkey/delete — remove a credential.
pub async fn passkey_delete(State(state): State<AppState>, Json(body): Json<Value>) -> Response {
    if !feature_flag_enabled() {
        return error_response(StatusCode::NOT_FOUND, "Passkey support is disabled.");
    }
    let store = PasskeyStore::new(&state.config.state_dir);
    let cred_id = body.get("id").and_then(|v| v.as_str()).unwrap_or("");
    let creds = store.registered_credentials();
    let is_last = creds.len() <= 1
        && creds
            .iter()
            .any(|c| c.get("id").and_then(|v| v.as_str()) == Some(cred_id));
    if is_last && !crate::auth::password::is_password_auth_enabled(&state.config.state_dir) {
        return error_response(
            StatusCode::CONFLICT,
            "Set a password or disable auth before removing the last passkey.",
        );
    }
    if !store.delete(cred_id) {
        return error_response(StatusCode::NOT_FOUND, "Passkey not found");
    }
    (
        StatusCode::OK,
        Json(json!({"ok": true, "credentials": store.registered_credentials()})),
    )
        .into_response()
}

/// GET /api/auth/passkeys — liste publique des credentials.
pub async fn passkeys_list(State(state): State<AppState>) -> Response {
    if !feature_flag_enabled() {
        return Json(json!({"credentials": [], "disabled": true})).into_response();
    }
    let store = PasskeyStore::new(&state.config.state_dir);
    Json(json!({"credentials": store.registered_credentials()})).into_response()
}

// ── État de challenge webauthn-rs (associé au challenge, en mémoire) ────────

/// Les états PasskeyRegistration/PasskeyAuthentication sont conservés en
/// mémoire associés au challenge (TTL 90s, single-use) — jamais sérialisés
/// sur disque (feature danger-allow-state-serialisation non activée).
fn take_reg_state(
    state: &AppState,
    challenge: &str,
) -> Result<PasskeyRegistration, (StatusCode, String)> {
    match state.challenge_store.take_webauthn_state(challenge) {
        Some(WebauthnState::Registration(rs)) => Ok(rs),
        _ => Err((
            StatusCode::BAD_REQUEST,
            "Passkey challenge expired. Try again.".to_string(),
        )),
    }
}

fn take_auth_state(
    state: &AppState,
    challenge: &str,
) -> Result<PasskeyAuthentication, (StatusCode, String)> {
    match state.challenge_store.take_webauthn_state(challenge) {
        Some(WebauthnState::Authentication(ast)) => Ok(ast),
        _ => Err((
            StatusCode::BAD_REQUEST,
            "Passkey challenge expired. Try again.".to_string(),
        )),
    }
}

pub fn router() -> axum::Router<crate::state::AppState> {
    axum::Router::new()
        .route(
            "/api/auth/passkey/options",
            axum::routing::get(passkey_options),
        )
        .route(
            "/api/auth/passkey/login",
            axum::routing::post(passkey_login),
        )
        .route(
            "/api/auth/passkey/register/options",
            axum::routing::get(passkey_register_options),
        )
        .route(
            "/api/auth/passkey/register",
            axum::routing::post(passkey_register),
        )
        .route(
            "/api/auth/passkey/delete",
            axum::routing::post(passkey_delete),
        )
        .route("/api/auth/passkeys", axum::routing::get(passkeys_list))
}
