import logging
from app.db import get_supabase
from app.email_service import send_lead_notification
from app.pipedrive import create_lead_in_pipedrive
from app.chat.tools import REQUIRED_FIELDS, humanize_lead_errors, validate_lead

log = logging.getLogger(__name__)
LOW_SIMILARITY = 0.25
HIGH_RISK_KEYWORDS = ("refund", "complaint", "lawyer", "press", "urgent")

# Widget quick-replies that start a lead flow ("Buy Sheets" is home delivery, not a lead).
_LEAD_QUICK_REPLIES = ("buy refill stations", "question for the team")
# Words an assistant field request mentions (the lead intents' required fields).
_LEAD_FIELD_HINTS = ("name", "email", "phone", "organization", "sheet",
                     "laundry room", "student", "tenant")


def _looks_like_field_request(content: str) -> bool:
    # A field request asks a question about one of the lead fields. Only text up to the LAST
    # question mark counts, so trailing boilerplate ("... email Info@...") and the greeting's
    # option list after its "?" don't false-positive.
    text = content.lower()
    q_end = text.rfind("?")
    if q_end == -1:
        return False
    return any(hint in text[:q_end + 1] for hint in _LEAD_FIELD_HINTS)


def is_lead_flow_turn(history: list[dict], current_message: str = "") -> bool:
    """True when the conversation is mid-lead-collection: the user picked a lead-intent
    quick-reply (now or recently), or the previous assistant message asked for a lead field.
    Field answers ("John Smith, john@acme.com", "500 sheets") score low against the KB, so the
    grounding safety net must not hijack these turns."""
    if current_message.strip().lower() in _LEAD_QUICK_REPLIES:
        return True
    for m in history:
        if m.get("role") == "user" and (m.get("content") or "").strip().lower() in _LEAD_QUICK_REPLIES:
            return True
    last_assistant = next((m for m in reversed(history) if m.get("role") == "assistant"), None)
    return bool(last_assistant and _looks_like_field_request(last_assistant.get("content") or ""))


# Server-side grounding safety net, wired into the chat flow (chat/router.py): when the top
# retrieval similarity is below LOW_SIMILARITY or a high-risk keyword appears, the turn is
# routed to the team instead of risking an ungrounded answer.
# Exception: mid-lead-flow turns (lead_flow=True) are never hijacked — field answers naturally
# score low and may contain incidental keywords ("City Urgent Care").
# A separate "the model said it lacks the info" override (model_signal) was removed rather than
# wired: detecting a lacks-info reply is a brittle marker-phrase heuristic that can swap a good
# answer for the canned escalation, and the un-groundable case is already covered twice — the
# system prompt's grounding rule directs the model itself to offer the team, and the similarity
# threshold above catches turns with no KB support.
def should_escalate(retrieval_scores: list[float],
                    text: str = "", lead_flow: bool = False) -> bool:
    if lead_flow:
        return False
    top = max(retrieval_scores) if retrieval_scores else 0.0
    if top < LOW_SIMILARITY:
        return True
    return any(k in text.lower() for k in HIGH_RISK_KEYWORDS)


def notify_lead(stored: dict) -> None:
    """Best-effort notification for an already-stored lead.

    Split out of capture_lead so live chat can store a lead at connect time and
    notify only when the conversation ends. Failures are flagged in the row and
    never raised: the lead is already safe in Supabase.
    """
    try:
        if send_lead_notification(stored):
            get_supabase().table("leads").update({"emailed": True}).eq(
                "id", stored["id"]).execute()
    except Exception:
        log.exception("lead %s email failed", stored.get("id"))
    try:
        if create_lead_in_pipedrive(stored):
            get_supabase().table("leads").update(
                {"pushed_to_pipedrive": True}).eq("id", stored["id"]).execute()
    except Exception:
        log.exception("lead %s pipedrive failed", stored.get("id"))


def capture_lead(session_id: str, intent: str, fields: dict,
                 notify: bool = True) -> dict:
    errors = validate_lead(intent, fields)
    if errors:
        # Log the machine-readable errors; raise the human-readable message, because the
        # router surfaces str(e) verbatim to the end user in its re-prompt.
        log.info("lead validation failed (intent=%s): %s", intent, "; ".join(errors))
        raise ValueError(humanize_lead_errors(errors))
    core = {k: fields.get(k) for k in ("name", "email", "phone", "organization")}
    extra = {k: v for k, v in fields.items()
             if k in REQUIRED_FIELDS.get(intent, []) and k not in core}
    row = {"session_id": session_id, "intent": intent, **core,
           "extra": extra, "message": fields.get("question", "")}
    # 1) store first — the lead must never be lost
    stored = get_supabase().table("leads").insert(row).execute().data[0]
    # 2) notify (best-effort; failures flagged, never raised). Live chat defers
    # this until the conversation ends, so the lead carries the transcript.
    if notify:
        notify_lead(stored)
    return stored
