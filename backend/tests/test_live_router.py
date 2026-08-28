from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.live import chats

SID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client():
    import app.main as main_mod
    return TestClient(main_mod.app)


def test_no_chat_reports_ended(client):
    with patch("app.live.router.chats.current_chat", return_value=None):
        body = client.get(f"/live/messages?session_id={SID}").json()
    assert body["ended"] is True
    assert body["messages"] == []


def test_active_chat_returns_agent_messages(client):
    chat = {"id": "c1", "session_id": SID, "status": chats.ACTIVE}
    msgs = [{"role": "agent", "content": "hello", "created_at": "2026-08-27T12:00:00+00:00"}]
    with patch("app.live.router.chats.current_chat", return_value=chat), \
         patch("app.live.router.chats.touch_visitor"), \
         patch("app.live.router.chats.sweep", return_value=chat), \
         patch("app.live.router.chats.agent_messages_since", return_value=msgs):
        body = client.get(f"/live/messages?session_id={SID}").json()
    assert body["ended"] is False
    assert body["messages"] == msgs


def test_a_swept_end_clears_the_flow_state_and_explains_why(client):
    chat = {"id": "c1", "session_id": SID, "status": chats.WAITING}
    ended = {**chat, "status": chats.ENDED, "ended_reason": chats.END_AGENT_DROPPED}
    with patch("app.live.router.chats.current_chat", return_value=chat), \
         patch("app.live.router.chats.touch_visitor"), \
         patch("app.live.router.chats.sweep", return_value=ended), \
         patch("app.live.router.chats.agent_messages_since", return_value=[]), \
         patch("app.live.router.memory.set_flow_state") as clear:
        body = client.get(f"/live/messages?session_id={SID}").json()
    assert body["ended"] is True
    assert body["reason"] == chats.END_AGENT_DROPPED
    assert "follow up" in body["notice"].lower()
    clear.assert_called_once_with(SID, None)


def test_polling_never_500s(client):
    with patch("app.live.router.chats.current_chat",
               side_effect=RuntimeError("supabase down")):
        r = client.get(f"/live/messages?session_id={SID}")
    assert r.status_code == 200
    assert r.json()["error"] is True
