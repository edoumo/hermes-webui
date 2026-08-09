import json

import api.models as models


def _configure_paths(monkeypatch, tmp_path):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(models, "_active_state_db_path", lambda: tmp_path / "state.db", raising=False)
    models._WEBUI_SESSION_STORES.clear()
    return session_dir


def test_sqlite_mode_writes_bounded_manifest_and_round_trips(monkeypatch, tmp_path):
    session_dir = _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_SESSION_STORE", "sqlite")
    message_body = "x" * 1_000_000
    session = models.Session(
        session_id="sqlite_roundtrip_001",
        workspace=tmp_path,
        title="SQLite session",
        messages=[{"role": "user", "content": message_body}],
        tool_calls=[{"id": "call-1", "result": "y" * 100_000}],
    )

    session.save(skip_index=True)

    manifest = session_dir / "sqlite_roundtrip_001.json"
    assert manifest.stat().st_size < 16_384
    assert message_body not in manifest.read_text(encoding="utf-8")
    loaded = models.Session.load("sqlite_roundtrip_001")
    assert loaded.messages == session.messages
    assert loaded.tool_calls == session.tool_calls
    assert loaded.title == session.title


def test_sqlite_mode_preserves_existing_legacy_json_for_rollback(monkeypatch, tmp_path):
    session_dir = _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_SESSION_STORE", "sqlite")
    path = session_dir / "sqlite_rollback_001.json"
    legacy_bytes = json.dumps({
        "session_id": "sqlite_rollback_001",
        "title": "legacy rollback baseline",
        "messages": [{"role": "user", "content": "legacy"}],
        "tool_calls": [],
        "anchor_activity_scenes": {},
    }).encode()
    path.write_bytes(legacy_bytes)
    session = models.Session(
        session_id="sqlite_rollback_001",
        workspace=tmp_path,
        title="new sqlite title",
        messages=[{"role": "user", "content": "new sqlite state"}],
    )

    session.save(skip_index=True)

    assert path.read_bytes() == legacy_bytes
    assert models.Session.load("sqlite_rollback_001").messages == session.messages


def test_sqlite_metadata_load_does_not_materialize_messages(monkeypatch, tmp_path):
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_SESSION_STORE", "sqlite")
    session = models.Session(
        session_id="sqlite_metadata_001",
        workspace=tmp_path,
        messages=[{"role": "user", "content": "large-body"}],
    )
    session.save(skip_index=True)

    metadata = models.Session.load_metadata_only("sqlite_metadata_001")

    assert metadata.messages == []
    assert metadata._metadata_message_count == 1
    assert metadata._loaded_metadata_only is True


def test_normalized_page_loader_bounds_materialized_history(monkeypatch, tmp_path):
    _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_SESSION_STORE", "sqlite")
    messages = [{"role": "user", "content": str(i)} for i in range(1_000)]
    session = models.Session(
        session_id="sqlite_page_001",
        workspace=tmp_path,
        messages=messages,
        tool_calls=[{"tid": "visible", "assistant_msg_idx": 998}],
    )
    session.save(skip_index=True)

    page = models.load_normalized_session_page("sqlite_page_001", msg_limit=3)

    assert page is not None
    assert len(page["session"].messages) <= 30
    assert page["total"] == 1_000
    assert page["base_position"] == 970
    assert page["session"]._loaded_metadata_only is True
    assert [call["tid"] for call in page["session"].tool_calls] == ["visible"]


def test_sqlite_mode_falls_back_to_legacy_json(monkeypatch, tmp_path):
    session_dir = _configure_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_SESSION_STORE", "sqlite")
    legacy = models.Session(
        session_id="legacy_fallback_001",
        workspace=tmp_path,
        messages=[{"role": "user", "content": "legacy"}],
    )
    payload = dict(legacy.__dict__)
    payload = {key: value for key, value in payload.items() if not key.startswith("_")}
    (session_dir / "legacy_fallback_001.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = models.Session.load("legacy_fallback_001")

    assert loaded.messages == legacy.messages
