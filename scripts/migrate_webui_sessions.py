#!/usr/bin/env python3
"""Idempotently migrate monolithic WebUI JSON sessions into normalized SQLite.

Dry-run is the default. Source files are never moved, rewritten or deleted.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from api.session_store import SqliteSessionStore, fingerprint_payload


@dataclass
class MigrationResult:
    dry_run: bool
    scanned: int = 0
    eligible: int = 0
    imported: int = 0
    verified: int = 0
    unchanged: int = 0
    skipped: int = 0
    failed: int = 0


def _source_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def migrate_sessions(
    *,
    session_dir: str | Path,
    db_path: str | Path,
    apply: bool = False,
) -> MigrationResult:
    session_dir = Path(session_dir).expanduser().resolve()
    db_path = Path(db_path).expanduser().resolve()
    result = MigrationResult(dry_run=not apply)
    store = SqliteSessionStore(db_path) if apply else None

    for source in sorted(session_dir.glob("*.json")):
        # WebUI bookkeeping sidecars (index, tombstones, etc.) are not sessions.
        if source.name.startswith("_"):
            continue
        result.scanned += 1
        try:
            raw = source.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("top-level JSON must be an object")
        except Exception:
            result.failed += 1
            continue

        if payload.get("session_store_backend") == "sqlite":
            result.skipped += 1
            continue
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            result.failed += 1
            continue

        result.eligible += 1
        if not apply:
            continue

        assert store is not None
        stat = source.stat()
        source_hash = _source_sha256(raw)
        expected_fingerprint = fingerprint_payload(payload)
        try:
            record = store.migration_record(session_id)
            if (
                record
                and record.get("status") == "verified"
                and record.get("source_sha256") == source_hash
                and store.get_fingerprint(session_id) == expected_fingerprint
            ):
                result.unchanged += 1
                continue

            store.save(session_id, payload, force=True)
            actual = store.load(session_id)
            actual_fingerprint = fingerprint_payload(actual) if actual is not None else None
            if actual_fingerprint != expected_fingerprint:
                raise ValueError("post-write fingerprint mismatch")
            store.record_migration(
                session_id,
                source_path=str(source),
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_sha256=source_hash,
                store_fingerprint=actual_fingerprint,
                status="verified",
            )
            result.imported += 1
            result.verified += 1
        except Exception as exc:
            result.failed += 1
            try:
                store.record_migration(
                    session_id,
                    source_path=str(source),
                    source_size=stat.st_size,
                    source_mtime_ns=stat.st_mtime_ns,
                    source_sha256=source_hash,
                    store_fingerprint=store.get_fingerprint(session_id),
                    status="failed",
                    error=f"{exc.__class__.__name__}: {exc}",
                )
            except Exception:
                pass

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-dir", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--apply", action="store_true", help="write migrations (default: dry-run)")
    args = parser.parse_args()
    result = migrate_sessions(session_dir=args.session_dir, db_path=args.db, apply=args.apply)
    print(json.dumps(asdict(result), sort_keys=True))
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
