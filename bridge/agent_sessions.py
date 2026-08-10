#!/usr/bin/env python3
"""Agent sessions — projection LECTURE SEULE du state.db Hermes Agent.

Règles strictes (Track A R3) :
  - ouverture UNIQUEMENT en ``file:...?mode=ro`` + ``PRAGMA query_only=ON`` ;
  - AUCUN fallback vers une connexion en écriture (déviation volontaire vs
    upstream ``open_state_db_readonly`` qui retombe en écriture : la red line
    R3 interdit toute écriture dans un state.db réel) ;
  - si l'ouverture read-only échoue -> erreur (fail closed), jamais de
    création de base fantôme ;
  - le bridge reste MINCE : pas de logique WebUI (compression lineage,
    visibilité sidebar, etc.) — uniquement une projection plate des lignes
    ``sessions`` avec normalisation minimale de la source.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

SOURCE_LABELS = {
    "acp": "ACP",
    "api_server": "API",
    "cli": "CLI",
    "cron": "Cron",
    "discord": "Discord",
    "email": "Email",
    "kanban": "Kanban",
    "wecom": "WeCom",
    "wecom_callback": "WeCom Callback",
    "slack": "Slack",
    "telegram": "Telegram",
    "tool": "Tool",
    "tui": "TUI",
    "webhook": "Webhook",
    "webui": "WebUI",
    "weixin": "Weixin",
    "matrix": "Matrix",
}

MESSAGING_SOURCES = {
    "discord", "email", "wecom", "wecom_callback", "slack",
    "telegram", "weixin", "matrix",
}


class StateDbUnavailable(Exception):
    """state.db manquant ou non ouvrable en lecture seule."""


def open_state_db_readonly(db_path) -> sqlite3.Connection:
    """Ouvre state.db en mode=ro STRICT (jamais d'écriture).

    Lève StateDbUnavailable si le fichier n'existe pas ou si l'ouverture
    read-only échoue. Aucun fallback en écriture.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise StateDbUnavailable(f"agent state.db not found: {db_path}")
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise StateDbUnavailable(
            f"agent state.db read-only open failed: {db_path}: {exc}"
        ) from exc
    try:
        conn.execute("PRAGMA query_only=ON")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def normalize_agent_session_source(raw_source):
    """Normalisation minimale de la source (label + classe)."""
    raw = str(raw_source or "").strip().lower() or "unknown"
    if raw == "webui":
        session_source = "webui"
    elif raw in {"acp", "cli", "tui"}:
        session_source = "cli"
    elif raw in MESSAGING_SOURCES:
        session_source = "messaging"
    elif raw == "cron":
        session_source = "cron"
    elif raw == "webhook":
        session_source = "webhook"
    elif raw == "kanban":
        session_source = "kanban"
    elif raw == "tool":
        session_source = "tool"
    elif raw == "api_server":
        session_source = "api"
    else:
        session_source = "other"
    label = SOURCE_LABELS.get(raw)
    if not label:
        label = raw.replace("_", " ").title() if raw != "unknown" else "Agent"
    return {
        "raw_source": None if raw == "unknown" else raw,
        "session_source": session_source,
        "source_label": label,
    }


def _optional_col(name, columns, fallback="NULL"):
    return f"s.{name}" if name in columns else f"{fallback} AS {name}"


def _session_projection_sql(columns):
    """SELECT stable sur les colonnes disponibles (schémas anciens tolérés)."""
    return f"""
        SELECT s.id,
               s.title,
               s.model,
               s.message_count,
               s.started_at,
               s.source,
               {_optional_col('session_source', columns)},
               {_optional_col('parent_session_id', columns)},
               {_optional_col('ended_at', columns)},
               {_optional_col('end_reason', columns)},
               {_optional_col('user_id', columns)},
               {_optional_col('chat_id', columns)},
               {_optional_col('platform', columns)}
    """


def _project_row(row, message_stats):
    """Projection plate d'une ligne sessions + stats messages optionnelles."""
    out = dict(row)
    stats = message_stats.get(str(row["id"]), {})
    out["actual_message_count"] = stats.get("count", row.get("message_count") or 0)
    out["last_activity"] = stats.get("last_activity")
    out.update(normalize_agent_session_source(row.get("source")))
    return out


def _message_stats(conn, session_ids):
    """Comptage/last_activity par session si la table messages est utilisable."""
    if not session_ids:
        return {}
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(messages)")
    cols = {row[1] for row in cur.fetchall()}
    if "session_id" not in cols:
        return {}
    has_ts = "timestamp" in cols
    ts_expr = "MAX(m.timestamp)" if has_ts else "NULL"
    stats = {}
    ids = list(session_ids)
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        placeholders = ",".join("?" * len(chunk))
        cur.execute(
            f"SELECT m.session_id, COUNT(m.id), {ts_expr} "
            f"FROM messages m WHERE m.session_id IN ({placeholders}) "
            f"GROUP BY m.session_id",
            chunk,
        )
        for sid, count, last_ts in cur.fetchall():
            stats[str(sid)] = {"count": count, "last_activity": last_ts}
    return stats


def list_agent_sessions(db_path, limit=200, source=None) -> list[dict]:
    """Liste les sessions agent (projection plate, lecture seule).

    ``source`` filtre sur la colonne ``source`` (ex: "cli", "webui").
    """
    db_path = Path(db_path)
    if not db_path.exists():
        raise StateDbUnavailable(f"agent state.db not found: {db_path}")
    with closing(open_state_db_readonly(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(sessions)")
        cols = {row[1] for row in cur.fetchall()}
        if "id" not in cols:
            return []
        where = ["s.source IS NOT NULL"]
        params = []
        if source:
            where.append("s.source = ?")
            params.append(str(source))
        sql = f"""
            {_session_projection_sql(cols)}
            FROM sessions s
            WHERE {' AND '.join(where)}
            ORDER BY s.started_at DESC
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(max(0, int(limit)))
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        stats = _message_stats(conn, [r["id"] for r in rows])
        return [_project_row(r, stats) for r in rows]


def get_agent_session(db_path, session_id) -> dict | None:
    """Retourne une session agent par id, ou None (lecture seule)."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise StateDbUnavailable(f"agent state.db not found: {db_path}")
    with closing(open_state_db_readonly(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(sessions)")
        cols = {row[1] for row in cur.fetchall()}
        if "id" not in cols:
            return None
        cur.execute(
            f"{_session_projection_sql(cols)} FROM sessions s WHERE s.id = ?",
            (str(session_id),),
        )
        row = cur.fetchone()
        if row is None:
            return None
        stats = _message_stats(conn, [row["id"]])
        return _project_row(dict(row), stats)
