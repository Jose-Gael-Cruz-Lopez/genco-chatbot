from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.live import chats

SID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def client(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_PASSWORD", "hunter2")
    monkeypatch.setenv("AGENT_SESSION_SECRET", "test-signing-secret")
    import importlib
    import app.agent.router as ar
    import app.main as main_mod
    importlib.reload(ar)
    importlib.reload(main_mod)
    c = TestClient(main_mod.app)
    c.post("/agent/login", json={"password": "hunter2"})
    yield c
    get_settings.cache_clear()


@pytest.mark.parametrize("method,path", [
    ("post", "/agent/heartbeat"),
    ("get", "/agent/queue"),
    ("get", f"/agent/chat/{SID}"),
    ("post", f"/agent/chat/{SID}/message"),
    ("post", f"/agent/chat/{SID}/end"),
])
def test_every_live_route_requires_a_session(method, path):
    get_settings.cache_clear()
    import importlib
    import app.main as main_mod
    importlib.reload(main_mod)
    anon = TestClient(main_mod.app)
    # An empty body on the POSTs: the gate must answer before body validation
    # does, so an anonymous caller never learns the route's shape from a 422.
    kwargs = {"json": {}} if method == "post" else {}
    r = getattr(anon, method)(path, **kwargs)
    assert r.status_code in (401, 503)


def test_heartbeat_records_availability(client):
    with patch("app.agent.router.presence.heartbeat") as hb:
        r = client.post("/agent/heartbeat", json={"available": True})
    assert r.status_code == 200
    hb.assert_called_once_with(True)


def test_queue_sweeps_before_listing(client):
    chat = {"id": "c1", "session_id": SID, "status": chats.WAITING,
            "question": "do you ship"}
    with patch("app.agent.router.chats.list_open", return_value=[chat]), \
         patch("app.agent.router.chats.sweep", return_value=chat) as sweep:
        body = client.get("/agent/queue").json()
    sweep.assert_called_once()
    assert body["chats"][0]["session_id"] == SID


def test_queue_hides_chats_that_the_sweep_ended(client):
    chat = {"id": "c1", "session_id": SID, "status": chats.WAITING}
    ended = {**chat, "status": chats.ENDED}
    with patch("app.agent.router.chats.list_open", return_value=[chat]), \
         patch("app.agent.router.chats.sweep", return_value=ended):
        assert client.get("/agent/queue").json()["chats"] == []


def test_opening_a_chat_accepts_it_and_returns_the_transcript(client):
    msgs = [{"role": "user", "content": "hi", "created_at": "t"}]
    with patch("app.agent.router.chats.accept",
               return_value={"id": "c1", "status": chats.ACTIVE}) as acc, \
         patch("app.agent.router.memory.get_recent_messages", return_value=msgs):
        body = client.get(f"/agent/chat/{SID}").json()
    acc.assert_called_once_with(SID)
    assert body["messages"] == msgs


def test_sending_a_message_stores_it_as_an_agent_turn(client):
    with patch("app.agent.router.chats.add_agent_message") as add:
        r = client.post(f"/agent/chat/{SID}/message", json={"text": "hello"})
    assert r.status_code == 200
    add.assert_called_once_with(SID, "hello")


def test_an_empty_message_is_rejected(client):
    with patch("app.agent.router.chats.add_agent_message") as add:
        r = client.post(f"/agent/chat/{SID}/message", json={"text": "   "})
    assert r.status_code == 400
    add.assert_not_called()


def test_ending_a_chat_uses_the_agent_ended_reason(client):
    chat = {"id": "c1", "session_id": SID, "status": chats.ACTIVE}
    with patch("app.agent.router.chats.current_chat", return_value=chat), \
         patch("app.agent.router.chats.end_chat") as end:
        r = client.post(f"/agent/chat/{SID}/end")
    assert r.status_code == 200
    end.assert_called_once_with(chat, chats.END_AGENT_ENDED)
