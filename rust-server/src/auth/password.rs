//! Auth password — portage PBKDF2 compatible upstream (Track B, R3).
//!
//! Porte le contrat password d'`api/auth.py` (HEAD upstream bd91b649) :
//!
//! - `_hash_password` (auth.py:367) : PBKDF2-HMAC-SHA256, 600 000 itérations
//!   (OWASP), salt = clé persistée `.pbkdf2_key` (32 octets, mode 0600) dans
//!   STATE_DIR, sortie = hex (64 chars) stockée dans `settings.json` sous
//!   `password_hash`. Format stocké identique : aucune enveloppe, hex brut.
//! - `_load_key` (auth.py:305) : lit les 32 premiers octets du fichier clé,
//!   sinon génère `secrets.token_bytes(32)` et persiste (mkdir + chmod 0600).
//! - `verify_password` (auth.py:573) : comparaison constant-time
//!   (`hmac.compare_digest`), avec migration transparente des hash calculés
//!   avec l'ancien sel `.signing_key` (re-hash + persistance via
//!   `_set_password`).
//! - `get_password_hash` (auth.py:398) : priorité env `HERMES_WEBUI_PASSWORD`
//!   (hashé au vol), sinon `settings.json["password_hash"]`.
//! - `save_settings` (api/config.py:9770) : `_set_password` → hash du
//!   `.strip()` ; `_clear_password` → `password_hash = None`.
//!
//! Ce module est volontairement autonome (aucune dépendance `crate::`) : il
//! prend des chemins en paramètres, ce qui permet de le tester en isolation
//! via `#[path]` sans toucher aux fichiers partagés (auth/mod.rs, state.rs,
//! config.rs, Cargo.toml restent inchangés — le wiring est fourni à
//! l'intégrateur dans le résumé de livraison).
//!
//! Aucun credential réel : tout est testé sur state temporaire /tmp.

use std::fs;
use std::io::Write;
use std::path::Path;

use hmac::{Hmac, Mac};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// Itérations PBKDF2 — VÉRIFIÉ contre le HEAD upstream actuel
/// (bd91b649, api/auth.py:380 : `hashlib.pbkdf2_hmac('sha256', ..., 600_000)`).
pub const PBKDF2_ITERATIONS: u32 = 600_000;

/// Nom du fichier clé salt (upstream auth.py:356 : `_load_key('.pbkdf2_key')`).
pub const PBKDF2_KEY_FILE: &str = ".pbkdf2_key";

/// Nom du fichier clé legacy (migration, upstream auth.py:363).
pub const SIGNING_KEY_FILE: &str = ".signing_key";

/// Taille de clé générée (upstream `secrets.token_bytes(32)`).
const KEY_LEN: usize = 32;

/// Longueur du hash hex (32 octets → 64 chars).
const HASH_HEX_LEN: usize = 64;

/// PBKDF2-HMAC-SHA256 conforme RFC 2898, dkLen = 32 (un seul bloc T_1).
/// Implémentation manuelle : `pbkdf2` n'est pas dans Cargo.toml (verrouillé),
/// `hmac` + `sha2` y sont déjà. Vérifiée contre hashlib.pbkdf2_hmac (vecteurs
/// de test Python dans tests/test_auth_password.rs).
fn pbkdf2_sha256(password: &[u8], salt: &[u8], iterations: u32) -> [u8; 32] {
    debug_assert!(iterations >= 1);
    // U_1 = PRF(password, salt || INT_32_BE(1))
    let mut u = {
        let mut mac = HmacSha256::new_from_slice(password).expect("hmac key");
        mac.update(salt);
        mac.update(&1u32.to_be_bytes());
        mac.finalize().into_bytes()
    };
    let mut out = [0u8; 32];
    out.copy_from_slice(&u);
    for _ in 1..iterations {
        // U_i = PRF(password, U_{i-1}) ; T_1 = U_1 XOR U_2 XOR ... XOR U_c
        let mut mac = HmacSha256::new_from_slice(password).expect("hmac key");
        mac.update(&u);
        u = mac.finalize().into_bytes();
        for (o, x) in out.iter_mut().zip(u.iter()) {
            *o ^= x;
        }
    }
    out
}

fn to_hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Charge une clé de 32 octets depuis STATE_DIR, en générant et persistant
/// une nouvelle si absente ou trop courte (upstream `_load_key`, auth.py:305).
/// Retourne `(clé, créée)` — `créée` = true quand la clé a été générée.
/// Échec d'écriture : on continue avec la clé générée (upstream log warning
/// et continue) — mais on ne propage PAS d'erreur fatale.
pub fn load_key(state_dir: &Path, filename: &str) -> (Vec<u8>, bool) {
    let key_file = state_dir.join(filename);
    if let Ok(raw) = fs::read(&key_file) {
        if raw.len() >= KEY_LEN {
            return (raw[..KEY_LEN].to_vec(), false);
        }
    }
    // Générer une clé aléatoire (upstream secrets.token_bytes(32)).
    let mut key = [0u8; KEY_LEN];
    rand::RngCore::fill_bytes(&mut rand::thread_rng(), &mut key);
    let created = persist_key(&key_file, &key);
    (key.to_vec(), created)
}

/// Persiste une clé (mkdir parents + write + chmod 0600). Retourne false si
/// l'écriture échoue (upstream : warning + continue).
fn persist_key(key_file: &Path, key: &[u8]) -> bool {
    if let Some(parent) = key_file.parent() {
        if fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    let write_ok = (|| -> std::io::Result<()> {
        let mut f = fs::File::create(key_file)?;
        f.write_all(key)?;
        f.sync_all()?;
        Ok(())
    })()
    .is_ok();
    if write_ok {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(key_file, fs::Permissions::from_mode(0o600));
        }
    }
    write_ok
}

/// Hash PBKDF2 hex d'un mot de passe avec le sel donné (upstream
/// `_hash_password(password, salt=...)`, auth.py:367). `iterations` est
/// paramétrable pour les tests ; la production utilise `PBKDF2_ITERATIONS`.
pub fn hash_password_with_salt(password: &str, salt: &[u8], iterations: u32) -> String {
    let dk = pbkdf2_sha256(password.as_bytes(), salt, iterations);
    to_hex(&dk)
}

/// Hash PBKDF2 hex avec le sel `.pbkdf2_key` courant (upstream
/// `_hash_password(password)` — sel par défaut).
pub fn hash_password(state_dir: &Path, password: &str) -> String {
    let (salt, _) = load_key(state_dir, PBKDF2_KEY_FILE);
    hash_password_with_salt(password, &salt, PBKDF2_ITERATIONS)
}

/// Hash hex attendu (upstream `get_password_hash`, auth.py:398) :
/// 1. env `HERMES_WEBUI_PASSWORD` (strip, prioritaire) — hashé au vol ;
/// 2. sinon `settings.json["password_hash"]` (string hex, ou None).
/// Le fichier settings est relu à chaque appel (pas de cache : le port Rust
/// n'a pas le problème de coût par requête de Python, et ça évite toute
/// invalidation de cache à gérer).
pub fn get_password_hash(state_dir: &Path) -> Option<String> {
    if let Ok(env_pw) = std::env::var("HERMES_WEBUI_PASSWORD") {
        let env_pw = env_pw.trim();
        if !env_pw.is_empty() {
            return Some(hash_password(state_dir, env_pw));
        }
    }
    let settings_path = state_dir.join("settings.json");
    let text = fs::read_to_string(settings_path).ok()?;
    let v: serde_json::Value = serde_json::from_str(&text).ok()?;
    v.get("password_hash")
        .and_then(|h| h.as_str())
        .filter(|h| !h.is_empty())
        .map(|s| s.to_string())
}

/// True si un password est configuré (upstream `is_password_auth_enabled`).
pub fn is_password_auth_enabled(state_dir: &Path) -> bool {
    get_password_hash(state_dir).is_some()
}

/// Vérifie un mot de passe contre le hash stocké, en temps constant
/// (upstream `verify_password`, auth.py:573). Gère la migration transparente
/// des hash calculés avec l'ancien sel `.signing_key` : si le hash legacy
/// correspond, le mot de passe est re-hashé avec `.pbkdf2_key` et persisté
/// dans settings.json (`_set_password`), comme upstream.
///
/// Retourne `(ok, migrated)` : `migrated` = true quand un re-hash a été
/// persisté (utile pour les tests ; la production peut l'ignorer).
pub fn verify_password(state_dir: &Path, plain: &str) -> (bool, bool) {
    let Some(expected) = get_password_hash(state_dir) else {
        return (false, false);
    };
    // Fast path : sel `.pbkdf2_key` courant.
    let (current_salt, _) = load_key(state_dir, PBKDF2_KEY_FILE);
    let candidate = hash_password_with_salt(plain, &current_salt, PBKDF2_ITERATIONS);
    if constant_time_eq(candidate.as_bytes(), expected.as_bytes()) {
        return (true, false);
    }
    // Migration : hash calculés avec `.signing_key` avant la séparation des
    // clés (upstream auth.py:587-601).
    let (legacy_salt, _) = load_key(state_dir, SIGNING_KEY_FILE);
    if legacy_salt != current_salt {
        let legacy_candidate = hash_password_with_salt(plain, &legacy_salt, PBKDF2_ITERATIONS);
        if constant_time_eq(legacy_candidate.as_bytes(), expected.as_bytes()) {
            // Re-hash avec le sel courant et persistance (upstream
            // save_settings({'_set_password': plain})).
            let new_hash = hash_password_with_salt(plain, &current_salt, PBKDF2_ITERATIONS);
            if set_password(state_dir, &new_hash) {
                return (true, true);
            }
            return (true, false);
        }
    }
    (false, false)
}

/// Comparaison constant-time (upstream `hmac.compare_digest`). Les deux
/// chaînes sont comparées sur la longueur max, sans early-exit.
pub fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        // Longueurs différentes : comparer quand même pour ne pas fuiter la
        // longueur par le timing (compare_digest fait pareil : il compare
        // jusqu'à la longueur max puis retourne false).
        let max = a.len().max(b.len());
        let mut diff = 1u8;
        for i in 0..max {
            let x = a.get(i).copied().unwrap_or(0);
            let y = b.get(i).copied().unwrap_or(0);
            diff |= x ^ y;
        }
        return diff == 0;
    }
    let mut diff = 0u8;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

/// Persiste `password_hash` dans settings.json (upstream save_settings :
/// `_set_password` → hash du strip ; `_clear_password` → None). Écriture
/// indentée (json.dumps(indent=2) upstream). Retourne false si le fichier
/// ne peut pas être écrit.
pub fn set_password(state_dir: &Path, password_hash: &str) -> bool {
    let settings_path = state_dir.join("settings.json");
    let mut settings: serde_json::Map<String, serde_json::Value> =
        fs::read_to_string(&settings_path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .and_then(|v: serde_json::Value| v.as_object().cloned())
            .unwrap_or_default();
    settings.insert(
        "password_hash".into(),
        serde_json::Value::String(password_hash.to_string()),
    );
    write_settings(&settings_path, &settings)
}

/// Supprime le password (upstream `_clear_password` → `password_hash = None`).
pub fn clear_password(state_dir: &Path) -> bool {
    let settings_path = state_dir.join("settings.json");
    let mut settings: serde_json::Map<String, serde_json::Value> =
        fs::read_to_string(&settings_path)
            .ok()
            .and_then(|t| serde_json::from_str(&t).ok())
            .and_then(|v: serde_json::Value| v.as_object().cloned())
            .unwrap_or_default();
    settings.insert("password_hash".into(), serde_json::Value::Null);
    write_settings(&settings_path, &settings)
}

fn write_settings(
    settings_path: &Path,
    settings: &serde_json::Map<String, serde_json::Value>,
) -> bool {
    if let Some(parent) = settings_path.parent() {
        if fs::create_dir_all(parent).is_err() {
            return false;
        }
    }
    let text = match serde_json::to_string_pretty(settings) {
        Ok(t) => t,
        Err(_) => return false,
    };
    fs::write(settings_path, text).is_ok()
}

/// Hash hex d'un mot de passe pour bootstrap (upstream : POST /api/settings
/// avec `_set_password` quand auth est désactivée). Équivalent à
/// `hash_password` mais explicite sur le sel utilisé.
pub fn bootstrap_password(state_dir: &Path, password: &str) -> String {
    hash_password(state_dir, password)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pbkdf2_matches_python_vectors_1000() {
        // Vecteurs générés avec hashlib.pbkdf2_hmac('sha256', ...) — mêmes
        // paramètres qu'upstream (auth.py:380), itérations réduites pour la
        // vitesse des tests.
        let salt: Vec<u8> = (0u8..32).collect();
        assert_eq!(
            hash_password_with_salt("test-password", &salt, 1_000),
            "d1695957b876291775507821a201d12220ab9846171e3b4f948fe0df755c8ab8"
        );
        assert_eq!(
            hash_password_with_salt("pässwörd-🔐-日本語", &salt, 1_000),
            "4adf3058c168dad2ff864c6d75ac161d6dfa5e8fec814b1d700e4908e8d9190d"
        );
        assert_eq!(
            hash_password_with_salt("", &salt, 1_000),
            "9a3a49caec0ef343debcc5d73d18a5bd90a40192651dba2eab0b189487b2e06c"
        );
        assert_eq!(
            hash_password_with_salt(&"x".repeat(10_000), &salt, 1_000),
            "d2f125cf6c720c8e969aab5682b74e1d006927a84d9d9e180a59b0705f08b4d2"
        );
    }

    #[test]
    #[ignore = "~8 s en debug (600k itérations) — couvert par tests/test_auth_password.rs (release)"]
    fn pbkdf2_matches_python_vectors_600k() {
        // Vecteur 600k réel (compatibilité exacte avec upstream). ~8 s en
        // debug, ~0.5 s en release — exécuté en release via
        // `cargo test --release` (voir TESTING.md du port).
        let salt: Vec<u8> = (0u8..32).collect();
        assert_eq!(
            hash_password_with_salt("test-password", &salt, 600_000),
            "8aba72dadfcd3b59e16a4f57d805f18dcf81b29fa8c9046e8fa8a899ea4f1248"
        );
    }
}
