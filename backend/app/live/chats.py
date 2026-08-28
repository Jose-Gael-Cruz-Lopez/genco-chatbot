"""The live chat lifecycle.

Every chat ends exactly one of four ways, and every ending notifies the lead that
was stored when the visitor handed over their email. That is the rule that makes
offering live chat safe: there is no exit from a conversation that leaves the
visitor un-followed-up.

Timeouts are evaluated lazily, in sweep(), whenever either side reads the chat.
Both sides poll every couple of seconds, so this needs no scheduler and no
background worker — and it self-corrects after a restart.
"""
import logging
from datetime import UTC, datetime, timedelta

from app.chat.memory import get_recent_messages, save_message
from app.config import get_settings
from app.db import get_supabase
from app.escalation import notify_lead
from app.live.presence import is_agent_available, parse_ts

log = logging.getLogger(__name__)

WAITING = "waiting"
ACTIVE = "active"
ENDED = "ended"

END_AGENT_ENDED = "agent_ended"
END_AGENT_DROPPED = "agent_dropped"
END_VISITOR_LEFT = "visitor_left"
END_NOT_ACCEPTED = "not_accepted"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def start_chat(session_id: str, question: str, lead_id: str | None) -> dict:
    """Open a waiting chat. The lead already exists — see flows._handle_live_email."""
    resp = get_supabase().table("live_chats").insert({
        "session_id": session_id,
        "lead_id": lead_id,
        "question": question,
        "status": WAITING,
    }).execute()
    return (resp.data or [{}])[0]


def current_chat(session_id: str) -> dict | None:
    """The session's newest chat that has not ended, or None."""
    try:
        resp = (get_supabase().table("live_chats")
                .select("*").eq("session_id", session_id).neq("status", ENDED)
                .order("started_at", desc=True).limit(1).execute())
        rows = resp.data or []
        return rows[0] if rows else None
    except Exception:
        log.exception("live chat lookup failed for session %s", session_id)
        return None


def list_open() -> list[dict]:
    """Every waiting or active chat, oldest first — the portal's queue."""
    try:
        resp = (get_supabase().table("live_chats")
                .select("*").neq("status", ENDED)
                .order("started_at", desc=False).execute())
        return resp.data or []
    except Exception:
        log.exception("live chat queue read failed")
        return []


def touch_visitor(session_id: str) -> None:
    """Record that the visitor is still on the page. Best-effort by design: a
    missed touch ages toward visitor_left, and the next poll corrects it."""
    try:
        (get_supabase().table("live_chats")
         .update({"visitor_last_seen_at": _now_iso()})
         .eq("session_id", session_id).neq("status", ENDED).execute())
    except Exception:
        log.exception("visitor touch failed for session %s", session_id)


def accept(session_id: str) -> dict | None:
    """Mark a waiting chat active — called when the agent opens its transcript."""
    chat = current_chat(session_id)
    if chat is None or chat.get("status") != WAITING:
        return chat
    try:
        (get_supabase().table("live_chats")
         .update({"status": ACTIVE, "accepted_at": _now_iso()})
         .eq("id", chat["id"]).execute())
    except Exception:
        log.exception("accepting chat %s failed", chat.get("id"))
        return chat
    return {**chat, "status": ACTIVE}


def add_agent_message(session_id: str, text: str) -> None:
    """Store an agent turn. It shares chat_messages with the bot and the visitor,
    so /history and the transcript need no special casing."""
    save_message(session_id, "agent", text)


def agent_messages_since(session_id: str, after: str | None) -> list[dict]:
    """Agent turns newer than `after` (an ISO timestamp), oldest first."""
    try:
        query = (get_supabase().table("chat_messages")
                 .select("role,content,created_at")
                 .eq("session_id", session_id).eq("role", "agent"))
        if after:
            query = query.gt("created_at", after)
        return (query.order("created_at", desc=False).execute().data) or []
    except Exception:
        log.exception("reading agent messages failed for session %s", session_id)
        return []


def _transcript(session_id: str) -> str:
    speaker = {"user": "Visitor", "agent": "Team", "assistant": "Bot"}
    lines = [f"{speaker.get(m.get('role'), m.get('role'))}: {m.get('content', '')}"
             for m in get_recent_messages(session_id, limit=100)]
    return "\n".join(lines)


def end_chat(chat: dict, reason: str) -> dict:
    """Close a chat and make sure its lead is notified exactly once.

    The transcript is written onto the lead's message field first, so the existing
    lead email carries the whole conversation with no change to email_service.
    """
    sb = get_supabase()
    try:
        sb.table("live_chats").update({
            "status": ENDED, "ended_at": _now_iso(), "ended_reason": reason,
        }).eq("id", chat["id"]).execute()
    except Exception:
        log.exception("ending chat %s failed", chat.get("id"))

    lead_id = chat.get("lead_id")
    if lead_id:
        # Attaching the transcript and notifying are separate steps on purpose.
        # The chat is marked ended above whatever happens here, so if a failed
        # transcript write also skipped the notification the visitor would be
        # stranded with no follow-up and nothing left to retry it. Losing the
        # transcript is survivable; losing the notification is not.
        stored: dict = {"id": lead_id}
        try:
            body = (f"Live chat ({reason}).\n"
                    f"Question: {chat.get('question') or ''}\n\n"
                    f"{_transcript(chat['session_id'])}")
            resp = sb.table("leads").update({"message": body}).eq(
                "id", lead_id).execute()
            stored = (resp.data or [{}])[0] or {"id": lead_id}
        except Exception:
            log.exception("attaching the transcript to lead %s after chat %s "
                          "failed; notifying anyway", lead_id, chat.get("id"))
        try:
            notify_lead(stored)
        except Exception:
            # The lead row exists either way; a failed notification is logged and
            # recoverable, and must never stop the chat from closing.
            log.exception("notifying lead %s after chat %s failed",
                          lead_id, chat.get("id"))
    return {**chat, "status": ENDED, "ended_reason": reason}


def sweep(chat: dict, now: datetime | None = None) -> dict:
    """Apply the timeout rules. Called on every read from either side."""
    if chat.get("status") == ENDED:
        return chat
    s = get_settings()
    moment = now or datetime.now(UTC)

    if chat.get("status") == WAITING:
        started = parse_ts(chat.get("started_at"))
        if started and moment - started > timedelta(
                seconds=s.LIVE_ACCEPT_TIMEOUT_SECONDS):
            return end_chat(chat, END_NOT_ACCEPTED)
        return chat

    # Active: the agent going quiet is the failure the email loop exists to catch.
    if not is_agent_available(now=moment):
        return end_chat(chat, END_AGENT_DROPPED)
    seen = parse_ts(chat.get("visitor_last_seen_at"))
    if seen and moment - seen > timedelta(seconds=s.LIVE_VISITOR_IDLE_SECONDS):
        return end_chat(chat, END_VISITOR_LEFT)
    return chat
