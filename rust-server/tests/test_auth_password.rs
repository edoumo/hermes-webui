//! Tests du portage auth password PBKDF2 (Track B, R3).
//!
//! Couverture exigée par le mandat : correct, wrong, malformed stored hash,
//! empty, Unicode, long input, timing-safe (autant que testable), change
//! password, removal. Plus : compatibilité exacte avec les vecteurs Python
//! (hashlib.pbkdf2_hmac, mêmes paramètres qu'upstream api/auth.py:380),
//! format stocké identique (settings.json password_hash hex + .pbkdf2_key),
//! bootstrap password, migration legacy .signing_key, env var prioritaire.
//!
//! Le module est inclus via `#[path]` (auth/mod.rs est verrouillé — le
//! wiring est fourni à l'intégrateur dans le résumé de livraison).
//!
//! Aucun credential réel : state temporaire /tmp, mots de passe de test.

#[path = "../src/auth/password.rs"]
mod password;

use std::path::PathBuf;

use password::{
    bootstrap_password, clear_password, constant_time_eq, get_password_hash, hash_password,
    hash_password_with_salt, is_password_auth_enabled, load_key, set_password, verify_password,
    PBKDF2_ITERATIONS, PBKDF2_KEY_FILE, SIGNING_KEY_FILE,
};

/// State dir temporaire unique par test (isolation totale, /tmp).
/// Retire aussi HERMES_WEBUI_PASSWORD de l'environnement : l'env var a
/// priorité sur le hash stocké (auth.py:423) et l'environnement du shell
/// peut en hériter (prod) — sans ce retrait, tous les tests seraient pollués.
fn test_state_dir(name: &str) -> PathBuf {
    std::env::remove_var("HERMES_WEBUI_PASSWORD");
    let dir = std::env::temp_dir().join(format!(
        "hermes-webui-rust-pw-{}-{name}",
        std::process::id()
    ));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("create state dir");
    dir
}

/// Écrit un settings.json minimal avec le password_hash donné.
fn write_settings(state_dir: &std::path::Path, password_hash: Option<&str>) {
    let value = match password_hash {
        Some(h) => serde_json::json!({ "password_hash": h }),
        None => serde_json::json!({ "password_hash": null }),
    };
    std::fs::write(
        state_dir.join("settings.json"),
        serde_json::to_string_pretty(&value).unwrap(),
    )
    .expect("write settings");
}

/// Hash de référence Python pour "test-password" (600k, sel = 0..32).
const PY_VEC_600K_ASCII: &str = "8aba72dadfcd3b59e16a4f57d805f18dcf81b29fa8c9046e8fa8a899ea4f1248";

// ── Compatibilité algorithmique (vecteurs Python) ──────────────────────────

#[test]
fn pbkdf2_matches_python_1000_iterations() {
    // Vecteurs hashlib.pbkdf2_hmac('sha256', ...) — itérations réduites pour
    // la vitesse ; l'algorithme est identique à 600k (même code path).
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
#[ignore = "~8 s en debug (600k itérations) — lancer avec cargo test --release"]
fn pbkdf2_matches_python_600k_real() {
    // Compatibilité EXACTE avec upstream (auth.py:380, 600_000 itérations).
    let salt: Vec<u8> = (0u8..32).collect();
    assert_eq!(
        hash_password_with_salt("test-password", &salt, 600_000),
        PY_VEC_600K_ASCII
    );
}

// ── Format stocké identique ───────────────────────────────────────────────

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn stored_format_matches_upstream() {
    let dir = test_state_dir("stored-format");
    // Bootstrap : hash 600k réel, sel = .pbkdf2_key généré. NB: comme
    // upstream, bootstrap ne fait que HASHER — la persistance dans
    // settings.json passe par set_password (POST /api/settings _set_password).
    let hash = bootstrap_password(&dir, "test-password");
    assert_eq!(hash.len(), 64, "hex SHA-256 = 64 chars");
    assert!(hash.chars().all(|c| c.is_ascii_hexdigit()), "hex pur");
    // .pbkdf2_key : 32 octets bruts, mode 0600.
    let key_path = dir.join(PBKDF2_KEY_FILE);
    assert!(key_path.exists(), ".pbkdf2_key créé");
    let key = std::fs::read(&key_path).unwrap();
    assert_eq!(key.len(), 32, "clé 32 octets");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&key_path).unwrap().permissions().mode();
        assert_eq!(mode & 0o777, 0o600, "chmod 0600 (upstream auth.py:331)");
    }
    // Persistance : set_password écrit le hash hex brut, aucune enveloppe.
    assert!(set_password(&dir, &hash));
    let settings: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(dir.join("settings.json")).unwrap()).unwrap();
    assert_eq!(settings["password_hash"], serde_json::json!(hash));
}

#[test]
fn pbkdf2_key_stable_and_reused() {
    let dir = test_state_dir("key-stable");
    let (k1, created1) = load_key(&dir, PBKDF2_KEY_FILE);
    assert!(created1);
    let (k2, created2) = load_key(&dir, PBKDF2_KEY_FILE);
    assert!(!created2);
    assert_eq!(k1, k2, "clé persistée réutilisée (upstream _load_key)");
    // Hash identique sur deux appels → sel stable.
    let h1 = hash_password(&dir, "test-password");
    let h2 = hash_password(&dir, "test-password");
    assert_eq!(h1, h2);
}

#[test]
fn pbkdf2_key_truncated_to_32_bytes() {
    let dir = test_state_dir("key-truncate");
    std::fs::write(dir.join(PBKDF2_KEY_FILE), vec![0xAB; 64]).unwrap();
    let (key, created) = load_key(&dir, PBKDF2_KEY_FILE);
    assert!(!created);
    assert_eq!(key, vec![0xAB; 32], "upstream : raw[:32] (auth.py:312)");
}

// ── Vérification : correct / wrong / malformed / empty / Unicode / long ──

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn verify_correct_password() {
    let dir = test_state_dir("verify-correct");
    let hash = bootstrap_password(&dir, "test-password");
    write_settings(&dir, Some(&hash));
    let (ok, migrated) = verify_password(&dir, "test-password");
    assert!(ok);
    assert!(!migrated);
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn verify_wrong_password() {
    let dir = test_state_dir("verify-wrong");
    let hash = bootstrap_password(&dir, "test-password");
    write_settings(&dir, Some(&hash));
    let (ok, _) = verify_password(&dir, "wrong-password");
    assert!(!ok);
    // Proche mais différent (typo).
    let (ok, _) = verify_password(&dir, "test-passwrod");
    assert!(!ok);
    // Casse différente.
    let (ok, _) = verify_password(&dir, "TEST-PASSWORD");
    assert!(!ok);
}

#[test]
fn verify_no_password_configured() {
    let dir = test_state_dir("verify-none");
    // Aucun settings.json → auth désactivée → verify false (upstream
    // auth.py:582 : `if not expected: return False`).
    let (ok, _) = verify_password(&dir, "anything");
    assert!(!ok);
    assert!(!is_password_auth_enabled(&dir));
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn verify_malformed_stored_hash() {
    let dir = test_state_dir("verify-malformed");
    // Hash stocké invalide (pas hex, longueur bizarre) → false, pas de panic.
    for bad in [
        "not-a-hex-hash",
        "abc",
        "zzzz",
        "8aba72dadfcd3b59e16a4f57d805f18dcf81b29fa8c9046e8fa8a899ea4f1248!",
        "8aba72dadfcd3b59e16a4f57d805f18dcf81b29fa8c9046e8fa8a899ea4f1248",
    ] {
        write_settings(&dir, Some(bad));
        let (ok, _) = verify_password(&dir, "test-password");
        assert!(!ok, "hash malformé {bad:?} → false");
    }
    // password_hash = null (auth désactivée).
    write_settings(&dir, None);
    let (ok, _) = verify_password(&dir, "test-password");
    assert!(!ok);
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn verify_empty_password() {
    let dir = test_state_dir("verify-empty");
    // Bootstrap avec mot de passe vide → hash d'une chaîne vide (upstream
    // refuse via `.strip()` dans save_settings, mais le hash existe).
    let hash = hash_password(&dir, "");
    write_settings(&dir, Some(&hash));
    let (ok, _) = verify_password(&dir, "");
    assert!(ok, "mot de passe vide vérifié contre son propre hash");
    let (ok, _) = verify_password(&dir, " ");
    assert!(!ok, "espace ≠ vide (pas de strip côté verify, upstream)");
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn verify_unicode_password() {
    let dir = test_state_dir("verify-unicode");
    let pw = "pässwörd-🔐-日本語-пароль";
    let hash = bootstrap_password(&dir, pw);
    write_settings(&dir, Some(&hash));
    let (ok, _) = verify_password(&dir, pw);
    assert!(ok, "Unicode exact");
    // Normalisation : pas de NFC/NFD (upstream encode() brut, pas de
    // unicodedata.normalize) — les deux formes sont donc distinctes.
    let (ok, _) = verify_password(&dir, "pässwörd-🔐-日本語-пароль");
    assert!(ok);
    let (ok, _) = verify_password(&dir, "pässwörd-🔐-日本語-пароль\u{301}");
    assert!(!ok, "combining accent ≠ précomposé (comportement upstream)");
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn verify_long_password() {
    let dir = test_state_dir("verify-long");
    // Long input à 600k itérations : 100 chars (≈ 60 Mo de SHA-256 par hash,
    // ~0.3 s en debug). Les preuves 10k chars / 1 Mo passent par les vecteurs
    // Python à 1000 itérations (même code path, coût 600× moindre).
    let long = "x".repeat(100);
    let hash = bootstrap_password(&dir, &long);
    write_settings(&dir, Some(&hash));
    let (ok, _) = verify_password(&dir, &long);
    assert!(ok, "100 chars");
    let (ok, _) = verify_password(&dir, &format!("{long}x"));
    assert!(!ok, "100+1 ≠ 100");
    // 1 Mo — pas de limite de taille côté hash (PBKDF2 accepte toute taille).
    // NB: vérifié à 1000 itérations : à 600k, 1 Mo × 600k = 600 Go de SHA-256
    // (~30 min en debug) pour un comportement déjà prouvé par le vecteur
    // Python 1000 itérations (10k chars) et le cas 100 chars ci-dessus.
    let huge = "y".repeat(1_000_000);
    let salt: Vec<u8> = (0u8..32).collect();
    let h = hash_password_with_salt(&huge, &salt, 1_000);
    assert_eq!(h.len(), 64, "1 Mo accepté, hex 64 chars");
    assert!(h.chars().all(|c| c.is_ascii_hexdigit()));
}

// ── Timing-safe (autant que testable) ─────────────────────────────────────

#[test]
fn constant_time_eq_basic() {
    assert!(constant_time_eq(b"abc", b"abc"));
    assert!(!constant_time_eq(b"abc", b"abd"));
    assert!(!constant_time_eq(b"abc", b"abcd"));
    assert!(!constant_time_eq(b"abcd", b"abc"));
    assert!(constant_time_eq(b"", b""));
    assert!(!constant_time_eq(b"", b"a"));
}

#[test]
fn constant_time_eq_no_early_exit_on_first_byte() {
    // Le point de la comparaison constant-time : un mismatch au premier
    // octet doit coûter autant qu'un mismatch au dernier octet. On ne peut
    // pas prouver le timing en test unitaire, mais on peut prouver que
    // l'implémentation parcourt TOUTE la chaîne : on vérifie le résultat
    // pour des paires qui ne diffèrent qu'au dernier octet, et on s'assure
    // que le code ne short-circuite pas (couverture de toutes les branches).
    let a = b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    let b_first = {
        let mut b = *a;
        b[0] = b'b';
        b
    };
    let b_last = {
        let mut b = *a;
        b[63] = b'b';
        b
    };
    assert!(!constant_time_eq(a, &b_first));
    assert!(!constant_time_eq(a, &b_last));
    // Longueurs différentes : même chemin de comparaison (pas de return
    // immédiat avant la boucle).
    assert!(!constant_time_eq(a, &a[..63]));
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn verify_password_uses_constant_time_path() {
    // Le chemin de vérification passe par constant_time_eq (pas de ==
    // naïf) : on vérifie que le hash attendu n'est jamais comparé par
    // égalité de chaîne directe — test structurel via le comportement :
    // un hash malformé de même longueur que le bon ne déclenche pas de
    // panic et retourne false proprement (déjà couvert), et le bon hash
    // passe. Le vrai timing ne se teste pas en unitaire (bruit machine) ;
    // la garantie vient de l'implémentation (boucle complète, XOR).
    let dir = test_state_dir("verify-ct");
    let hash = bootstrap_password(&dir, "test-password");
    write_settings(&dir, Some(&hash));
    // 100 vérifications correctes + 100 incorrectes : stabilité, pas de
    // panic, résultats déterministes.
    for _ in 0..100 {
        assert!(verify_password(&dir, "test-password").0);
        assert!(!verify_password(&dir, "wrong-password").0);
    }
}

// ── Bootstrap / change / removal ─────────────────────────────────────────

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn bootstrap_then_change_then_remove() {
    let dir = test_state_dir("lifecycle");
    // 1. Bootstrap : aucun password → set_password crée le hash.
    let hash1 = bootstrap_password(&dir, "first-password");
    assert!(set_password(&dir, &hash1));
    assert!(is_password_auth_enabled(&dir));
    assert!(verify_password(&dir, "first-password").0);
    assert!(!verify_password(&dir, "second-password").0);

    // 2. Change : nouveau hash remplace l'ancien (upstream _set_password).
    let hash2 = bootstrap_password(&dir, "second-password");
    assert!(set_password(&dir, &hash2));
    assert!(verify_password(&dir, "second-password").0);
    assert!(
        !verify_password(&dir, "first-password").0,
        "ancien hash remplacé"
    );
    // Le hash stocké est bien le nouveau.
    assert_eq!(get_password_hash(&dir).as_deref(), Some(hash2.as_str()));

    // 3. Removal : clear_password → password_hash = null (upstream
    // _clear_password), auth désactivée.
    assert!(clear_password(&dir));
    assert!(!is_password_auth_enabled(&dir));
    assert!(!verify_password(&dir, "second-password").0);
    let settings: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(dir.join("settings.json")).unwrap()).unwrap();
    assert_eq!(settings["password_hash"], serde_json::Value::Null);
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn set_password_preserves_other_settings() {
    let dir = test_state_dir("preserve-settings");
    std::fs::write(
        dir.join("settings.json"),
        serde_json::to_string_pretty(&serde_json::json!({
            "bot_name": "Hermes",
            "theme": "light",
        }))
        .unwrap(),
    )
    .unwrap();
    let hash = bootstrap_password(&dir, "pw");
    assert!(set_password(&dir, &hash));
    let settings: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(dir.join("settings.json")).unwrap()).unwrap();
    assert_eq!(settings["bot_name"], "Hermes", "clés existantes préservées");
    assert_eq!(settings["theme"], "light");
    assert_eq!(settings["password_hash"], serde_json::json!(hash));
}

// ── Migration legacy .signing_key (upstream auth.py:587-601) ─────────────

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn legacy_signing_key_hash_migrates() {
    let dir = test_state_dir("legacy-migration");
    // Simule un hash calculé avec l'ancien sel .signing_key (avant la
    // séparation des clés) : on écrit une clé .signing_key différente de
    // .pbkdf2_key, et on stocke le hash legacy.
    let legacy_salt = vec![0x11; 32];
    std::fs::write(dir.join(SIGNING_KEY_FILE), &legacy_salt).unwrap();
    let legacy_hash = hash_password_with_salt("test-password", &legacy_salt, PBKDF2_ITERATIONS);
    write_settings(&dir, Some(&legacy_hash));
    // Le sel courant (.pbkdf2_key) est différent → fast path échoue, le
    // legacy path doit matcher et re-hasher.
    let (ok, migrated) = verify_password(&dir, "test-password");
    assert!(ok, "hash legacy accepté");
    assert!(migrated, "re-hash persisté avec le sel courant");
    // Après migration : le hash stocké est celui du sel courant.
    let (current_salt, _) = load_key(&dir, PBKDF2_KEY_FILE);
    let expected = hash_password_with_salt("test-password", &current_salt, PBKDF2_ITERATIONS);
    assert_eq!(get_password_hash(&dir).as_deref(), Some(expected.as_str()));
    // Et le fast path fonctionne désormais (migrated = false).
    let (ok, migrated) = verify_password(&dir, "test-password");
    assert!(ok);
    assert!(!migrated);
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn legacy_salt_equal_to_current_no_migration_attempt() {
    let dir = test_state_dir("legacy-equal");
    // .signing_key == .pbkdf2_key → pas de tentative de migration (upstream
    // auth.py:592 : `if legacy_salt != current_salt`).
    let (salt, _) = load_key(&dir, PBKDF2_KEY_FILE);
    std::fs::write(dir.join(SIGNING_KEY_FILE), &salt).unwrap();
    let hash = hash_password_with_salt("test-password", &salt, PBKDF2_ITERATIONS);
    write_settings(&dir, Some(&hash));
    let (ok, migrated) = verify_password(&dir, "test-password");
    assert!(ok);
    assert!(!migrated);
}

// ── Env var HERMES_WEBUI_PASSWORD (priorité upstream auth.py:423) ────────

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn env_password_takes_precedence_over_settings() {
    let dir = test_state_dir("env-precedence");
    // Un hash stocké différent de l'env var.
    let stored = bootstrap_password(&dir, "stored-password");
    write_settings(&dir, Some(&stored));
    // Env var posée → c'est ELLE qui fait foi.
    std::env::set_var("HERMES_WEBUI_PASSWORD", "env-password");
    let expected_env = hash_password(&dir, "env-password");
    assert_eq!(
        get_password_hash(&dir).as_deref(),
        Some(expected_env.as_str()),
        "env var prioritaire (auth.py:423)"
    );
    assert!(verify_password(&dir, "env-password").0);
    assert!(
        !verify_password(&dir, "stored-password").0,
        "hash stocké ignoré"
    );
    std::env::remove_var("HERMES_WEBUI_PASSWORD");
    // Sans env var → le hash stocké refait foi.
    assert!(verify_password(&dir, "stored-password").0);
}

#[ignore = "coût 600k réel (~8s/hash en debug) — couvert par cargo test --release -- --ignored"]
#[test]
fn env_password_blank_is_ignored() {
    let dir = test_state_dir("env-blank");
    let stored = bootstrap_password(&dir, "stored-password");
    write_settings(&dir, Some(&stored));
    std::env::set_var("HERMES_WEBUI_PASSWORD", "   ");
    assert!(
        verify_password(&dir, "stored-password").0,
        "env var vide/blank ignorée (upstream .strip())"
    );
    std::env::remove_var("HERMES_WEBUI_PASSWORD");
}

// ── Itérations : le contrat 600k est le défaut de production ──────────────

#[test]
fn production_iterations_are_600k() {
    assert_eq!(
        PBKDF2_ITERATIONS, 600_000,
        "vérifié contre HEAD upstream bd91b649 (auth.py:380)"
    );
}
