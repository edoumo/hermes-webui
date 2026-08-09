#!/usr/bin/env python3
"""Export normalized WebUI sessions to atomic full JSON snapshots for rollback."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.session_store import SqliteSessionStore  # noqa: E402


@dataclass
class ExportResult:
    dry_run: bool
    discovered: int = 0
    exported: int = 0
    skipped_existing: int = 0
    failed: int = 0


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        tmp.unlink(missing_ok=True)


def export_sessions(
    *,
    db_path: Path,
    output_dir: Path,
    apply: bool = False,
    overwrite: bool = False,
    session_ids: list[str] | None = None,
) -> ExportResult:
    store = SqliteSessionStore(db_path)
    if session_ids is None:
        session_ids = store.list_session_ids()
    result = ExportResult(dry_run=not apply, discovered=len(session_ids))
    for session_id in session_ids:
        try:
            payload = store.load(session_id)
            if payload is None:
                result.failed += 1
                continue
            target = output_dir / f"{session_id}.json"
            if target.exists() and not overwrite:
                result.skipped_existing += 1
                continue
            if apply:
                _atomic_write_json(target, payload)
            result.exported += 1
        except Exception:
            result.failed += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--session-id", action="append", dest="session_ids")
    parser.add_argument("--apply", action="store_true", help="write JSON files; default is dry-run")
    parser.add_argument("--overwrite", action="store_true", help="replace existing JSON atomically")
    args = parser.parse_args()
    result = export_sessions(
        db_path=args.db,
        output_dir=args.output_dir,
        apply=args.apply,
        overwrite=args.overwrite,
        session_ids=args.session_ids,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
