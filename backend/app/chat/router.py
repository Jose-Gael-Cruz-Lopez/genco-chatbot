import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel
from app import llm
from app.config import get_settings
from app import guardrails
from app import injection_scanner
from app.observability import trace_turn
from app.chat import flows, memory, prompts
from app.chat.tools import CAPTURE_LEAD_TOOL
from app.escalation import capture_lead, is_lead_flow_turn, should_escalate
from app.rag.retrieve import retrieve

logger = logging.getLogger(__name__)

router = APIRouter()

_settings = get_settings()
_rate_limiter = guardrails.RateLimiter(_settings.RATE_LIMIT_PER_MINUTE)
_cost = guardrails.CostTracker(_settings.DAILY_COST_CAP_USD)

# On-brand redirect used when retrieval is too weak to ground an answer (the grounding safety net).
# Worded so it also reads fine if it fires on a bare greeting.
_ESCALATION_REPLY = (
    "I want to make sure you get accurate information. I can help you buy sheets, set up refill "
    "stations for your community, or connect you with our team — email Info@GenerationConscious.co "
    "or text (516) 619-6174."
)

# Re-prompt when the model emits a capture_lead call the server can't parse (malformed JSON,
# missing intent) — ask again instead of crashing the turn.
_TOOL_REPROMPT_REPLY = (
    "Sorry — I didn't quite catch all of that. Could you share those details once more?"
)

# Offered when storing a fully-collected lead fails unexpectedly: the human path must always
# be available so the lead is never silently lost.
_LEAD_FALLBACK_REPLY = (
    "I wasn't able to submit your details just now, but our team still wants to hear from you — "
    "please email Info@GenerationConscious.co or text (516) 619-6174 and they'll take care of you."
)

# Returned when both the primary and fallback models fail (e.g. one OpenRouter outage takes
# out both) — never 500; keep the frozen
# {session_id, reply, retrieval_scores, quick_replies, live} contract.
_UNAVAILABLE_REPLY = (
    "I'm momentarily unavailable. Please email Info@GenerationConscious.co or text "
    "(516) 619-6174 and the team will help."
)

# FAQ mode never calls a model, so its only failure is the database. Same
# never-crash rule: log, offer the human path, keep the 200 contract.
_FAQ_FALLBACK_REPLY = (
    "I'm having trouble looking that up right now. Please email "
    "Info@GenerationConscious.co or text (516) 619-6174 and the team will help."
)


def _client_ip(request: Request) -> str:
    # Key on the RIGHTMOST X-Forwarded-For hop: Render (like most reverse proxies) appends the
    # real connecting IP as the last value, so the rightmost entry is proxy-set and non-spoofable,
    # while leftmost values arrive straight from the client and can be forged to rotate rate-limit
    # buckets. Behind additional trusted proxies the rightmost hop may be a proxy address — the
    # daily cost cap remains the global backstop either way.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "unknown"


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


def _faq_turn(req: ChatRequest) -> dict:
    """One zero-AI turn: full-text match + deterministic flows, no model call."""
    session_id = memory.get_or_create_session(req.session_id)
    memory.save_message(session_id, "user", req.message)
    next_state: dict | None = None
    try:
        state = memory.get_flow_state(session_id)
        reply, quick_replies, next_state, scores = flows.handle_turn(
            session_id, req.message, state)
        memory.set_flow_state(session_id, next_state)
    except Exception:
        logger.exception("FAQ turn failed; returning the contact fallback.")
        reply, quick_replies, scores = _FAQ_FALLBACK_REPLY, [], []
    # Mid-live-chat the bot stays silent: the visitor's message has been stored
    # for the agent to read, and their reply arrives over /live/messages.
    live = (next_state or {}).get("state") == "live"
    if reply:
        memory.save_message(session_id, "assistant", reply)
    return {"session_id": session_id, "reply": reply,
            "retrieval_scores": scores, "quick_replies": quick_replies,
            "live": live}


@router.post("/chat")
def chat(req: ChatRequest, request: Request) -> dict:
    # Guard gates run BEFORE any Supabase call: throttled/capped/declined requests must not
    # create chat_sessions rows (or cause any other DB load). They echo the client-supplied
    # session_id (or "") since no session is looked up or minted on these paths.
    echo_id = req.session_id or ""
    # Rate-limit by client IP, not the browser-supplied session_id (which a client can rotate/omit
    # to mint a fresh bucket every request). Applies in BOTH modes.
    if not _rate_limiter.allow(_client_ip(request)):
        return {"session_id": echo_id,
                "reply": "You're sending messages quickly — give me a moment and try again.",
                "retrieval_scores": [], "quick_replies": [], "live": False}
    # FAQ mode branches here: no model to protect from injection, no spend to cap.
    if _settings.BOT_MODE == "faq":
        return _faq_turn(req)
    if _cost.exceeded():
        logger.warning(
            "Daily cost cap exceeded; returning static unavailable message "
            "(fallback model NOT invoked).")
        return {"session_id": echo_id,
                "reply": "I'm momentarily unavailable. Please email Info@GenerationConscious.co and the team will help.",
                "retrieval_scores": [], "quick_replies": [], "live": False}
    # Substring guard (always on, cheap) + optional ML scanner (LLM Guard) when installed.
    if guardrails.is_injection_attempt(req.message) or injection_scanner.is_injection(req.message):
        return {"session_id": echo_id,
                "reply": "I can only help with Generation Conscious products and orders. How can I help with that?",
                "retrieval_scores": [], "quick_replies": [], "live": False}
    session_id = memory.get_or_create_session(req.session_id)
    history = memory.get_recent_messages(session_id, limit=10)
    memory.save_message(session_id, "user", req.message)

    # The trace opens BEFORE retrieval so it spans the full retrieve -> generate -> respond
    # pipeline (CLAUDE.md convention; VERIFICATION check 8).
    with trace_turn("chat", message=req.message) as span:
        with span.span("retrieve", query=req.message) as retrieval:
            hits = retrieve(req.message, k=5)
            scores = [h["similarity"] for h in hits]
            retrieval.set_output({"scores": scores})
        span.update(scores=scores)
        context = "\n\n".join(h["content"] for h in hits)
        msgs = prompts.build_messages(prompts.SYSTEM_PROMPT, context, history, req.message)
        try:
            with span.generation("generate", messages=msgs) as gen:
                result = llm.chat_completion(msgs, tools=[CAPTURE_LEAD_TOOL])
                gen.set_result(model=result["model"], usage=result["usage"],
                               output=result["content"])
        except Exception:
            logger.warning("Primary model failed; retrying with fallback model.")
            try:
                with span.generation("generate", messages=msgs) as gen:
                    result = llm.chat_completion(msgs, tools=[CAPTURE_LEAD_TOOL],
                                                 use_fallback=True)
                    gen.set_result(model=result["model"], usage=result["usage"],
                                   output=result["content"])
            except Exception:
                # Both models failed (both go through the same OpenRouter endpoint, so one
                # outage takes out both). Offer the human path and keep the 200 contract.
                logger.exception("Fallback model also failed; returning unavailable reply.")
                span.update(reply=_UNAVAILABLE_REPLY, error="llm_double_failure")
                span.event("respond", output=_UNAVAILABLE_REPLY)
                memory.save_message(session_id, "assistant", _UNAVAILABLE_REPLY)
                return {"session_id": session_id, "reply": _UNAVAILABLE_REPLY,
                        "retrieval_scores": scores, "quick_replies": [],
                        "live": False}
        reply = result["content"] or ""
        tool_calls = result.get("tool_calls") or []
        for call in tool_calls:
            # The whole per-call block is guarded: a malformed model emission or an unexpected
            # storage failure must never 500 the turn (leads must never be lost to a crash).
            try:
                fn = call.get("function") or {}
                if fn.get("name") != "capture_lead":
                    continue
                raw_args = fn.get("arguments") or ""
                args = json.loads(raw_args)
                intent = args.pop("intent")
            except Exception:
                # Malformed JSON / missing intent from the model — ask again, don't crash.
                logger.warning("Malformed capture_lead tool call; raw payload: %r", call)
                reply = _TOOL_REPROMPT_REPLY
                continue
            try:
                capture_lead(session_id, intent, args)
                reply = ("Thanks — I've passed this to our team. They respond within 24 hours "
                         "(usually ~15 minutes).")
            except ValueError as e:
                reply = f"I still need a bit more info before I can submit this: {e}"
            except Exception:
                # Unexpected failure (e.g. Supabase APIError on insert) at the exact moment a
                # fully-collected lead exists: log the raw payload so the lead fields are
                # recoverable from logs, then offer the human contact path.
                logger.exception(
                    "capture_lead failed; raw tool-call payload (lead recoverable): "
                    "intent=%s arguments=%s", intent, raw_args)
                reply = _LEAD_FALLBACK_REPLY
        # Server-side grounding safety net: if the model isn't capturing a lead and retrieval is too
        # weak to ground an answer (top similarity below threshold, or a high-risk keyword), route to
        # the team rather than risk an ungrounded reply — regardless of what the model produced.
        # Suppressed mid-lead-flow: field answers ("John Smith, john@acme.com", "500 sheets")
        # naturally score low against the KB and must not derail the capture flow.
        if not tool_calls and should_escalate(
                scores, text=req.message,
                lead_flow=is_lead_flow_turn(history, current_message=req.message)):
            reply = _ESCALATION_REPLY
            # Tag the trace so the team can filter KB gaps in LangFuse (README /
            # VERIFICATION check 8: escalations carry the `escalation` tag).
            span.tag("escalation")
        span.update(model=result["model"], usage=result["usage"], reply=reply)
        span.event("respond", output=reply)
        _cost.record(result["usage"], result["model"])
    memory.save_message(session_id, "assistant", reply)
    return {"session_id": session_id, "reply": reply, "retrieval_scores": scores,
            "quick_replies": [], "live": False}


@router.get("/history")
def history(session_id: str) -> dict:
    return {"session_id": session_id,
            "messages": memory.get_recent_messages(session_id, limit=100)}
