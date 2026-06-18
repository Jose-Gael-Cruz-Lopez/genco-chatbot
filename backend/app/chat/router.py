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
    with trace_turn("chat", message=req.message, scores=scores) as span:
        result = llm.chat_completion(msgs)
        reply = result["content"] or ""
        span.update(model=result["model"], usage=result["usage"], reply=reply)
    memory.save_message(session_id, "assistant", reply)
    return {"session_id": session_id, "reply": reply, "retrieval_scores": scores}


@router.get("/history")
def history(session_id: str) -> dict:
    return {"session_id": session_id,
            "messages": memory.get_recent_messages(session_id, limit=100)}
