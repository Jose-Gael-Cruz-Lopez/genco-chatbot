import json
import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel
from app import llm
from app.config import get_settings
from app import guardrails
from app import injection_scanner
from app.observability import trace_turn
from app.chat import memory, prompts
from app.chat.tools import CAPTURE_LEAD_TOOL
from app.escalation import capture_lead, should_escalate
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


def _client_ip(request: Request) -> str:
    # On Render (and most proxies) the real client is the first hop in X-Forwarded-For.
    # Caveat: the leftmost hop is client-spoofable if the proxy appends rather than replaces, so a
    # determined abuser can still rotate the per-minute limit — the daily cost cap is the real
    # backstop. Good enough to stop casual hammering.
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


@router.post("/chat")
def chat(req: ChatRequest, request: Request) -> dict:
    session_id = memory.get_or_create_session(req.session_id)
    # Rate-limit by client IP, not the browser-supplied session_id (which a client can rotate/omit
    # to mint a fresh bucket every request).
    if not _rate_limiter.allow(_client_ip(request)):
        return {"session_id": session_id,
                "reply": "You're sending messages quickly — give me a moment and try again.",
                "retrieval_scores": []}
    if _cost.exceeded():
        logger.warning("Daily cost cap exceeded; blocking request and returning fallback.")
        return {"session_id": session_id,
                "reply": "I'm momentarily unavailable. Please email Info@GenerationConscious.co and the team will help.",
                "retrieval_scores": []}
    # Substring guard (always on, cheap) + optional ML scanner (LLM Guard) when installed.
    if guardrails.is_injection_attempt(req.message) or injection_scanner.is_injection(req.message):
        return {"session_id": session_id,
                "reply": "I can only help with Generation Conscious products and orders. How can I help with that?",
                "retrieval_scores": []}
    history = memory.get_recent_messages(session_id, limit=10)
    memory.save_message(session_id, "user", req.message)
    hits = retrieve(req.message, k=5)
    scores = [h["similarity"] for h in hits]
    context = "\n\n".join(h["content"] for h in hits)
    msgs = prompts.build_messages(prompts.SYSTEM_PROMPT, context, history, req.message)

    with trace_turn("chat", message=req.message, scores=scores) as span:
        try:
            result = llm.chat_completion(msgs, tools=[CAPTURE_LEAD_TOOL])
        except Exception:
            logger.warning("Primary model failed; retrying with fallback model.")
            result = llm.chat_completion(msgs, tools=[CAPTURE_LEAD_TOOL], use_fallback=True)
        reply = result["content"] or ""
        tool_calls = result.get("tool_calls") or []
        for call in tool_calls:
            if call["function"]["name"] == "capture_lead":
                args = json.loads(call["function"]["arguments"])
                intent = args.pop("intent")
                try:
                    capture_lead(session_id, intent, args)
                    reply = ("Thanks — I've passed this to our team. They respond within 24 hours "
                             "(usually ~15 minutes).")
                except ValueError as e:
                    reply = f"I still need a bit more info before I can submit this: {e}"
        # Server-side grounding safety net: if the model isn't capturing a lead and retrieval is too
        # weak to ground an answer (top similarity below threshold, or a high-risk keyword), route to
        # the team rather than risk an ungrounded reply — regardless of what the model produced.
        if not tool_calls and should_escalate(scores, text=req.message):
            reply = _ESCALATION_REPLY
        span.update(model=result["model"], usage=result["usage"], reply=reply)
        _cost.record(result["usage"], result["model"])
    memory.save_message(session_id, "assistant", reply)
    return {"session_id": session_id, "reply": reply, "retrieval_scores": scores}


@router.get("/history")
def history(session_id: str) -> dict:
    return {"session_id": session_id,
            "messages": memory.get_recent_messages(session_id, limit=100)}
