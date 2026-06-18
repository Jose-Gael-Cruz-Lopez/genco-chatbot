from fastapi import APIRouter
from pydantic import BaseModel
from app import llm
from app.observability import trace_turn
from app.chat import memory, prompts
from app.rag.retrieve import retrieve

router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str


@router.post("/chat")
def chat(req: ChatRequest) -> dict:
    session_id = memory.get_or_create_session(req.session_id)
    memory.save_message(session_id, "user", req.message)
    hits = retrieve(req.message, k=5)
    scores = [h["similarity"] for h in hits]
    context = "\n\n".join(h["content"] for h in hits)
    history = memory.get_recent_messages(session_id, limit=10)
    msgs = prompts.build_messages(prompts.SYSTEM_PROMPT, context, history, req.message)
    from app.chat.tools import CAPTURE_LEAD_TOOL
    from app.escalation import capture_lead
    import json

    with trace_turn("chat", message=req.message, scores=scores) as span:
        result = llm.chat_completion(msgs, tools=[CAPTURE_LEAD_TOOL])
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
    memory.save_message(session_id, "assistant", reply)
    return {"session_id": session_id, "reply": reply, "retrieval_scores": scores}


@router.get("/history")
def history(session_id: str) -> dict:
    return {"session_id": session_id,
            "messages": memory.get_recent_messages(session_id, limit=100)}
