from app.db import get_supabase


def get_or_create_session(session_id: str | None) -> str:
    sb = get_supabase()
    if session_id:
        existing = sb.table("chat_sessions").select("id").eq("id", session_id).execute()
        if existing.data:
            return session_id
    created = sb.table("chat_sessions").insert({}).execute()
    return created.data[0]["id"]


def save_message(session_id: str, role: str, content: str) -> None:
    get_supabase().table("chat_messages").insert(
        {"session_id": session_id, "role": role, "content": content}).execute()


def get_recent_messages(session_id: str, limit: int = 10) -> list[dict]:
    resp = (get_supabase().table("chat_messages")
            .select("role,content,created_at")
            .eq("session_id", session_id)
            .order("created_at").limit(limit).execute())
    return resp.data or []
