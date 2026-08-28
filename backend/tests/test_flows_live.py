from unittest.mock import patch

import pytest

from app.chat import flows

SID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def live_env():
    with patch("app.chat.flows.is_agent_available", return_value=True) as avail, \
         patch("app.chat.flows.start_chat",
               return_value={"id": "chat-1"}) as start, \
         patch("app.chat.flows.capture_lead",
               return_value={"id": "lead-1"}) as cap, \
         patch("app.chat.flows.faq_misses.record_feedback"), \
         patch("app.chat.flows.best_match", return_value=(None, [])):
        yield {"available": avail, "start": start, "capture": cap}


def _fb_state(matched=True):
    return {"state": "awaiting_feedback", "question": "do you ship",
            "top_rank": 0.4, "matched": matched}


def test_live_is_offered_when_the_team_is_online(live_env):
    reply, qr, state, _ = flows.handle_turn(SID, flows.FEEDBACK_NO, _fb_state())
    assert state["state"] == "awaiting_live_consent"
    assert qr == [flows.CHAT_NOW, flows.JUST_EMAIL]
    assert "online" in reply.lower()


def test_live_is_not_offered_when_the_team_is_offline(live_env):
    live_env["available"].return_value = False
    reply, _, state, _ = flows.handle_turn(SID, flows.FEEDBACK_NO, _fb_state())
    # unchanged behaviour: straight into today's email flow
    assert state["state"] == "lead"
    assert state["intent"] == "question"
    assert "name" in reply.lower()


def test_choosing_email_falls_through_to_the_existing_flow(live_env):
    consent = {"state": "awaiting_live_consent", "question": "do you ship"}
    reply, _, state, _ = flows.handle_turn(SID, flows.JUST_EMAIL, consent)
    assert state["state"] == "lead"
    assert state["fields"]["question"] == "do you ship"
    assert "name" in reply.lower()


def test_choosing_chat_asks_for_an_email_first(live_env):
    consent = {"state": "awaiting_live_consent", "question": "do you ship"}
    reply, _, state, _ = flows.handle_turn(SID, flows.CHAT_NOW, consent)
    assert state["state"] == "live_collect_email"
    assert "email" in reply.lower()


def test_a_bad_email_re_prompts_without_connecting(live_env):
    collecting = {"state": "live_collect_email", "question": "q"}
    reply, _, state, _ = flows.handle_turn(SID, "nope", collecting)
    assert state["state"] == "live_collect_email"
    assert "email" in reply.lower()
    live_env["start"].assert_not_called()


def test_a_good_email_stores_the_lead_unnotified_and_connects(live_env):
    collecting = {"state": "live_collect_email", "question": "do you ship"}
    reply, qr, state, _ = flows.handle_turn(SID, "ada@example.com", collecting)
    assert state["state"] == "live"
    assert state["chat_id"] == "chat-1"
    assert "connecting" in reply.lower()
    assert qr == []
    # stored but NOT notified — the email goes out when the chat ends
    assert live_env["capture"].call_args.kwargs["notify"] is False
    live_env["start"].assert_called_once()


def test_a_failed_lead_store_never_connects(live_env):
    live_env["capture"].side_effect = RuntimeError("supabase down")
    collecting = {"state": "live_collect_email", "question": "q"}
    reply, _, state, _ = flows.handle_turn(SID, "ada@example.com", collecting)
    live_env["start"].assert_not_called()
    assert state is None or state.get("state") != "live"
    assert "Info@GenerationConscious.co" in reply


def test_messages_during_a_live_chat_are_not_matched_against_the_faq(live_env):
    live = {"state": "live", "chat_id": "chat-1", "question": "q"}
    with patch("app.chat.flows.best_match") as match:
        reply, qr, state, scores = flows.handle_turn(SID, "what about bulk?", live)
    match.assert_not_called()
    assert reply == ""
    assert qr == []
    assert scores == []
    assert state == live


def test_cancel_still_exits_a_live_chat(live_env):
    live = {"state": "live", "chat_id": "chat-1", "question": "q"}
    _, _, state, _ = flows.handle_turn(SID, "cancel", live)
    assert state is None
