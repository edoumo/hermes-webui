"""Canonical SQLite persistence for Hermes WebUI sessions.

The Hermes ``sessions`` and ``messages`` tables own session identity and transcript
content.  Additive ``webui_*`` tables contain only WebUI state that Hermes core
does not model (UI metadata, context projection, display tool records and scenes).
Writes are short transactions with compare-and-swap revisions; transcript rewrites
soft-archive superseded core rows instead of deleting audit history.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable

_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_COLLECTION_KEYS = frozenset({"messages", "context_messages", "tool_calls", "anchor_activity_scenes"})
_CONTENT_JSON_PREFIX = "\x00json:"
_JSON_MESSAGE_COLUMNS = frozenset({
    "tool_calls", "reasoning_details", "codex_reasoning_items", "codex_message_items",
})
_CORE_MESSAGE_KEYS = frozenset({
    "role", "content", "tool_call_id", "tool_calls", "tool_name", "effect_disposition",
    "timestamp", "token_count", "finish_reason", "reasoning", "reasoning_content",
    "reasoning_details", "codex_reasoning_items", "codex_message_items",
    "platform_message_id", "message_id", "observed", "api_content",
})


class ConcurrentSessionWrite(RuntimeError):
    """Raised when a stale in-memory Session attempts to overwrite a newer revision."""


def _wal_reset_vulnerable(version_info: tuple[int, ...] | None = None) -> bool:
    info = version_info if version_info is not None else sqlite3.sqlite_version_info
    if info < (3, 7, 0) or info >= (3, 51, 3):
        return False
    if (3, 50, 7) <= info < (3, 51, 0):
        return False
    if (3, 44, 6) <= info < (3, 45, 0):
        return False
    return True


def _configure_journal(con: sqlite3.Connection) -> str:
    current = str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if _wal_reset_vulnerable():
        if current != "wal":
            con.execute("PRAGMA journal_mode=DELETE")
            return "delete"
        return "wal"
    try:
        return str(con.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
    except sqlite3.OperationalError:
        con.execute("PRAGMA journal_mode=DELETE")
        return "delete"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _digest(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def fingerprint_payload(payload: dict[str, Any]) -> str:
    return _digest(_json(payload))


def _validate_session_id(session_id: str) -> str:
    value = str(session_id or "")
    if not value or not _SAFE_SESSION_ID.fullmatch(value):
        raise ValueError(f"Unsafe session_id {session_id!r}")
    return value


def _encode_content(content: Any) -> Any:
    if isinstance(content, (list, dict)):
        return _CONTENT_JSON_PREFIX + json.dumps(content, ensure_ascii=True, separators=(",", ":"))
    return content


def _decode_content(content: Any) -> Any:
    if isinstance(content, str) and content.startswith(_CONTENT_JSON_PREFIX):
        try:
            return json.loads(content[len(_CONTENT_JSON_PREFIX):])
        except (TypeError, json.JSONDecodeError):
            return content
    return content


@dataclass(frozen=True)
class SaveStats:
    preserved_messages: int = 0
    deleted_messages: int = 0
    inserted_messages: int = 0
    preserved_tool_calls: int = 0
    deleted_tool_calls: int = 0
    inserted_tool_calls: int = 0
    generation: int = 0


_CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    model_config TEXT,
    system_prompt TEXT,
    parent_session_id TEXT REFERENCES sessions(id),
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_write_tokens INTEGER DEFAULT 0,
    reasoning_tokens INTEGER DEFAULT 0,
    billing_provider TEXT,
    billing_base_url TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    pricing_version TEXT,
    title TEXT,
    api_call_count INTEGER DEFAULT 0,
    handoff_state TEXT,
    handoff_platform TEXT,
    handoff_error TEXT,
    session_key TEXT,
    chat_id TEXT,
    chat_type TEXT,
    thread_id TEXT,
    display_name TEXT,
    origin_json TEXT,
    expiry_finalized INTEGER DEFAULT 0,
    cwd TEXT,
    git_branch TEXT,
    git_repo_root TEXT,
    compression_failure_cooldown_until REAL,
    compression_failure_error TEXT,
    compression_fallback_streak INTEGER NOT NULL DEFAULT 0,
    rewind_count INTEGER NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    profile_name TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT,
    tool_call_id TEXT,
    tool_calls TEXT,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    reasoning TEXT,
    reasoning_content TEXT,
    reasoning_details TEXT,
    codex_reasoning_items TEXT,
    codex_message_items TEXT,
    platform_message_id TEXT,
    observed INTEGER DEFAULT 0,
    effect_disposition TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0,
    api_content TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_session_active ON messages(session_id, active, id);
"""

_WEBUI_SCHEMA = """
CREATE TABLE IF NOT EXISTS webui_session_state (
    session_id TEXT PRIMARY KEY REFERENCES sessions(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL DEFAULT 0,
    ui_metadata_json TEXT NOT NULL,
    collection_keys_json TEXT NOT NULL DEFAULT '[]',
    user_message_count INTEGER NOT NULL DEFAULT 0,
    content_fingerprint TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS webui_message_state (
    message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    original_keys_json TEXT NOT NULL,
    extra_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webui_message_state_window
    ON webui_message_state(session_id, position, message_id);
CREATE TABLE IF NOT EXISTS webui_context_messages (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(session_id, position)
);
CREATE TABLE IF NOT EXISTS webui_tool_calls (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(session_id, position)
);
CREATE TABLE IF NOT EXISTS webui_components (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    component_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    PRIMARY KEY(session_id, component_key)
);
CREATE TABLE IF NOT EXISTS webui_migration_journal (
    session_id TEXT PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    source_mtime_ns INTEGER NOT NULL,
    source_sha256 TEXT NOT NULL,
    store_fingerprint TEXT,
    status TEXT NOT NULL,
    error TEXT,
    migrated_at REAL NOT NULL
);
"""


class SqliteSessionStore:
    """Repository over Hermes canonical sessions/messages plus additive WebUI state."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        busy_timeout_ms: int = 15_000,
        trace_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser()
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.trace_callback = trace_callback
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_CORE_SCHEMA)
            con.executescript(_WEBUI_SCHEMA)
            columns = {str(row[1]) for row in con.execute("PRAGMA table_info(webui_session_state)")}
            if "collection_keys_json" not in columns:
                con.execute(
                    "ALTER TABLE webui_session_state ADD COLUMN collection_keys_json "
                    "TEXT NOT NULL DEFAULT '[]'"
                )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=self.busy_timeout_ms / 1000)
        con.row_factory = sqlite3.Row
        con.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        con.execute("PRAGMA foreign_keys=ON")
        _configure_journal(con)
        con.execute("PRAGMA synchronous=FULL")
        if self.trace_callback is not None:
            con.set_trace_callback(self.trace_callback)
        return con

    @staticmethod
    def _upsert_core_session(con: sqlite3.Connection, session_id: str, metadata: dict[str, Any]) -> None:
        parent = metadata.get("parent_session_id")
        if parent and con.execute("SELECT 1 FROM sessions WHERE id=?", (parent,)).fetchone() is None:
            parent = None
        started = float(metadata.get("created_at") or metadata.get("started_at") or time.time())
        con.execute(
            "INSERT INTO sessions(id,source,model,parent_session_id,started_at,title,cwd,archived,profile_name) "
            "VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "model=COALESCE(excluded.model,sessions.model), cwd=COALESCE(excluded.cwd,sessions.cwd), "
            "archived=excluded.archived, profile_name=COALESCE(excluded.profile_name,sessions.profile_name)",
            (
                session_id,
                str(metadata.get("session_source") or metadata.get("source") or "webui"),
                metadata.get("model"),
                parent,
                started,
                None,
                str(metadata.get("workspace")) if metadata.get("workspace") else None,
                1 if metadata.get("archived") else 0,
                metadata.get("profile"),
            ),
        )

    @staticmethod
    def _message_parts(message: dict[str, Any]) -> tuple[dict[str, Any], str, str, str]:
        encoded = _json(message)
        keys_json = _json(sorted(message))
        extras = {key: value for key, value in message.items() if key not in _CORE_MESSAGE_KEYS}
        overrides: dict[str, Any] = {}
        # SQLite REAL affinity returns integral timestamps as float. Preserve the
        # original JSON type as reconstruction metadata, not transcript content.
        if "timestamp" in message and not isinstance(message.get("timestamp"), float):
            overrides["timestamp"] = message.get("timestamp")
        reconstruction = {
            "__webui_store_v": 1,
            "extra": extras,
            "overrides": overrides,
        }
        return message, keys_json, _json(reconstruction), _digest(encoded)

    @staticmethod
    def _insert_core_message(
        con: sqlite3.Connection,
        session_id: str,
        position: int,
        message: dict[str, Any],
        keys_json: str,
        extra_json: str,
        digest: str,
    ) -> int:
        def encoded_json(key: str) -> str | None:
            value = message.get(key)
            return json.dumps(value, ensure_ascii=True, separators=(",", ":")) if value is not None else None

        timestamp = message.get("timestamp")
        try:
            timestamp = float(timestamp) if timestamp is not None else time.time() + position * 1e-6
        except (TypeError, ValueError):
            timestamp = time.time() + position * 1e-6
        platform_id = message.get("platform_message_id") or message.get("message_id")
        cursor = con.execute(
            "INSERT INTO messages(session_id,role,content,tool_call_id,tool_calls,tool_name," 
            "effect_disposition,timestamp,token_count,finish_reason,reasoning,reasoning_content," 
            "reasoning_details,codex_reasoning_items,codex_message_items,platform_message_id," 
            "observed,active,api_content) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                str(message.get("role") or "unknown"),
                _encode_content(message.get("content")),
                message.get("tool_call_id"),
                encoded_json("tool_calls"),
                message.get("tool_name"),
                message.get("effect_disposition"),
                timestamp,
                message.get("token_count"),
                message.get("finish_reason"),
                message.get("reasoning"),
                message.get("reasoning_content"),
                encoded_json("reasoning_details"),
                encoded_json("codex_reasoning_items"),
                encoded_json("codex_message_items"),
                platform_id,
                1 if message.get("observed") else 0,
                1,
                message.get("api_content"),
            ),
        )
        message_id = int(cursor.lastrowid)
        con.execute(
            "INSERT INTO webui_message_state(message_id,session_id,position,original_keys_json,extra_json,payload_sha256) "
            "VALUES(?,?,?,?,?,?)",
            (message_id, session_id, position, keys_json, extra_json, digest),
        )
        return message_id

    @staticmethod
    def _core_row_matches(row: sqlite3.Row, message: dict[str, Any]) -> bool:
        """Return whether an agent-written core row represents ``message``.

        Hermes may append to ``messages`` before WebUI persists its richer view.
        Matching rows are adopted by attaching WebUI metadata instead of inserting
        a duplicate canonical message.
        """
        expected: dict[str, Any] = {
            "role": str(message.get("role") or "unknown"),
            "content": _encode_content(message.get("content")),
        }
        scalar_keys = (
            "tool_call_id", "tool_name", "effect_disposition", "token_count",
            "finish_reason", "reasoning", "reasoning_content", "api_content",
        )
        for key in scalar_keys:
            if key in message:
                expected[key] = message.get(key)
        for key in ("tool_calls", "reasoning_details", "codex_reasoning_items", "codex_message_items"):
            if key in message:
                value = message.get(key)
                expected[key] = (
                    json.dumps(value, ensure_ascii=True, separators=(",", ":"))
                    if value is not None else None
                )
        if "platform_message_id" in message or "message_id" in message:
            expected["platform_message_id"] = (
                message.get("platform_message_id") or message.get("message_id")
            )
        if "observed" in message:
            expected["observed"] = 1 if message.get("observed") else 0
        if "timestamp" in message and message.get("timestamp") is not None:
            try:
                expected["timestamp"] = float(message["timestamp"])
            except (TypeError, ValueError):
                return False
        return all(row[key] == value for key, value in expected.items())

    @staticmethod
    def _reconcile_messages(
        con: sqlite3.Connection, session_id: str, incoming: list[dict[str, Any]]
    ) -> tuple[int, int, int]:
        existing = con.execute(
            "SELECT m.*,w.position,w.payload_sha256 FROM messages m "
            "LEFT JOIN webui_message_state w ON w.message_id=m.id "
            "WHERE m.session_id=? AND m.active=1 ORDER BY m.id",
            (session_id,),
        ).fetchall()
        prefix = 0
        prepared: list[tuple[dict[str, Any], str, str, str]] = []
        for position, message in enumerate(incoming):
            if not isinstance(message, dict):
                raise ValueError(f"message at position {position} must be an object")
            parts = SqliteSessionStore._message_parts(message)
            prepared.append(parts)
            if prefix != position or position >= len(existing):
                continue
            row = existing[position]
            if row["payload_sha256"] is not None:
                if row["payload_sha256"] == parts[3]:
                    prefix += 1
                continue
            if SqliteSessionStore._core_row_matches(row, message):
                _, keys_json, extra_json, digest = parts
                con.execute(
                    "INSERT INTO webui_message_state(message_id,session_id,position,original_keys_json," 
                    "extra_json,payload_sha256) VALUES(?,?,?,?,?,?)",
                    (int(row["id"]), session_id, position, keys_json, extra_json, digest),
                )
                prefix += 1
        tail_rows = existing[prefix:]
        if tail_rows:
            con.executemany("UPDATE messages SET active=0 WHERE id=?", ((row["id"],) for row in tail_rows))
        for position in range(prefix, len(prepared)):
            message, keys_json, extra_json, digest = prepared[position]
            SqliteSessionStore._insert_core_message(
                con, session_id, position, message, keys_json, extra_json, digest
            )
        return prefix, len(tail_rows), max(0, len(prepared) - prefix)

    @staticmethod
    def _reconcile_json_rows(
        con: sqlite3.Connection, table: str, session_id: str, incoming: list[Any]
    ) -> tuple[int, int, int]:
        existing = [str(row[0]) for row in con.execute(
            f"SELECT payload_sha256 FROM {table} WHERE session_id=? ORDER BY position", (session_id,)
        )]
        encoded = [(_json(value), _digest(_json(value))) for value in incoming]
        prefix = 0
        while prefix < min(len(existing), len(encoded)) and existing[prefix] == encoded[prefix][1]:
            prefix += 1
        deleted = len(existing) - prefix
        con.execute(f"DELETE FROM {table} WHERE session_id=? AND position>=?", (session_id, prefix))
        if prefix < len(encoded):
            con.executemany(
                f"INSERT INTO {table}(session_id,position,payload_json,payload_sha256) VALUES(?,?,?,?)",
                ((session_id, position, value, digest) for position, (value, digest) in enumerate(encoded[prefix:], prefix)),
            )
        return prefix, deleted, len(encoded) - prefix

    def save(
        self,
        session_id: str,
        payload: dict[str, Any],
        *,
        expected_revision: int | None = None,
        force: bool = False,
        before_commit: Callable[[], None] | None = None,
    ) -> SaveStats:
        session_id = _validate_session_id(session_id)
        if str(payload.get("session_id") or session_id) != session_id:
            raise ValueError("payload session_id does not match repository key")
        metadata = {key: value for key, value in payload.items() if key not in _COLLECTION_KEYS}
        metadata["session_id"] = session_id
        messages = list(payload.get("messages") or [])
        context_messages = list(payload.get("context_messages") or [])
        tool_calls = list(payload.get("tool_calls") or [])
        scenes = dict(payload.get("anchor_activity_scenes") or {})
        fingerprint = fingerprint_payload(payload)
        collection_keys_json = _json(sorted(set(payload) & _COLLECTION_KEYS))
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            current = con.execute(
                "SELECT revision FROM webui_session_state WHERE session_id=?", (session_id,)
            ).fetchone()
            current_revision = int(current[0]) if current else None
            if current_revision is not None and not force and expected_revision != current_revision:
                raise ConcurrentSessionWrite(
                    f"session {session_id} revision changed: expected {expected_revision}, current {current_revision}"
                )
            if current_revision is None and expected_revision not in (None, 0) and not force:
                raise ConcurrentSessionWrite(f"session {session_id} has no WebUI revision")
            revision = (current_revision or 0) + 1
            self._upsert_core_session(con, session_id, metadata)
            preserved_m, deleted_m, inserted_m = self._reconcile_messages(con, session_id, messages)
            _, deleted_c, _ = self._reconcile_json_rows(
                con, "webui_context_messages", session_id, context_messages
            )
            preserved_t, deleted_t, inserted_t = self._reconcile_json_rows(
                con, "webui_tool_calls", session_id, tool_calls
            )
            existing_scenes = {str(row[0]) for row in con.execute(
                "SELECT component_key FROM webui_components WHERE session_id=?", (session_id,)
            )}
            incoming_scenes = {str(key) for key in scenes}
            con.executemany(
                "DELETE FROM webui_components WHERE session_id=? AND component_key=?",
                ((session_id, key) for key in existing_scenes - incoming_scenes),
            )
            for key, value in scenes.items():
                value_json = _json(value)
                con.execute(
                    "INSERT INTO webui_components(session_id,component_key,payload_json,payload_sha256) "
                    "VALUES(?,?,?,?) ON CONFLICT(session_id,component_key) DO UPDATE SET "
                    "payload_json=excluded.payload_json,payload_sha256=excluded.payload_sha256",
                    (session_id, str(key), value_json, _digest(value_json)),
                )
            user_count = sum(1 for message in messages if message.get("role") == "user")
            con.execute(
                "UPDATE sessions SET message_count=?,tool_call_count=?,model=COALESCE(?,model)," 
                "cwd=COALESCE(?,cwd),archived=? WHERE id=?",
                (len(messages), len(tool_calls), metadata.get("model"), metadata.get("workspace"),
                 1 if metadata.get("archived") else 0, session_id),
            )
            con.execute(
                "INSERT INTO webui_session_state(session_id,revision,ui_metadata_json,collection_keys_json," 
                "user_message_count,content_fingerprint,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET "
                "revision=excluded.revision,ui_metadata_json=excluded.ui_metadata_json," 
                "collection_keys_json=excluded.collection_keys_json," 
                "user_message_count=excluded.user_message_count,content_fingerprint=excluded.content_fingerprint," 
                "updated_at=excluded.updated_at",
                (session_id, revision, _json(metadata), collection_keys_json, user_count, fingerprint,
                 float(metadata.get("updated_at") or time.time())),
            )
            if before_commit is not None:
                before_commit()
            con.commit()
            return SaveStats(preserved_m, deleted_m, inserted_m, preserved_t, deleted_t, inserted_t, revision)
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _message_from_row(row: sqlite3.Row) -> dict[str, Any]:
        keys = set(json.loads(row["original_keys_json"]))
        reconstruction = json.loads(row["extra_json"])
        if reconstruction.get("__webui_store_v") == 1:
            result = dict(reconstruction.get("extra") or {})
            overrides = dict(reconstruction.get("overrides") or {})
        else:  # compatibility with early feature-flagged validation databases
            result = dict(reconstruction)
            overrides = {}
        for key in keys:
            if key not in _CORE_MESSAGE_KEYS:
                continue
            column = "platform_message_id" if key == "message_id" else key
            value = row[column]
            if key == "content":
                value = _decode_content(value)
            elif key in _JSON_MESSAGE_COLUMNS and value is not None:
                try:
                    value = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    pass
            elif key == "observed":
                value = bool(value)
            result[key] = value
        result.update(overrides)
        return result

    def load_metadata(self, session_id: str) -> dict[str, Any] | None:
        session_id = _validate_session_id(session_id)
        with self._connect() as con:
            row = con.execute(
                "SELECT w.ui_metadata_json,w.user_message_count,w.revision,w.content_fingerprint," 
                "s.message_count,s.tool_call_count FROM webui_session_state w "
                "JOIN sessions s ON s.id=w.session_id WHERE w.session_id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        metadata = json.loads(row["ui_metadata_json"])
        metadata.update({
            "_store_message_count": int(row["message_count"] or 0),
            "_store_user_message_count": int(row["user_message_count"] or 0),
            "_store_tool_call_count": int(row["tool_call_count"] or 0),
            "_store_generation": int(row["revision"]),
            "_store_fingerprint": row["content_fingerprint"],
        })
        return metadata

    def load(self, session_id: str) -> dict[str, Any] | None:
        session_id = _validate_session_id(session_id)
        metadata = self.load_metadata(session_id)
        if metadata is None:
            return None
        with self._connect() as con:
            state = con.execute(
                "SELECT collection_keys_json FROM webui_session_state WHERE session_id=?", (session_id,)
            ).fetchone()
            collection_keys = set(json.loads(state[0])) if state else set()
            rows = con.execute(
                "SELECT m.*,w.position,w.original_keys_json,w.extra_json FROM messages m "
                "JOIN webui_message_state w ON w.message_id=m.id "
                "WHERE m.session_id=? AND m.active=1 ORDER BY w.position,m.id",
                (session_id,),
            ).fetchall()
            contexts = [json.loads(row[0]) for row in con.execute(
                "SELECT payload_json FROM webui_context_messages WHERE session_id=? ORDER BY position", (session_id,)
            )]
            tools = [json.loads(row[0]) for row in con.execute(
                "SELECT payload_json FROM webui_tool_calls WHERE session_id=? ORDER BY position", (session_id,)
            )]
            scenes = {str(row[0]): json.loads(row[1]) for row in con.execute(
                "SELECT component_key,payload_json FROM webui_components WHERE session_id=? ORDER BY component_key",
                (session_id,),
            )}
        metadata.pop("_store_message_count", None)
        metadata.pop("_store_user_message_count", None)
        metadata.pop("_store_tool_call_count", None)
        metadata.pop("_store_generation", None)
        metadata.pop("_store_fingerprint", None)
        if "messages" in collection_keys:
            metadata["messages"] = [self._message_from_row(row) for row in rows]
        if "context_messages" in collection_keys:
            metadata["context_messages"] = contexts
        if "tool_calls" in collection_keys:
            metadata["tool_calls"] = tools
        if "anchor_activity_scenes" in collection_keys:
            metadata["anchor_activity_scenes"] = scenes
        return metadata

    def page_messages(self, session_id: str, *, before_position: int | None = None, limit: int = 100) -> dict[str, Any]:
        session_id = _validate_session_id(session_id)
        limit = max(1, min(int(limit), 500))
        with self._connect() as con:
            counts = con.execute(
                "SELECT s.message_count,w.user_message_count FROM sessions s JOIN webui_session_state w "
                "ON w.session_id=s.id WHERE s.id=?", (session_id,)
            ).fetchone()
            if counts is None:
                raise KeyError(session_id)
            clause = "" if before_position is None else " AND w.position < ?"
            params: list[Any] = [session_id]
            if before_position is not None:
                params.append(int(before_position))
            params.append(limit)
            rows = con.execute(
                "SELECT m.*,w.position,w.original_keys_json,w.extra_json FROM messages m "
                "JOIN webui_message_state w ON w.message_id=m.id "
                "WHERE m.session_id=? AND m.active=1" + clause + " ORDER BY w.position DESC,m.id DESC LIMIT ?",
                params,
            ).fetchall()
        rows.reverse()
        positions = [int(row["position"]) for row in rows]
        total = int(counts["message_count"] or 0)
        return {
            "messages": [self._message_from_row(row) for row in rows],
            "positions": positions,
            "total": total,
            "user_message_count": int(counts["user_message_count"] or 0),
            "has_older": bool(positions and positions[0] > 0),
            "has_newer": bool(positions and positions[-1] < total - 1),
            "next_before_position": positions[0] if positions else None,
        }

    def tool_calls_for_message_window(self, session_id: str, *, start_position: int, end_position: int) -> list[dict[str, Any]]:
        session_id = _validate_session_id(session_id)
        with self._connect() as con:
            rows = con.execute(
                "SELECT payload_json FROM webui_tool_calls WHERE session_id=? "
                "AND CAST(json_extract(payload_json,'$.assistant_msg_idx') AS INTEGER)>=? "
                "AND CAST(json_extract(payload_json,'$.assistant_msg_idx') AS INTEGER)<? ORDER BY position",
                (session_id, int(start_position), int(end_position)),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def scenes_for_message_window(self, session_id: str, *, start_position: int, end_position: int) -> dict[str, Any]:
        session_id = _validate_session_id(session_id)
        with self._connect() as con:
            rows = con.execute(
                "SELECT component_key,payload_json FROM webui_components WHERE session_id=? "
                "AND CAST(json_extract(payload_json,'$.message_index') AS INTEGER)>=? "
                "AND CAST(json_extract(payload_json,'$.message_index') AS INTEGER)<? ORDER BY component_key",
                (session_id, int(start_position), int(end_position)),
            ).fetchall()
        return {str(row[0]): json.loads(row[1]) for row in rows}

    def list_session_ids(self) -> list[str]:
        with self._connect() as con:
            return [str(row[0]) for row in con.execute(
                "SELECT session_id FROM webui_session_state ORDER BY session_id"
            )]

    def get_fingerprint(self, session_id: str) -> str | None:
        session_id = _validate_session_id(session_id)
        with self._connect() as con:
            row = con.execute(
                "SELECT content_fingerprint FROM webui_session_state WHERE session_id=?", (session_id,)
            ).fetchone()
        return str(row[0]) if row else None

    def migration_record(self, session_id: str) -> dict[str, Any] | None:
        session_id = _validate_session_id(session_id)
        with self._connect() as con:
            row = con.execute("SELECT * FROM webui_migration_journal WHERE session_id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def record_migration(
        self, session_id: str, *, source_path: str, source_size: int, source_mtime_ns: int,
        source_sha256: str, store_fingerprint: str | None, status: str, error: str | None = None,
    ) -> None:
        session_id = _validate_session_id(session_id)
        with self._connect() as con:
            con.execute(
                "INSERT INTO webui_migration_journal(session_id,source_path,source_size,source_mtime_ns," 
                "source_sha256,store_fingerprint,status,error,migrated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET source_path=excluded.source_path," 
                "source_size=excluded.source_size,source_mtime_ns=excluded.source_mtime_ns," 
                "source_sha256=excluded.source_sha256,store_fingerprint=excluded.store_fingerprint," 
                "status=excluded.status,error=excluded.error,migrated_at=excluded.migrated_at",
                (session_id, source_path, int(source_size), int(source_mtime_ns), source_sha256,
                 store_fingerprint, status, error, time.time()),
            )

    def journal_mode(self) -> str:
        with self._connect() as con:
            return str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    def integrity_check(self) -> str:
        with self._connect() as con:
            return str(con.execute("PRAGMA integrity_check").fetchone()[0])
