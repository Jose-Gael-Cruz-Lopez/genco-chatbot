"""The deterministic FAQ-mode conversation state machine.

No model is ever called from this module: matched answers are verbatim KB chunks
and every other reply is a fixed string defined here. That is what makes the
sales claim honest — the bot only shows text the GC team wrote.

Flow states (persisted as chat_sessions.flow_state; None means idle):
  {"state": "awaiting_feedback", "question": str, "top_rank": float,
   "matched": bool}
  {"state": "lead", "intent": str, "fields": dict}
"""
import logging
import re

from app import faq_misses
from app.chat.tools import FIELD_LABELS, REQUIRED_FIELDS
from app.escalation import capture_lead
from app.rag.fts import best_match

log = logging.getLogger(__name__)

# ── Button labels (the widget sends a tap as a normal user message) ────────
FEEDBACK_YES = "\U0001F44D Yes, that answered it"
FEEDBACK_NO = "✉️ No — ask the team"
SEND_TO_TEAM = "✉️ Send my question to the team"
WHOLESALE_START = "Start wholesale inquiry"
BUY_SHEETS = "Buy Sheets"
BUY_REFILL = "Buy Refill Stations"
ASK_TEAM = "Question for the team"

PRODUCT_URL = "https://generationconscious.co/product/laundry-detergent-sheets/"
CONTACT = "Info@GenerationConscious.co or text (516) 619-6174"

# KB source file -> extra quick-reply buttons offered with its answers.
# Single source of truth for answer-driven flow triggers.
_SOURCE_TRIGGERS: dict[str, list[str]] = {
    "wholesale.md": [WHOLESALE_START],
}

_BUY_SHEETS_REPLY = (
    "Great — you can choose your sheet count, scent, and one-time or "
    f"subscription here: {PRODUCT_URL}"
)
_NO_MATCH_REPLY = (
    "I couldn't find that in our FAQ — but our team can answer it personally."
)
_THANKS_REPLY = "Glad that helped! Anything else I can look up for you?"
# Prefixed to the first field prompt when the visitor is being handed to a human,
# so the escalation reads as "a person is taking this" rather than as a bare form
# question appearing out of nowhere (spec motivation, point 2).
_HANDOFF_LEAD_IN = "No problem — I'll pass this to our team so they can answer you personally."
_CANCEL_REPLY = "No problem — that's cancelled. What else can I help you with?"
_LEAD_DONE = {
    "question": ("Thanks — I've sent your question to our team. They usually "
                 "reply the same day."),
    "wholesale": ("Thanks — I've passed your wholesale inquiry to our team. "
                  "They respond within 24 hours (usually ~15 minutes)."),
    "refill_station": ("Thanks — I've passed your refill-station details to our "
                       "team. They respond within 24 hours (usually ~15 minutes)."),
}
_LEAD_FALLBACK = (
    "I wasn't able to submit your details just now, but our team still wants to "
    f"hear from you — please email {CONTACT} and they'll take care of you."
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INT_FIELDS = ("estimated_sheets", "num_laundry_rooms", "num_students")
_NORM_RE = re.compile(r"[^a-z0-9 ]+")

# Field prompts are built from FIELD_LABELS (the single source of truth); only
# labels that don't read well in the generic template are overridden here.
_FIELD_PROMPTS = {
    "question": "Sure — what's your question for the team?",
}

# Fields asked before the rest of their intent's list. REQUIRED_FIELDS remains the
# source of truth for WHICH fields are needed; this only changes the order they're
# asked in, so someone who picked "Question for the team" gets to say what they
# came to ask before being asked for contact details.
_ASK_FIRST: dict[str, str] = {
    "question": "question",
}


def _norm(text: str) -> str:
    """Compare button taps by their words alone.

    Emoji, variation selectors, and dash characters differ between platforms and
    between the widget and a hand-typed reply; matching on letters and digits
    keeps a tap recognisable either way.
    """
    return " ".join(_NORM_RE.sub(" ", (text or "").lower()).split())


def _greeting_buttons() -> list[str]:
    return [BUY_SHEETS, BUY_REFILL, ASK_TEAM]


def _ask_order(intent: str) -> list[str]:
    fields = list(REQUIRED_FIELDS[intent])
    first = _ASK_FIRST.get(intent)
    if first in fields:
        fields.remove(first)
        fields.insert(0, first)
    return fields


def _missing_fields(intent: str, fields: dict) -> list[str]:
    return [f for f in _ask_order(intent) if not fields.get(f)]


def _prompt_for(field: str) -> str:
    if field in _FIELD_PROMPTS:
        return _FIELD_PROMPTS[field]
    return f"What's {FIELD_LABELS.get(field, field.replace('_', ' '))}?"


def _validate_field(field: str, value: str) -> tuple[bool, str]:
    """(ok, re-prompt). Mirrors tools.validate_lead's rules, one field at a time."""
    value = value.strip()
    if not value:
        return False, f"I didn't catch that. {_prompt_for(field)}"
    if field == "email" and not _EMAIL_RE.match(value):
        return False, "That email doesn't look right — could you type it again?"
    if field in _INT_FIELDS and not re.fullmatch(r"\d[\d,]*", value.replace(" ", "")):
        return False, (f"Please give {FIELD_LABELS[field]} as a number "
                       "(digits only, for example 500).")
    return True, ""


def handle_turn(session_id: str, message: str,
                state: dict | None) -> tuple[str, list[str], dict | None, list[float]]:
    """Run one FAQ-mode turn.

    Returns (reply, quick_replies, next_flow_state, retrieval_scores). An unknown
    or corrupt state resets to idle rather than trapping the visitor.
    """
    text = message.strip()
    norm = _norm(text)
    if norm == "cancel":
        return _CANCEL_REPLY, _greeting_buttons(), None, []

    name = (state or {}).get("state")
    if name == "awaiting_feedback":
        return _handle_feedback(session_id, state or {}, text, norm)
    if name == "lead":
        return _handle_lead_step(session_id, state or {}, text)
    if name is not None:
        log.warning("Unknown flow state %r — resetting to idle.", name)
    return _handle_idle(session_id, text, norm)


def _handle_idle(session_id: str, text: str,
                 norm: str) -> tuple[str, list[str], dict | None, list[float]]:
    if norm == _norm(BUY_SHEETS):
        return _BUY_SHEETS_REPLY, [], None, []
    for label, intent in ((BUY_REFILL, "refill_station"),
                          (ASK_TEAM, "question"),
                          (WHOLESALE_START, "wholesale"),
                          (SEND_TO_TEAM, "question")):
        if norm == _norm(label):
            return _start_lead(session_id, intent, {})

    hit, scores = best_match(text)
    top = scores[0] if scores else 0.0
    if hit is None:
        # Every unanswerable question is a FAQ gap worth recording, even if the
        # visitor abandons before leaving contact details.
        faq_misses.record_feedback(text, top, answered=False)
        return (_NO_MATCH_REPLY, [SEND_TO_TEAM],
                {"state": "awaiting_feedback", "question": text,
                 "top_rank": top, "matched": False}, scores)
    source = (hit.get("metadata") or {}).get("source") or ""
    buttons = [FEEDBACK_YES, FEEDBACK_NO] + _SOURCE_TRIGGERS.get(source, [])
    return (hit["content"], buttons,
            {"state": "awaiting_feedback", "question": text,
             "top_rank": top, "matched": True}, scores)


def _handle_feedback(session_id: str, state: dict, text: str,
                     norm: str) -> tuple[str, list[str], dict | None, list[float]]:
    question = state.get("question") or ""
    top = float(state.get("top_rank") or 0.0)
    matched = bool(state.get("matched"))

    if matched and norm == _norm(FEEDBACK_YES):
        faq_misses.record_feedback(question, top, answered=True)
        return _THANKS_REPLY, [], None, []
    if norm in (_norm(FEEDBACK_NO), _norm(SEND_TO_TEAM)):
        # A no-match was already recorded when it happened; don't double-count.
        if matched:
            faq_misses.record_feedback(question, top, answered=False)
        prefill = {"question": question} if question else {}
        return _start_lead(session_id, "question", prefill, lead_in=_HANDOFF_LEAD_IN)
    if norm == _norm(WHOLESALE_START):
        return _start_lead(session_id, "wholesale", {})
    # Only explicit Yes/No taps are recorded as feedback; anything else typed is
    # simply a new question.
    return _handle_idle(session_id, text, norm)


def _start_lead(session_id: str, intent: str, fields: dict,
                lead_in: str = "") -> tuple[str, list[str], dict | None, list[float]]:
    """Enter a guided lead flow. `lead_in` prefixes the first prompt only — used
    to acknowledge a human handoff before asking for contact details."""
    reply, quick, state, scores = _ask_next(
        session_id, {"state": "lead", "intent": intent, "fields": dict(fields)})
    if lead_in:
        reply = f"{lead_in} {reply}"
    return reply, quick, state, scores


def _ask_next(session_id: str,
              state: dict) -> tuple[str, list[str], dict | None, list[float]]:
    """Prompt for the next missing field, or submit once all are collected.

    The next field is derived from REQUIRED_FIELDS rather than stored, so a
    partially-written state can never point at the wrong field.
    """
    missing = _missing_fields(state["intent"], state["fields"])
    if missing:
        return _prompt_for(missing[0]), [], state, []
    return _submit(session_id, state)


def _submit(session_id: str,
            state: dict) -> tuple[str, list[str], dict | None, list[float]]:
    intent = state["intent"]
    payload = dict(state["fields"])
    for field in _INT_FIELDS:
        if field in payload:
            payload[field] = int(str(payload[field]).replace(",", "").strip())
    try:
        capture_lead(session_id, intent, payload)
    except Exception:
        # A fully-collected lead exists at this exact moment: log the payload so
        # it stays recoverable, then offer the human path. Never lose a lead.
        log.exception("capture_lead failed in FAQ flow (lead recoverable): "
                      "intent=%s fields=%s", intent, payload)
        return _LEAD_FALLBACK, [], None, []
    return _LEAD_DONE[intent], [], None, []


def _handle_lead_step(session_id: str, state: dict,
                      text: str) -> tuple[str, list[str], dict | None, list[float]]:
    intent = state.get("intent")
    if intent not in REQUIRED_FIELDS:
        log.warning("Lead flow with unknown intent %r — resetting to idle.", intent)
        return _handle_idle(session_id, text, _norm(text))
    fields = dict(state.get("fields") or {})
    missing = _missing_fields(intent, fields)
    if not missing:
        return _submit(session_id,
                       {"state": "lead", "intent": intent, "fields": fields})
    field = missing[0]
    ok, reprompt = _validate_field(field, text)
    if not ok:
        return reprompt, [], state, []
    fields[field] = text.strip()
    return _ask_next(session_id,
                     {"state": "lead", "intent": intent, "fields": fields})
