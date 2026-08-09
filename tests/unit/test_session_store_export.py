import json

from api.session_store import SqliteSessionStore
from scripts.export_webui_sessions import export_sessions


def test_export_is_dry_run_by_default_and_atomic_on_apply(tmp_path):
    db = tmp_path / "state.db"
    output = tmp_path / "export"
    payload = {
        "session_id": "export_001",
        "title": "Export",
        "messages": [{"role": "user", "content": "hello", "timestamp": 10}],
        "tool_calls": [],
        "anchor_activity_scenes": {},
    }
    SqliteSessionStore(db).save("export_001", payload)

    dry = export_sessions(db_path=db, output_dir=output)
    assert dry.dry_run is True
    assert dry.exported == 1
    assert not output.exists()

    applied = export_sessions(db_path=db, output_dir=output, apply=True)
    target = output / "export_001.json"
    assert applied.exported == 1
    assert json.loads(target.read_text()) == payload
    assert not list(output.glob("*.tmp"))


def test_export_does_not_overwrite_without_explicit_flag(tmp_path):
    db = tmp_path / "state.db"
    output = tmp_path / "export"
    output.mkdir()
    target = output / "export_001.json"
    target.write_text("legacy")
    SqliteSessionStore(db).save("export_001", {"session_id": "export_001", "messages": []})

    result = export_sessions(db_path=db, output_dir=output, apply=True)

    assert result.skipped_existing == 1
    assert target.read_text() == "legacy"
