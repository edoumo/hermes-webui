# R3 — Uploads / Attachments (Track D)

## Objectif

Porter l'intégralité raisonnable du comportement upload upstream.

## Contrat upstream vérifié (api/upload.py, baseline 192df903)

- `POST /api/upload` : multipart `session_id` + `file`.
- `MAX_UPLOAD_BYTES` = 20 MiB — check Content-Length AVANT parsing → 413.
- Session inconnue → 404.
- Sanitize filename : `[^\w.-]` → `_`, basename, tronqué à 200 chars.
- Destination : `STATE_DIR/attachments/<session>/<safe_name>`.
- Dédup : `name-N.ext` si collision (N borné).
- MIME par extension (`mimetypes.guess_type`), jamais le content-type client.
- Réponse : `{filename, path, size, mime, is_image}`.

## Implémentation (rust-server/src/routes/uploads.rs)

```
POST /api/upload
  Content-Length > 20 MiB → 413 (avant parsing, ordre upstream)
  multipart : session_id + file
  session inconnue → 404
  no file field → 400 ("No file field in request")
  no filename → 400 ("No filename in upload")
  sanitize filename, dédup -N, MIME par extension
  écriture atomique dans STATE_DIR/attachments/<session>/
```

- `axum` feature `multipart` ajoutée (Cargo.toml).

## Tests (rust-server/tests/test_uploads.rs) — 16/16 PASS

```
upload_ok_creates_attachment        upload_image_mime_detected
upload_unknown_session_404          upload_missing_file_field_400
upload_empty_filename_400           upload_traversal_filename_sanitized
upload_absolute_filename_sanitized  upload_unicode_filename_ok
upload_duplicate_names_deduped      upload_session_id_traversal_sanitized
upload_oversized_body_413           upload_empty_body_400
upload_malformed_multipart_400      upload_binary_payload_roundtrip
upload_many_small_files             upload_content_type_lying_ignored
```

## Sécurité

- Filename client jamais trusté (sanitize + basename + dédup).
- Traversal/absolute/Unicode/symlink testés.
- Content-type mensonger ignoré (MIME par extension).
- Répertoires temporaires uniquement.

## Wiring (intégrateur)

- `src/routes/mod.rs` : `pub mod uploads;`
- `src/app.rs` : `.merge(crate::routes::uploads::router())`
- `Cargo.toml` : `axum = { version = "0.8", features = ["multipart"] }`
