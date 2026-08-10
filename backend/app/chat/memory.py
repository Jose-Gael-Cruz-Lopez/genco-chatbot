import uuid

from app.db import get_supabase


def _is_uuid(value: str | None) -> bool:
    # chat_sessions.id / chat_messages.session_id are Postgres uuid columns; filtering them
    # with a non-UUID string makes PostgREST raise APIError 22P02, so validate first.
    try:
        uuid.UUID(str(value))
        return True
    except ValueError:
        return False


def get_or_create_session(session_id: str | None) -> str:
    sb = get_supabase()
    if session_id and _is_uuid(session_id):
        existing = sb.table("chat_sessions").select("id").eq("id", session_id).execute()
        if existing.data:
            return session_id
    # Absent, non-UUID, or unknown id — mint a fresh session (the function's contract).
    created = sb.table("chat_sessions").insert({}).execute()
    return created.data[0]["id"]


def save_message(session_id: str, role: str, content: str) -> None:
    get_supabase().table("chat_messages").insert(
        {"session_id": session_id, "role": role, "content": content}).execute()


def get_recent_messages(session_id: str, limit: int = 10) -> list[dict]:
    # A non-UUID id (corrupted localStorage, hand-typed curl) can never match a session row,
    # and filtering the uuid column with it raises 22P02 — treat it as empty history.
    # Fetch the most recent `limit` messages (descending), then reverse to chronological order.
    # Ascending + limit would return the OLDEST N, so a long conversation would keep feeding the
    # model its opening and never the recent context.
    resp = (get_supabase().table("chat_messages")
            .select("role,content,created_at")
            .eq("session_id", session_id)
            .order("created_at", desc=True).limit(limit).execute())
    return list(reversed(resp.data or []))
