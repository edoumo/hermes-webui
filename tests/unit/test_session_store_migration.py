import json

from scripts.migrate_webui_sessions import migrate_sessions


def _session(session_id, content):
    return {
        "session_id": session_id,
        "title": session_id,
        "workspace": "/tmp",
        "created_at": 1.0,
        "updated_at": 2.0,
        "messages": [{"role": "user", "content": content}],
        "tool_calls": [],
        "anchor_activity_scenes": {},
    }


def test_dry_run_is_default_and_does_not_create_database(tmp_path):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "one.json").write_text(json.dumps(_session("one", "hello")), encoding="utf-8")
    db_path = tmp_path / "state.db"

    result = migrate_sessions(session_dir=session_dir, db_path=db_path)

    assert result.dry_run is True
    assert result.eligible == 1
    assert result.imported == 0
    assert not db_path.exists()


def test_apply_is_idempotent_and_preserves_sources(tmp_path):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    source = session_dir / "one.json"
    source.write_text(json.dumps(_session("one", "hello")), encoding="utf-8")
    original = source.read_bytes()
    db_path = tmp_path / "state.db"

    first = migrate_sessions(session_dir=session_dir, db_path=db_path, apply=True)
    second = migrate_sessions(session_dir=session_dir, db_path=db_path, apply=True)

    assert first.imported == 1
    assert first.verified == 1
    assert first.failed == 0
    assert second.imported == 0
    assert second.unchanged == 1
    assert source.read_bytes() == original


def test_corrupt_and_sqlite_manifest_files_are_not_imported(tmp_path):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    (session_dir / "bad.json").write_text("{broken", encoding="utf-8")
    (session_dir / "manifest.json").write_text(
        json.dumps({"session_id": "manifest", "session_store_backend": "sqlite", "messages": []}),
        encoding="utf-8",
    )

    result = migrate_sessions(session_dir=session_dir, db_path=tmp_path / "state.db", apply=True)

    assert result.imported == 0
    assert result.failed == 1
    assert result.skipped == 1
