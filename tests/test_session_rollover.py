from collections import OrderedDict
import threading

import api.models as models
import api.rollover as rollover
import api.session_events as session_events


class _Session:
    def __init__(self, session_id, events):
        self.session_id = session_id
        self.title = "Source"
        self.workspace = "/tmp"
        self.model = "model"
        self.model_provider = "provider"
        self.profile = "default"
        self.project_id = None
        self.messages = []
        self.active_stream_id = None
        self.archived = False
        self.parent_session_id = None
        self.session_source = None
        self._events = events

    def save(self, **_kwargs):
        self._events.append(f"save:{self.session_id}")


def test_invalid_session_id_cannot_escape_session_directory(tmp_path, monkeypatch):
    monkeypatch.setattr("api.config.SESSION_DIR", tmp_path)

    assert rollover.session_file_size("../escape") == 0
    assert rollover.resolve_rollover("../escape", "now") == {
        "status": "failed",
        "error": "invalid session id",
    }
    assert not list(tmp_path.iterdir())


def test_rollover_archives_before_publishing_continuation(monkeypatch):
    events = []
    source = _Session("source_001", events)
    continuation = _Session("continuation_001", events)
    sessions = OrderedDict()

    monkeypatch.setattr(models, "get_session", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(models, "new_session", lambda **_kwargs: continuation)
    monkeypatch.setattr(models, "SESSIONS", sessions)
    monkeypatch.setattr(models, "LOCK", threading.RLock())
    monkeypatch.setattr(rollover, "_archive_session", lambda _sid: events.append("archive") or "/archive")
    monkeypatch.setattr(rollover, "_clear_marker", lambda _sid: events.append("clear"))
    monkeypatch.setattr(session_events, "publish_session_list_changed", lambda *_args, **_kwargs: events.append("publish"))

    result = rollover.perform_rollover("source_001", "summary")

    assert result == {"new_session_id": "continuation_001", "archived_to": "/archive"}
    assert events.index("archive") < events.index("save:continuation_001") < events.index("publish")
    assert continuation.parent_session_id == "source_001"
    assert continuation.session_source == "rollover"
    assert source.archived is True


def test_rollover_aborts_if_reversible_archive_cannot_be_created(monkeypatch):
    events = []
    source = _Session("source_001", events)
    monkeypatch.setattr(models, "get_session", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(models, "new_session", lambda **_kwargs: events.append("new") or None)
    monkeypatch.setattr(rollover, "_archive_session", lambda _sid: None)

    try:
        rollover.perform_rollover("source_001", "summary")
    except RuntimeError as exc:
        assert "could not archive" in str(exc)
    else:
        raise AssertionError("rollover must fail closed when archival fails")
    assert "new" not in events


def test_rollover_does_not_save_continuation_under_lock(monkeypatch):
    # Regression (2026-08-11 incident, session 9d3e419bbabf @ 117 MiB):
    # perform_rollover used to call cont.save() inside `with LOCK:` while
    # save() -> _write_session_index() re-acquires the same non-reentrant
    # threading.Lock. That self-deadlock froze the whole WebUI API forever.
    # The test uses a REAL threading.Lock (not an RLock) and asserts the
    # continuation is saved only after LOCK is released. With the old code
    # this test deadlocks (or, with the guard below, fails fast).
    events = []
    lock = threading.Lock()
    source = _Session("source_001", events)
    continuation = _Session("continuation_001", events)

    def guarded_save():
        if lock.locked():
            raise AssertionError("save() called while LOCK held — self-deadlock")
        events.append(f"save:{continuation.session_id}")

    continuation.save = lambda **_: guarded_save()  # type: ignore[method-assign]

    monkeypatch.setattr(models, "get_session", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(models, "new_session", lambda **_kwargs: continuation)
    monkeypatch.setattr(models, "SESSIONS", OrderedDict())
    monkeypatch.setattr(models, "LOCK", lock)
    monkeypatch.setattr(rollover, "_archive_session", lambda _sid: events.append("archive") or "/archive")
    monkeypatch.setattr(rollover, "_clear_marker", lambda _sid: events.append("clear"))
    monkeypatch.setattr(session_events, "publish_session_list_changed", lambda *_args, **_kwargs: events.append("publish"))

    result = rollover.perform_rollover("source_001", "summary")

    assert result == {"new_session_id": "continuation_001", "archived_to": "/archive"}
    assert source.archived is True
    assert "save:continuation_001" in events
    assert events.index("archive") < events.index("save:continuation_001")
