//! Login rate limiter (R4) — port fidèle de `api/auth.py` `_check_login_rate`,
//! `_record_login_attempt`, `_clear_login_attempts` (lignes 218-302).
//!
//! Contrat upstream (baseline 192df903) :
//! - Fichier persistant `STATE_DIR/.login_attempts.json` : `{ip: [timestamp, ...]}`
//! - Max 5 tentatives par IP sur une fenêtre glissante de 60 s.
//! - Lecture au boot avec purge des tentatives expirées ; écriture atomique
//!   (tmp + fsync + chmod 0600 + rename) à chaque mutation.
//! - Thread-safe (mutex global upstream ; Arc<Mutex> ici).
//! - `check` : purge, retourne `len < 5` ; `record` : append timestamp ;
//!   `clear` : retire l'IP (après login réussi).

use std::collections::HashMap;
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

pub const LOGIN_MAX_ATTEMPTS: usize = 5;
pub const LOGIN_WINDOW_SECS: u64 = 60;
const ATTEMPTS_FILE: &str = ".login_attempts.json";

/// Store du rate-limit (partagé via Arc dans AppState).
#[derive(Clone)]
pub struct LoginRateLimiter {
    state_dir: PathBuf,
    /// ip -> [timestamps (epoch secs), ...]
    attempts: Arc<Mutex<HashMap<String, Vec<u64>>>>,
}

impl LoginRateLimiter {
    pub fn new(state_dir: PathBuf) -> Self {
        let attempts = load_attempts(&state_dir.join(ATTEMPTS_FILE));
        LoginRateLimiter {
            state_dir,
            attempts: Arc::new(Mutex::new(attempts)),
        }
    }

    /// True si l'IP est autorisée à tenter un login (purge + `len < 5`).
    pub fn check_allowed(&self, ip: &str) -> bool {
        let mut guard = self.attempts.lock().unwrap_or_else(|p| p.into_inner());
        let now = now_unix();
        let attempts = prune(&mut guard, ip, now);
        persist(&self.state_dir.join(ATTEMPTS_FILE), &guard);
        attempts.len() < LOGIN_MAX_ATTEMPTS
    }

    /// Enregistre une tentative échouée (append timestamp).
    pub fn record_attempt(&self, ip: &str) {
        let mut guard = self.attempts.lock().unwrap_or_else(|p| p.into_inner());
        let now = now_unix();
        let entry = guard.entry(ip.to_string()).or_default();
        entry.push(now);
        // Garde uniquement les tentatives dans la fenêtre (bounded).
        entry.retain(|&t| now.saturating_sub(t) < LOGIN_WINDOW_SECS);
        persist(&self.state_dir.join(ATTEMPTS_FILE), &guard);
    }

    /// Efface les tentatives d'une IP (après login réussi).
    pub fn clear_attempts(&self, ip: &str) {
        let mut guard = self.attempts.lock().unwrap_or_else(|p| p.into_inner());
        guard.remove(ip);
        persist(&self.state_dir.join(ATTEMPTS_FILE), &guard);
    }
}

/// Purge les tentatives expirées de `ip`, retourne les tentatives fraîches.
fn prune(attempts: &mut HashMap<String, Vec<u64>>, ip: &str, now: u64) -> Vec<u64> {
    let fresh = match attempts.get(ip) {
        Some(list) => list
            .iter()
            .copied()
            .filter(|&t| now.saturating_sub(t) < LOGIN_WINDOW_SECS)
            .collect(),
        None => vec![],
    };
    if fresh.is_empty() {
        attempts.remove(ip);
    } else {
        attempts.insert(ip.to_string(), fresh.clone());
    }
    fresh
}

/// Charge le fichier de tentatives au boot (purge des expirés, fail-closed → {}).
fn load_attempts(path: &Path) -> HashMap<String, Vec<u64>> {
    let mut out = HashMap::new();
    let Ok(text) = fs::read_to_string(path) else {
        return out;
    };
    let Ok(data) = serde_json::from_str::<serde_json::Value>(&text) else {
        return out;
    };
    let Some(obj) = data.as_object() else {
        return out;
    };
    let now = now_unix();
    for (ip, raw) in obj {
        if let Some(list) = raw.as_array() {
            let fresh: Vec<u64> = list
                .iter()
                .filter_map(|v| v.as_u64().or_else(|| v.as_f64().map(|f| f as u64)))
                .filter(|&t| now.saturating_sub(t) < LOGIN_WINDOW_SECS)
                .collect();
            if !fresh.is_empty() {
                out.insert(ip.clone(), fresh);
            }
        }
    }
    out
}

/// Écriture atomique (tmp + fsync + chmod 0600 + rename), fail-soft (log debug).
fn persist(path: &Path, attempts: &HashMap<String, Vec<u64>>) {
    let Ok(payload) = serde_json::to_string(attempts) else {
        return;
    };
    if let Some(parent) = path.parent() {
        if fs::create_dir_all(parent).is_err() {
            return;
        }
    }
    let tmp = path.with_extension("login_attempts.tmp");
    let write_ok = (|| -> std::io::Result<()> {
        let mut f = fs::File::create(&tmp)?;
        f.write_all(payload.as_bytes())?;
        f.sync_all()?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&tmp, fs::Permissions::from_mode(0o600));
        }
        fs::rename(&tmp, path)?;
        Ok(())
    })();
    if write_ok.is_err() {
        let _ = fs::remove_file(&tmp);
    }
}

fn now_unix() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_state() -> PathBuf {
        let d = std::env::temp_dir().join(format!(
            "hermes-rate-limit-test-{}-{}",
            std::process::id(),
            std::thread::current()
                .name()
                .unwrap_or("unnamed")
                .replace("::", "-")
        ));
        let _ = fs::remove_dir_all(&d);
        fs::create_dir_all(&d).unwrap();
        d
    }

    #[test]
    fn allows_up_to_max_attempts() {
        let d = temp_state();
        let lim = LoginRateLimiter::new(d.clone());
        assert!(lim.check_allowed("10.0.0.1"));
        for _ in 0..5 {
            lim.record_attempt("10.0.0.1");
        }
        assert!(!lim.check_allowed("10.0.0.1"), "6e tentative bloquée");
        // Autre IP non affectée
        assert!(lim.check_allowed("10.0.0.2"));
        // Clear réinitialise
        lim.clear_attempts("10.0.0.1");
        assert!(lim.check_allowed("10.0.0.1"));
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn persists_across_reload() {
        let d = temp_state();
        {
            let lim = LoginRateLimiter::new(d.clone());
            for _ in 0..5 {
                lim.record_attempt("10.0.0.9");
            }
        }
        // Recharge depuis disque — toujours bloqué.
        let lim2 = LoginRateLimiter::new(d.clone());
        assert!(!lim2.check_allowed("10.0.0.9"));
        assert!(d.join(ATTEMPTS_FILE).exists());
        let _ = fs::remove_dir_all(&d);
    }

    #[test]
    fn ignores_corrupt_file() {
        let d = temp_state();
        fs::write(d.join(ATTEMPTS_FILE), b"{not json").unwrap();
        let lim = LoginRateLimiter::new(d.clone());
        assert!(lim.check_allowed("10.0.0.3"));
        let _ = fs::remove_dir_all(&d);
    }
}
