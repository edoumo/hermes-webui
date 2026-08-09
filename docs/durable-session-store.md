# Durable WebUI Session Store Operations

This document describes the feature-flagged SQLite session store. The default remains the legacy JSON store until a production migration and shadow-validation gate are completed.

## Storage contract

- `state.db:sessions` owns session identity and aggregate counters.
- `state.db:messages` owns canonical transcript content, one active row per message.
- `webui_session_state` owns UI metadata, a monotonic compare-and-swap revision, counts, and a content fingerprint.
- `webui_message_state` maps canonical message IDs to WebUI positions and stores only reconstruction metadata not represented by the core schema.
- `webui_context_messages`, `webui_tool_calls`, and `webui_components` store WebUI-only render/context data.
- Legacy JSON files are migration inputs in `legacy` and `shadow`; SQLite mode writes bounded manifests without transcript arrays.

Agent-appended core message rows are adopted when their core content matches the WebUI transcript tail. They are not inserted a second time. Replaced transcript rows are soft-deactivated (`messages.active=0`) rather than deleted.

## Modes

Set `HERMES_WEBUI_SESSION_STORE` before starting the server:

- `legacy` (default): read and write legacy JSON only.
- `shadow`: legacy JSON remains authoritative; SQLite receives best-effort writes. SQLite failures are logged and do not block the JSON write.
- `sqlite`: SQLite is authoritative. Writes are transactional and stale writers fail closed through revision comparison. JSON files become bounded discovery manifests.

An unknown value is treated as `legacy`.

## Migration

The command is dry-run unless `--apply` is supplied:

```bash
.venv/bin/python scripts/migrate_webui_sessions.py \
  --session-dir "$HOME/.hermes/webui/sessions" \
  --db "$HOME/.hermes/state.db"
```

Apply only after a database backup and successful dry-run:

```bash
.venv/bin/python scripts/migrate_webui_sessions.py \
  --session-dir "$HOME/.hermes/webui/sessions" \
  --db "$HOME/.hermes/state.db" \
  --apply
```

Properties:

- source JSON is never deleted, moved, or modified;
- each session is committed independently;
- the migration journal records `started`, `verified`, or `failed`;
- verification compares the exact payload SHA-256 fingerprint after reconstruction;
- a second run skips already verified and unchanged sources;
- missing parent sessions are projected as `NULL` in the core FK while the original WebUI metadata remains preserved.

## Required pre-cutover checks

Run these against a copy first:

```sql
PRAGMA integrity_check;
PRAGMA foreign_key_check;
SELECT status, count(*) FROM webui_migration_journal GROUP BY status;
SELECT count(*) FROM webui_message_state w
LEFT JOIN messages m ON m.id = w.message_id
WHERE m.id IS NULL;
```

Acceptance criteria:

- `integrity_check` is `ok`;
- migration failures are zero;
- every eligible source is `verified`;
- a second migration imports zero sessions;
- no new foreign-key violation is introduced;
- no orphan `webui_message_state` row exists;
- authenticated create, update, send, reload, pagination, clear, branch, rollover, and delete flows pass in an isolated server;
- the full test suite passes with an isolated `HERMES_HOME` and `HERMES_WEBUI_STATE_DIR`.

## Progressive deployment

1. Commit the tested implementation before changing the service.
2. Back up `state.db`, its WAL/SHM state through SQLite backup, and preserve legacy JSON.
3. Run migration dry-run, then apply.
4. Start in `shadow` mode.
5. Compare store fingerprints and logs through representative live turns and restarts.
6. Switch to `sqlite` only after zero divergence.
7. Keep legacy JSON until the retention gate is explicitly approved.

Do not expose the service on a new public address as part of this change.

## Rollback

Before leaving shadow mode, validate a full export to a separate directory:

```bash
.venv/bin/python scripts/export_webui_sessions.py \
  --db "$HOME/.hermes/state.db" \
  --output-dir /path/to/rollback-export

.venv/bin/python scripts/export_webui_sessions.py \
  --db "$HOME/.hermes/state.db" \
  --output-dir /path/to/rollback-export \
  --apply
```

The exporter is dry-run by default, writes atomically, and never overwrites a file unless `--overwrite` is explicit.

To roll back after SQLite-only writes:

1. Stop or quiesce writes.
2. Export every normalized session to a separate directory and verify counts/fingerprints.
3. Restore the exported full JSON snapshots to the legacy session directory with an explicit reviewed overwrite.
4. Set `HERMES_WEBUI_SESSION_STORE=legacy`.
5. Restart the WebUI.
6. Verify session listing, a historical session, and a new turn.

Existing full legacy JSON is never replaced by a manifest during migration or the first SQLite save; it remains the rollback baseline. New sessions receive bounded manifests. Legacy and shadow modes continue writing authoritative JSON. Never delete `webui_*` or inactive core rows during rollback.

## SQLite safety

- Foreign keys are enabled per connection.
- Transactions use `BEGIN IMMEDIATE` and `synchronous=FULL`.
- Existing journal mode is preserved.
- On SQLite versions older than 3.46.1, creating a new database directly in WAL mode is refused because of the WAL-reset vulnerability; initialize safely or upgrade SQLite first.
