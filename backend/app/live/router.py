"""The visitor's half of live chat: one polling endpoint.

Called every couple of seconds by the widget while a chat is open. It must never
return anything but HTTP 200 with a well-formed body — a polling loop has no way
to recover from a 500, and the visitor is mid-conversation with a person.
"""
import logging

from fastapi import APIRouter

from app.chat import memory
from app.live import chats

logger = logging.getLogger(__name__)

router = APIRouter()

_NOTICE = {
    chats.END_AGENT_DROPPED: ("Looks like we got disconnected — our team has your "
                              "email and will follow up personally."),
    chats.END_NOT_ACCEPTED: ("Our team just stepped away — they have your email "
                             "and will follow up personally."),
    chats.END_VISITOR_LEFT: "That chat timed out. Anything else I can look up?",
    chats.END_AGENT_ENDED: ("Thanks for chatting! Anything else I can look up "
                            "for you?"),
}
_DEFAULT_NOTICE = "That chat has ended. Anything else I can look up for you?"


@router.get("/live/messages")
def live_messages(session_id: str, after: str | None = None) -> dict:
    try:
        chat = chats.current_chat(session_id)
        if chat is None:
            return {"messages": [], "ended": True, "reason": None,
                    "notice": None, "error": False}
        chats.touch_visitor(session_id)
        chat = chats.sweep(chat)
        messages = chats.agent_messages_since(session_id, after)
        if chat.get("status") == chats.ENDED:
            reason = chat.get("ended_reason")
            # Free the session so the next thing they type is answered normally.
            memory.set_flow_state(session_id, None)
            return {"messages": messages, "ended": True, "reason": reason,
                    "notice": _NOTICE.get(reason, _DEFAULT_NOTICE), "error": False}
        return {"messages": messages, "ended": False, "reason": None,
                "notice": None, "error": False}
    except Exception:
        # Signal the failure without ending the chat: a blip should not tear down
        # a live conversation, and the widget gives up after several in a row.
        logger.exception("live poll failed for session %s", session_id)
        return {"messages": [], "ended": False, "reason": None,
                "notice": None, "error": True}
