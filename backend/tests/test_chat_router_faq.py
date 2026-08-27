from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def faq_client(monkeypatch):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "faq")
    import importlib
    from app.chat import router as router_mod
    importlib.reload(router_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    with patch("app.chat.router.memory.get_or_create_session", return_value=SID), \
         patch("app.chat.router.memory.save_message"), \
         patch("app.chat.router.memory.get_flow_state", return_value=None), \
         patch("app.chat.router.memory.set_flow_state") as set_state:
        yield TestClient(main_mod.app), set_state
    get_settings.cache_clear()


def test_faq_turn_returns_the_frozen_contract(faq_client):
    client, _ = faq_client
    with patch("app.chat.router.flows.handle_turn",
               return_value=("An answer.", ["A", "B"], {"state": "x"}, [0.5])):
        r = client.post("/chat", json={"session_id": SID, "message": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"session_id", "reply", "retrieval_scores", "quick_replies"}
    assert body["reply"] == "An answer."
    assert body["quick_replies"] == ["A", "B"]
    assert body["retrieval_scores"] == [0.5]


def test_faq_turn_persists_the_next_flow_state(faq_client):
    client, set_state = faq_client
    with patch("app.chat.router.flows.handle_turn",
               return_value=("r", [], {"state": "lead"}, [])):
        client.post("/chat", json={"session_id": SID, "message": "hi"})
    set_state.assert_called_once_with(SID, {"state": "lead"})


def test_faq_mode_calls_no_model(faq_client):
    client, _ = faq_client
    with patch("app.chat.router.flows.handle_turn",
               return_value=("r", [], None, [])), \
         patch("app.llm.chat_completion") as llm_call:
        client.post("/chat", json={"session_id": SID, "message": "hi"})
    llm_call.assert_not_called()


def test_flow_failure_returns_contact_fallback_with_200(faq_client):
    client, _ = faq_client
    with patch("app.chat.router.flows.handle_turn",
               side_effect=RuntimeError("supabase down")):
        r = client.post("/chat", json={"session_id": SID, "message": "hi"})
    assert r.status_code == 200
    assert "Info@GenerationConscious.co" in r.json()["reply"]
    assert r.json()["quick_replies"] == []


def test_rate_limit_still_applies_in_faq_mode(faq_client):
    client, _ = faq_client
    with patch("app.chat.router._rate_limiter.allow", return_value=False):
        r = client.post("/chat", json={"session_id": SID, "message": "hi"})
    assert r.status_code == 200
    assert "quickly" in r.json()["reply"]
    assert r.json()["quick_replies"] == []
