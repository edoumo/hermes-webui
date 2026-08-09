import sqlite3

import pytest

from api.session_store import ConcurrentSessionWrite, SqliteSessionStore


def _payload(*, title="Test", messages=None, tool_calls=None, scenes=None, context_messages=None):
    return {
        "session_id": "session_001",
        "title": title,
        "created_at": 1.0,
        "updated_at": 2.0,
        "workspace": "/tmp",
        "model": "test-model",
        "messages": messages or [],
        "context_messages": context_messages or [],
        "tool_calls": tool_calls or [],
        "anchor_activity_scenes": scenes or {},
        "composer_draft": {"text": "draft"},
    }


def test_round_trip_uses_core_sessions_and_messages_as_canonical_rows(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")
    payload = _payload(
        messages=[
            {"role": "user", "content": "hello", "custom": {"a": 1}},
            {"role": "assistant", "content": "hi", "reasoning": "short"},
        ],
        context_messages=[{"role": "system", "content": "compressed context"}],
        tool_calls=[{"id": "call-1", "name": "terminal", "result": "ok"}],
        scenes={"scene-1": {"message_index": 1, "body": "x" * 1000}},
    )

    stats = store.save("session_001", payload)

    assert stats.inserted_messages == 2
    assert stats.inserted_tool_calls == 1
    assert store.load("session_001") == payload
    with sqlite3.connect(tmp_path / "state.db") as con:
        assert con.execute("SELECT COUNT(*) FROM sessions WHERE id='session_001'").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM messages WHERE session_id='session_001' AND active=1").fetchone()[0] == 2
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "webui_sessions" not in tables
        assert "webui_messages" not in tables
        metadata_json = con.execute(
            "SELECT ui_metadata_json FROM webui_session_state WHERE session_id='session_001'"
        ).fetchone()[0]
    assert "hello" not in metadata_json
    assert "call-1" not in metadata_json
    assert "scene-1" not in metadata_json


def test_append_only_save_preserves_core_message_ids_and_inserts_only_tail(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")
    first = _payload(messages=[{"role": "user", "content": "one"}])
    first_stats = store.save("session_001", first)
    with sqlite3.connect(tmp_path / "state.db") as con:
        first_id = con.execute(
            "SELECT id FROM messages WHERE session_id='session_001' AND active=1"
        ).fetchone()[0]
    second = _payload(messages=first["messages"] + [{"role": "assistant", "content": "two"}])

    stats = store.save("session_001", second, expected_revision=first_stats.generation)

    assert stats.preserved_messages == 1
    assert stats.deleted_messages == 0
    assert stats.inserted_messages == 1
    with sqlite3.connect(tmp_path / "state.db") as con:
        assert con.execute(
            "SELECT id FROM messages WHERE session_id='session_001' AND active=1 ORDER BY id LIMIT 1"
        ).fetchone()[0] == first_id
    assert store.load("session_001")["messages"] == second["messages"]


def test_changed_tail_is_soft_archived_and_prefix_id_is_preserved(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")
    original = _payload(messages=[
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "old"},
        {"role": "user", "content": "discard"},
    ])
    first = store.save("session_001", original)
    with sqlite3.connect(tmp_path / "state.db") as con:
        first_id = con.execute(
            "SELECT id FROM messages WHERE session_id='session_001' AND active=1 ORDER BY id LIMIT 1"
        ).fetchone()[0]
    changed = _payload(messages=[original["messages"][0], {"role": "assistant", "content": "new"}])

    stats = store.save("session_001", changed, expected_revision=first.generation)

    assert stats.preserved_messages == 1
    assert stats.deleted_messages == 2
    assert stats.inserted_messages == 1
    with sqlite3.connect(tmp_path / "state.db") as con:
        assert con.execute("SELECT COUNT(*) FROM messages WHERE session_id='session_001' AND active=0").fetchone()[0] == 2
        assert con.execute(
            "SELECT id FROM messages WHERE session_id='session_001' AND active=1 ORDER BY id LIMIT 1"
        ).fetchone()[0] == first_id
    assert store.load("session_001")["messages"] == changed["messages"]


def test_roundtrip_preserves_integral_timestamp_type(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")
    payload = _payload(messages=[{"role": "assistant", "content": "x", "timestamp": 1784281621}])
    store.save("session_001", payload)
    loaded = store.load("session_001")
    assert loaded == payload
    assert type(loaded["messages"][0]["timestamp"]) is int


def test_agent_appended_core_row_is_adopted_without_duplicate(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")
    first_payload = _payload(messages=[{"role": "user", "content": "one"}])
    first = store.save("session_001", first_payload)
    with sqlite3.connect(tmp_path / "state.db") as con:
        con.execute(
            "INSERT INTO messages(session_id,role,content,timestamp,active) VALUES(?,?,?,?,1)",
            ("session_001", "assistant", "agent reply", 3.0),
        )
        con.execute("UPDATE sessions SET message_count=2 WHERE id='session_001'")

    final_payload = _payload(messages=[
        {"role": "user", "content": "one"},
        {"role": "assistant", "content": "agent reply", "timestamp": 3.0},
    ])
    stats = store.save(
        "session_001", final_payload, expected_revision=first.generation
    )

    assert stats.preserved_messages == 2
    assert stats.inserted_messages == 0
    with sqlite3.connect(tmp_path / "state.db") as con:
        assert con.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id='session_001' AND active=1"
        ).fetchone()[0] == 2
        assert con.execute(
            "SELECT COUNT(*) FROM webui_message_state WHERE session_id='session_001'"
        ).fetchone()[0] == 2


def test_compare_and_swap_rejects_stale_writer(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")
    first = store.save("session_001", _payload(title="v1"))
    store.save("session_001", _payload(title="v2"), expected_revision=first.generation)

    with pytest.raises(ConcurrentSessionWrite):
        store.save("session_001", _payload(title="stale"), expected_revision=first.generation)

    assert store.load("session_001")["title"] == "v2"


def test_metadata_only_load_does_not_select_message_payloads(tmp_path):
    selected_sql = []
    store = SqliteSessionStore(tmp_path / "state.db", trace_callback=selected_sql.append)
    store.save("session_001", _payload(messages=[{"role": "user", "content": "secret-body"}]))
    selected_sql.clear()

    metadata = store.load_metadata("session_001")

    assert metadata["session_id"] == "session_001"
    assert metadata["_store_message_count"] == 1
    assert "messages" not in metadata
    assert not any("FROM messages" in sql for sql in selected_sql)


def test_failure_before_commit_keeps_previous_generation(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")
    original = _payload(title="committed", messages=[{"role": "user", "content": "safe"}])
    first = store.save("session_001", original)

    def fail():
        raise RuntimeError("injected crash")

    with pytest.raises(RuntimeError, match="injected crash"):
        store.save(
            "session_001",
            _payload(title="uncommitted", messages=[{"role": "user", "content": "lost"}]),
            expected_revision=first.generation,
            before_commit=fail,
        )

    assert store.load("session_001") == original


def test_cursor_page_returns_latest_messages_and_window_metadata(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")
    messages = [{"role": "user", "content": str(i)} for i in range(10)]
    tool_calls = [
        {"tid": "old", "assistant_msg_idx": 1},
        {"tid": "visible", "assistant_msg_idx": 8},
    ]
    scenes = {
        "old-scene": {"message_index": 1, "scene": {"title": "old"}},
        "visible-scene": {"message_index": 8, "scene": {"title": "visible"}},
    }
    store.save("session_001", _payload(messages=messages, tool_calls=tool_calls, scenes=scenes))

    latest = store.page_messages("session_001", limit=3)
    older = store.page_messages("session_001", before_position=latest["next_before_position"], limit=3)
    visible_tools = store.tool_calls_for_message_window("session_001", start_position=7, end_position=10)
    visible_scenes = store.scenes_for_message_window("session_001", start_position=7, end_position=10)

    assert [m["content"] for m in latest["messages"]] == ["7", "8", "9"]
    assert latest["has_older"] is True
    assert [m["content"] for m in older["messages"]] == ["4", "5", "6"]
    assert older["has_newer"] is True
    assert [call["tid"] for call in visible_tools] == ["visible"]
    assert list(visible_scenes) == ["visible-scene"]


def test_vulnerable_sqlite_does_not_enable_wal_on_a_fresh_database(tmp_path, monkeypatch):
    import api.session_store as session_store

    monkeypatch.setattr(session_store, "_wal_reset_vulnerable", lambda: True)
    store = SqliteSessionStore(tmp_path / "state.db")

    assert store.journal_mode() == "delete"


def test_invalid_session_id_is_rejected(tmp_path):
    store = SqliteSessionStore(tmp_path / "state.db")

    with pytest.raises(ValueError):
        store.save("../escape", _payload())
