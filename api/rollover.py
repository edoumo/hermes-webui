"""Session rollover with summary — automatic continuation for oversized sessions.

Trigger: session file > ROLLOVER_SIZE_THRESHOLD (50 MiB) after a turn.
Flow: detect -> mark pending -> frontend banner (countdown 60s) -> user accepts
      or timeout -> generate summary (deepseek-v4-flash:0731 via call_llm) ->
      create continuation session with summary as first message -> archive old
      session (copy to sessions_archive/, reversible) + archived flag.

Safety:
  - Detection is a cheap stat() + flag check; the summary LLM call runs in a
    daemon thread so the request path never blocks.
  - No rollover while a stream is active on the session.
  - No double proposal: a session is only proposed once per "generation"
    (re-proposed only after the session grows again past the threshold).
  - Summary failure -> no rollover, re-proposed on a later turn.
  - Archiving is a copy, never a delete.
"""

import json
import logging
import os
import re
import shutil
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ROLLOVER_SIZE_THRESHOLD = 50 * 1024 * 1024  # 50 MiB per Ed's decision
ROLLOVER_COUNTDOWN_SECS = 60
ROLLOVER_SUMMARY_MODEL = os.getenv(
    "HERMES_WEBUI_ROLLOVER_SUMMARY_MODEL", "deepseek-v4-flash:0731"
).strip()
ROLLOVER_SUMMARY_PROVIDER = os.getenv(
    "HERMES_WEBUI_ROLLOVER_SUMMARY_PROVIDER", "ollama-cloud"
).strip()
ROLLOVER_SUMMARY_MAX_TOKENS = 2048
ROLLOVER_SUMMARY_TIMEOUT = 120
ROLLOVER_LAST_MESSAGES = 50  # summary input: last N messages + compression anchor

# ---------------------------------------------------------------------------
# State (in-memory + persisted marker file)
# ---------------------------------------------------------------------------
_PENDING: dict[str, dict] = {}
_PENDING_LOCK = threading.Lock()
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# Marker file lives next to the session file: <sid>.json.rollover
# Content: {"proposed_at": epoch, "generation": int}
# generation increments each time the session crosses the threshold again,
# so a "later" answer does not re-propose forever on every turn.


def _marker_path(session_id: str) -> Path:
    from api.config import SESSION_DIR
    if not _SAFE_SESSION_ID.fullmatch(str(session_id or "")):
        raise ValueError("invalid session id")
    return SESSION_DIR / f"{session_id}.json.rollover"


def _read_marker(session_id: str) -> dict:
    try:
        p = _marker_path(session_id)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        logger.debug("rollover marker read failed for %s", session_id, exc_info=True)
    return {}


def _write_marker(session_id: str, data: dict) -> None:
    try:
        p = _marker_path(session_id)
        p.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        logger.debug("rollover marker write failed for %s", session_id, exc_info=True)


def _clear_marker(session_id: str) -> None:
    try:
        _marker_path(session_id).unlink(missing_ok=True)
    except Exception:
        logger.debug("rollover marker clear failed for %s", session_id, exc_info=True)


# ---------------------------------------------------------------------------
# Size detection
# ---------------------------------------------------------------------------
def session_file_size(session_id: str) -> int:
    """Return the on-disk size of the session JSON file (0 if missing)."""
    from api.config import SESSION_DIR
    try:
        if not _SAFE_SESSION_ID.fullmatch(str(session_id or "")):
            return 0
        p = SESSION_DIR / f"{session_id}.json"
        return p.stat().st_size if p.exists() else 0
    except Exception:
        logger.debug("rollover size stat failed for %s", session_id, exc_info=True)
        return 0


def is_oversized(session_id: str) -> bool:
    return session_file_size(session_id) > ROLLOVER_SIZE_THRESHOLD


# ---------------------------------------------------------------------------
# Summary generation (daemon thread)
# ---------------------------------------------------------------------------
def _build_summary_input(session) -> tuple[str, str]:
    """Return (anchor_summary, last_messages_text) without loading the full file.

    Uses the persisted compression_anchor_summary (already computed by the
    context engine) plus the last ROLLOVER_LAST_MESSAGES messages from the
    in-memory session object (already loaded by the caller).
    """
    anchor = str(getattr(session, "compression_anchor_summary", "") or "").strip()
    messages = list(getattr(session, "messages", None) or [])
    tail = messages[-ROLLOVER_LAST_MESSAGES:]
    parts = []
    for m in tail:
        role = str(m.get("role", "") if isinstance(m, dict) else getattr(m, "role", ""))
        content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
        if isinstance(content, list):
            # multimodal content blocks: keep text parts only
            text_parts = [
                str(b.get("text", ""))
                for b in content
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
            ]
            content = "\n".join(text_parts)
        content = str(content or "").strip()
        if not content:
            continue
        # Bound each message to keep the prompt small
        if len(content) > 4000:
            content = content[:4000] + "…[truncated]"
        parts.append(f"[{role}]\n{content}")
    return anchor, "\n\n".join(parts)


_SUMMARY_SYSTEM_PROMPT = (
    "Tu es un assistant qui produit un résumé de continuité de session de chat. "
    "Le résumé doit permettre de continuer le travail dans une NOUVELLE session "
    "sans relire l'ancienne. Structure obligatoire, en français, format texte "
    "simple (pas de markdown lourd) :\n"
    "CONTEXTE: objectif global et sujet de la session\n"
    "DECISIONS: décisions prises et validées\n"
    "TRAVAIL_FAIT: actions accomplies, fichiers touchés, commandes exécutées\n"
    "ETAT_ACTUEL: où on en est précisément (fichiers, services, résultats)\n"
    "PROCHAINE_ETAPE: la prochaine action à faire\n"
    "POINTS_ATTENTION: pièges, secrets nommés sans les exposer, contraintes\n"
    "Sois dense et factuel. Ne répète pas le contenu brut des messages."
)


def generate_summary(session_id: str, session=None) -> str:
    """Generate the continuation summary via the auxiliary LLM route.

    ``session`` may be passed in when the caller already holds the loaded
    session object (end-of-turn / session-open hooks) — re-loading a 100+ MiB
    session here would pay the exact GIL cost the rollover is meant to avoid.
    Raises on failure so the caller can decide not to roll over.
    """
    from api.models import get_session
    from agent.auxiliary_client import call_llm

    if session is None:
        session = get_session(session_id, metadata_only=False)
    if session is None:
        raise RuntimeError(f"session {session_id} not found for summary")

    anchor, tail_text = _build_summary_input(session)
    user_content = (
        f"Résumé de compression existant :\n{anchor}\n\n"
        f"Derniers messages de la session :\n{tail_text}"
        if anchor
        else f"Derniers messages de la session :\n{tail_text}"
    )
    if not tail_text and not anchor:
        raise RuntimeError("no content to summarize")

    resp = call_llm(
        task="compression",
        provider=ROLLOVER_SUMMARY_PROVIDER,
        model=ROLLOVER_SUMMARY_MODEL,
        messages=[
            {"role": "system", "content": _SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        max_tokens=ROLLOVER_SUMMARY_MAX_TOKENS,
        temperature=0.2,
        timeout=ROLLOVER_SUMMARY_TIMEOUT,
    )
    try:
        text = resp.choices[0].message.content
    except Exception:
        text = str(resp)
    text = str(text or "").strip()
    if not text:
        raise RuntimeError("empty summary from LLM")
    return text


# ---------------------------------------------------------------------------
# Rollover execution
# ---------------------------------------------------------------------------
def _archive_session(session_id: str) -> str | None:
    """Copy the session file (and sidecars) to sessions_archive/. Reversible."""
    from api.config import SESSION_DIR, STATE_DIR
    try:
        src = SESSION_DIR / f"{session_id}.json"
        if not src.exists():
            return None
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest_dir = STATE_DIR / "sessions_archive" / f"{stamp}-{session_id}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / f"{session_id}.json")
        # Sidecars (best effort)
        for sub in ("_run_journal", "_turn_journal"):
            sdir = SESSION_DIR / sub / session_id
            if sdir.exists():
                shutil.copytree(sdir, dest_dir / sub / session_id, dirs_exist_ok=True)
        return str(dest_dir)
    except Exception:
        logger.exception("rollover archive failed for %s", session_id)
        return None


def perform_rollover(session_id: str, summary: str) -> dict:
    """Create the continuation session, inject the summary, archive the old one.

    Returns {"new_session_id": ..., "archived_to": ...}.
    """
    from api.models import get_session, new_session, SESSIONS, LOCK
    from api.session_events import publish_session_list_changed

    source = get_session(session_id, metadata_only=False)
    if source is None:
        raise RuntimeError(f"session {session_id} not found for rollover")

    # 1) Archive first. A continuation without a durable, reversible source
    # archive violates the rollover contract and must never be published.
    archived_to = _archive_session(session_id)
    if not archived_to:
        raise RuntimeError(f"could not archive source session {session_id}")

    # 2) Create continuation session inheriting workspace/model/profile
    cont = new_session(
        workspace=getattr(source, "workspace", None),
        model=getattr(source, "model", None),
        model_provider=getattr(source, "model_provider", None),
        profile=getattr(source, "profile", None),
        project_id=getattr(source, "project_id", None),
    )
    cont.title = f"{source.title or 'Session'} (continuation)"
    cont.parent_session_id = session_id
    cont.session_source = "rollover"

    # 3) Inject the summary as the first assistant message
    summary_msg = {
        "role": "assistant",
        "content": (
            "📎 **Continuation de la session précédente** "
            f"(`{session_id}`)\n\n{summary}\n\n"
            f"*L'ancienne session est archivée et consultable.*"
        ),
    }
    cont.messages = [summary_msg]

    # 4) Persist continuation. The SESSIONS dict update must stay atomic
    # under LOCK, but the actual disk save must NOT run inside `with LOCK`:
    # save() -> _write_session_index() re-acquires the same non-reentrant
    # threading.Lock, which self-deadlocks the rollover thread permanently
    # (observed 2026-08-11: session 9d3e419bbabf at 117 MiB — the rollover
    # created the continuation, then blocked forever in save() holding LOCK,
    # freezing every /api/* request that touches get_session/all_sessions).
    # The dict insertion and the disk write are both ordered by the same
    # thread here, so no other thread can observe the session between the
    # two steps.
    with LOCK:
        SESSIONS[cont.session_id] = cont
        SESSIONS.move_to_end(cont.session_id)
    cont.save()

    # 5) Mark the source archived only after the continuation is durable.
    try:
        source.archived = True
        source.save()
    except Exception:
        logger.exception("rollover: failed to mark source archived for %s", session_id)

    # 6) Cleanup marker + pending state
    _clear_marker(session_id)
    with _PENDING_LOCK:
        _PENDING.pop(session_id, None)

    publish_session_list_changed("session_rollover", session_id=session_id)
    return {"new_session_id": cont.session_id, "archived_to": archived_to}


# ---------------------------------------------------------------------------
# Proposal orchestration
# ---------------------------------------------------------------------------
def maybe_propose_rollover(session_id: str, session=None) -> None:
    """Cheap, non-blocking check called at end of turn AND at session open.

    If the session is oversized and not already pending, marks it pending and
    spawns the summary generation in a daemon thread. The frontend learns the
    proposal via /api/session/status (rollover field).

    ``session`` may be passed in when the caller already holds the loaded
    session object (e.g. GET /api/session) to avoid a second load. When
    omitted, a metadata-only load is used for the active-stream guard.
    """
    if not _SAFE_SESSION_ID.fullmatch(str(session_id or "")):
        return
    try:
        if session is None:
            from api.models import get_session

            session = get_session(session_id, metadata_only=True)
        # Never propose while a stream is active on the session: the file is
        # being rewritten by the checkpoint thread and the user is mid-turn.
        if session is not None and getattr(session, "active_stream_id", None):
            return
        if not is_oversized(session_id):
            return
        with _PENDING_LOCK:
            if session_id in _PENDING:
                return
            marker = _read_marker(session_id)
            # Re-propose only when the session grew again (generation bump)
            if marker.get("generation", 0) >= 1:
                return
            _PENDING[session_id] = {
                "proposed_at": time.time(),
                "status": "proposing",
                "summary": None,
                "error": None,
            }
            _write_marker(session_id, {"proposed_at": time.time(), "generation": 1})
        t = threading.Thread(
            target=_proposal_worker,
            args=(session_id, session),
            daemon=True,
            name=f"rollover-{session_id[:8]}",
        )
        t.start()
    except Exception:
        logger.debug("rollover proposal check failed for %s", session_id, exc_info=True)


def _proposal_worker(session_id: str, session=None) -> None:
    """Generate the summary in background; store it for the frontend."""
    try:
        summary = generate_summary(session_id, session=session)
        with _PENDING_LOCK:
            if session_id in _PENDING:
                _PENDING[session_id]["summary"] = summary
                _PENDING[session_id]["status"] = "proposed"
    except Exception as e:
        logger.warning("rollover summary failed for %s: %s", session_id, e)
        with _PENDING_LOCK:
            if session_id in _PENDING:
                _PENDING[session_id]["status"] = "failed"
                _PENDING[session_id]["error"] = str(e)[:300]
                # Allow re-proposal on a later turn
                _clear_marker(session_id)
                _PENDING.pop(session_id, None)


def rollover_status(session_id: str) -> dict:
    """Status payload for /api/session/status."""
    with _PENDING_LOCK:
        pending = _PENDING.get(session_id)
        if pending is None:
            return {"status": "none"}
        return {
            "status": pending.get("status", "none"),
            "proposed_at": pending.get("proposed_at"),
            "summary_ready": bool(pending.get("summary")),
            "countdown_secs": ROLLOVER_COUNTDOWN_SECS,
            "threshold_mb": ROLLOVER_SIZE_THRESHOLD // (1024 * 1024),
            "error": pending.get("error"),
        }


def resolve_rollover(session_id: str, action: str) -> dict:
    """Handle user decision: 'now' | 'later' | 'auto' (timeout -> now)."""
    if not _SAFE_SESSION_ID.fullmatch(str(session_id or "")):
        return {"status": "failed", "error": "invalid session id"}
    action = str(action or "").strip().lower()
    if action == "later":
        with _PENDING_LOCK:
            _PENDING.pop(session_id, None)
        # Keep marker generation=1 so we do not re-propose every turn;
        # re-proposal happens only if the session grows past threshold again
        # (generation bump handled by marker rewrite on next crossing).
        return {"status": "deferred"}

    # 'now' or 'auto' (timeout): perform the rollover
    with _PENDING_LOCK:
        pending = _PENDING.get(session_id) or {}
        summary = pending.get("summary")
    if not summary:
        # Summary not ready yet: NEVER generate synchronously here — the
        # LLM call plus a full session reload would block the HTTP thread
        # and pay the exact GIL cost the rollover is meant to avoid. Return
        # pending; the frontend re-polls /api/session/status and retries.
        return {"status": "pending", "error": "summary not ready yet"}
    try:
        result = perform_rollover(session_id, summary)
        return {"status": "done", **result}
    except Exception as e:
        logger.exception("rollover execution failed for %s", session_id)
        return {"status": "failed", "error": str(e)[:300]}
