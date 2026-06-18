import json
import logging

from fastapi import APIRouter
from pydantic import BaseModel
from app import llm
from app.config import get_settings
from app import guardrails
from app.observability import trace_turn
from app.chat import memory, prompts
from app.chat.tools import CAPTURE_LEAD_TOOL
from app.escalation import capture_lead
from app.rag.retrieve import retrieve

logger = logging.getLogger(__name__)

router = APIRouter()

_settings = get_settings()
_rate_limiter = guardrails.RateLimiter(_settings.RATE_LIMIT_PER_MINUTE)
_cost = guardrails.CostTracker(_settings.DAILY_COST_CAP_USD)


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


@router.post("/chat")
def chat(req: ChatRequest) -> dict:
    session_id = memory.get_or_create_session(req.session_id)
    if not _rate_limiter.allow(session_id):
        return {"session_id": session_id,
                "reply": "You're sending messages quickly — give me a moment and try again.",
                "retrieval_scores": []}
    if _cost.exceeded():
        logger.warning("Daily cost cap exceeded; blocking request and returning fallback.")
        return {"session_id": session_id,
                "reply": "I'm momentarily unavailable. Please email Info@GenerationConscious.co and the team will help.",
                "retrieval_scores": []}
    if guardrails.is_injection_attempt(req.message):
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
        span.update(model=result["model"], usage=result["usage"], reply=reply)
        _cost.record(result["usage"], result["model"])
    memory.save_message(session_id, "assistant", reply)
    return {"session_id": session_id, "reply": reply, "retrieval_scores": scores}


@router.get("/history")
def history(session_id: str) -> dict:
    return {"session_id": session_id,
            "messages": memory.get_recent_messages(session_id, limit=100)}
