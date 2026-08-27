from unittest.mock import patch

import pytest

from app.chat import flows

SID = "11111111-1111-1111-1111-111111111111"


def _hit(content="Shipping is live USPS at checkout.", source="shipping_and_tax.md",
         rank=0.5):
    return {"content": content, "metadata": {"source": source}, "similarity": rank}


@pytest.fixture
def no_db():
    """Neutralise every external call; individual tests re-patch what they assert on."""
    with patch("app.chat.flows.best_match", return_value=(None, [])) as bm, \
         patch("app.chat.flows.faq_misses.record_feedback") as rec, \
         patch("app.chat.flows.capture_lead", return_value={"id": "lead-1"}) as cap:
        yield {"best_match": bm, "record": rec, "capture": cap}


# ── idle: matching ────────────────────────────────────────────────────────
def test_good_match_answers_verbatim_with_feedback_buttons(no_db):
    content = "Shipping is calculated at checkout using live USPS rates."
    no_db["best_match"].return_value = (_hit(content), [0.5])
    reply, qr, state, scores = flows.handle_turn(SID, "do you ship?", None)
    assert reply == content
    assert qr == [flows.FEEDBACK_YES, flows.FEEDBACK_NO]
    assert state == {"state": "awaiting_feedback", "question": "do you ship?",
                     "top_rank": 0.5, "matched": True}
    assert scores == [0.5]


def test_wholesale_source_adds_the_wholesale_trigger_button(no_db):
    no_db["best_match"].return_value = (_hit(source="wholesale.md"), [0.5])
    _, qr, _, _ = flows.handle_turn(SID, "bulk pricing?", None)
    assert qr == [flows.FEEDBACK_YES, flows.FEEDBACK_NO, flows.WHOLESALE_START]


def test_no_match_offers_to_send_the_question_and_records_the_gap(no_db):
    no_db["best_match"].return_value = (None, [0.001])
    reply, qr, state, _ = flows.handle_turn(SID, "do you sell dog food", None)
    assert "couldn't find that" in reply
    assert qr == [flows.SEND_TO_TEAM]
    assert state["matched"] is False
    assert state["question"] == "do you sell dog food"
    no_db["record"].assert_called_once_with("do you sell dog food", 0.001,
                                            answered=False)


# ── feedback ──────────────────────────────────────────────────────────────
def test_yes_thanks_resets_to_idle_and_records_a_hit(no_db):
    state = {"state": "awaiting_feedback", "question": "q", "top_rank": 0.5,
             "matched": True}
    reply, qr, new_state, _ = flows.handle_turn(SID, flows.FEEDBACK_YES, state)
    assert "Glad that helped" in reply
    assert qr == []
    assert new_state is None
    no_db["record"].assert_called_once_with("q", 0.5, answered=True)


def test_no_starts_the_question_lead_flow_with_the_question_prefilled(no_db):
    state = {"state": "awaiting_feedback", "question": "how do refills work",
             "top_rank": 0.5, "matched": True}
    reply, _, new_state, _ = flows.handle_turn(SID, flows.FEEDBACK_NO, state)
    assert new_state["state"] == "lead"
    assert new_state["intent"] == "question"
    assert new_state["fields"]["question"] == "how do refills work"
    # question is prefilled, so the first prompt is for the name
    assert "name" in reply.lower()
    no_db["record"].assert_called_once_with("how do refills work", 0.5,
                                            answered=False)


def test_send_to_team_from_a_no_match_does_not_double_record(no_db):
    state = {"state": "awaiting_feedback", "question": "dog food", "top_rank": 0.0,
             "matched": False}
    _, _, new_state, _ = flows.handle_turn(SID, flows.SEND_TO_TEAM, state)
    assert new_state["fields"]["question"] == "dog food"
    no_db["record"].assert_not_called()


def test_other_text_during_feedback_is_treated_as_a_new_question(no_db):
    no_db["best_match"].return_value = (_hit("Refill answer."), [0.6])
    state = {"state": "awaiting_feedback", "question": "old", "top_rank": 0.5,
             "matched": True}
    reply, qr, new_state, _ = flows.handle_turn(SID, "how do refills work", state)
    assert reply == "Refill answer."
    assert new_state["question"] == "how do refills work"
    # no feedback row for text that was not an explicit Yes/No tap
    assert not any(c.kwargs.get("answered") is True
                   for c in no_db["record"].call_args_list)


# ── greeting buttons ──────────────────────────────────────────────────────
def test_buy_sheets_returns_the_product_url(no_db):
    reply, qr, state, _ = flows.handle_turn(SID, flows.BUY_SHEETS, None)
    assert "https://generationconscious.co/product/laundry-detergent-sheets/" in reply
    assert state is None


def test_buy_refill_stations_starts_the_refill_lead_flow(no_db):
    reply, _, state, _ = flows.handle_turn(SID, flows.BUY_REFILL, None)
    assert state == {"state": "lead", "intent": "refill_station", "fields": {}}
    assert "name" in reply.lower()


def test_question_for_the_team_asks_for_the_question_first(no_db):
    reply, _, state, _ = flows.handle_turn(SID, flows.ASK_TEAM, None)
    assert state["intent"] == "question"
    assert "question" in reply.lower()


# ── guided lead flow ──────────────────────────────────────────────────────
def test_lead_flow_collects_one_field_at_a_time_in_order(no_db):
    state = {"state": "lead", "intent": "wholesale", "fields": {}}
    reply, _, state, _ = flows.handle_turn(SID, "Ada Lovelace", state)
    assert state["fields"] == {"name": "Ada Lovelace"}
    assert "email" in reply.lower()
    reply, _, state, _ = flows.handle_turn(SID, "ada@example.com", state)
    assert state["fields"]["email"] == "ada@example.com"
    assert "phone" in reply.lower()


def test_invalid_email_re_prompts_and_stays_on_the_field(no_db):
    state = {"state": "lead", "intent": "wholesale", "fields": {"name": "Ada"}}
    reply, _, new_state, _ = flows.handle_turn(SID, "not-an-email", state)
    assert "email" in reply.lower()
    assert "email" not in new_state["fields"]


def test_non_numeric_count_re_prompts(no_db):
    state = {"state": "lead", "intent": "wholesale",
             "fields": {"name": "A", "email": "a@b.co", "phone": "555",
                        "organization": "Org"}}
    reply, _, new_state, _ = flows.handle_turn(SID, "lots of them", state)
    assert "number" in reply.lower()
    assert "estimated_sheets" not in new_state["fields"]


def test_completed_lead_is_captured_and_resets_to_idle(no_db):
    state = {"state": "lead", "intent": "wholesale",
             "fields": {"name": "A", "email": "a@b.co", "phone": "555",
                        "organization": "Org"}}
    reply, qr, new_state, _ = flows.handle_turn(SID, "500", state)
    no_db["capture"].assert_called_once()
    args = no_db["capture"].call_args[0]
    assert args[0] == SID and args[1] == "wholesale"
    assert args[2]["estimated_sheets"] == 500     # coerced to int
    assert new_state is None
    assert "Thanks" in reply


def test_capture_failure_offers_the_human_path_and_resets(no_db):
    no_db["capture"].side_effect = RuntimeError("supabase down")
    state = {"state": "lead", "intent": "question",
             "fields": {"question": "q", "name": "A"}}
    reply, _, new_state, _ = flows.handle_turn(SID, "a@b.co", state)
    assert "Info@GenerationConscious.co" in reply
    assert new_state is None


# ── cancel and corrupt state ──────────────────────────────────────────────
def test_cancel_exits_any_flow(no_db):
    state = {"state": "lead", "intent": "wholesale", "fields": {"name": "A"}}
    reply, qr, new_state, _ = flows.handle_turn(SID, "cancel", state)
    assert new_state is None
    assert qr == [flows.BUY_SHEETS, flows.BUY_REFILL, flows.ASK_TEAM]
    assert "cancel" in reply.lower()


def test_unknown_state_resets_to_idle_and_answers_the_message(no_db):
    no_db["best_match"].return_value = (_hit("An answer."), [0.6])
    reply, _, new_state, _ = flows.handle_turn(SID, "do you ship", {"state": "bogus"})
    assert reply == "An answer."
    assert new_state["state"] == "awaiting_feedback"


def test_lead_state_with_unknown_intent_resets_to_idle(no_db):
    no_db["best_match"].return_value = (_hit("An answer."), [0.6])
    reply, _, new_state, _ = flows.handle_turn(
        SID, "do you ship", {"state": "lead", "intent": "nope", "fields": {}})
    assert reply == "An answer."
    assert new_state["state"] == "awaiting_feedback"


def test_button_matching_ignores_emoji_and_punctuation(no_db):
    state = {"state": "awaiting_feedback", "question": "q", "top_rank": 0.5,
             "matched": True}
    # same words, no emoji / different dash
    reply, _, new_state, _ = flows.handle_turn(SID, "Yes, that answered it", state)
    assert new_state is None


def test_flows_module_makes_no_ai_calls():
    src = (__import__("pathlib").Path(flows.__file__)).read_text()
    for banned in ("app.llm", "embeddings", "openrouter", "openai"):
        assert banned not in src.lower()


# ── human-handoff acknowledgement ─────────────────────────────────────────
# Greg's ask (spec motivation, point 2) was the "please wait for a live customer
# agent" feel: the visitor should be told a person is taking over, not dropped
# straight into a bare field prompt.
def test_escalating_after_a_bad_answer_acknowledges_the_handoff(no_db):
    state = {"state": "awaiting_feedback", "question": "how do refills work",
             "top_rank": 0.5, "matched": True}
    reply, _, _, _ = flows.handle_turn(SID, flows.FEEDBACK_NO, state)
    assert "team" in reply.lower()
    assert "name" in reply.lower()          # still asks for the first field


def test_escalating_after_no_match_acknowledges_the_handoff(no_db):
    state = {"state": "awaiting_feedback", "question": "dog food",
             "top_rank": 0.0, "matched": False}
    reply, _, _, _ = flows.handle_turn(SID, flows.SEND_TO_TEAM, state)
    assert "team" in reply.lower()
    assert "name" in reply.lower()


def test_greeting_button_flows_have_no_handoff_lead_in(no_db):
    # "Buy Refill Stations" is a normal enquiry, not a human escalation.
    reply, _, _, _ = flows.handle_turn(SID, flows.BUY_REFILL, None)
    assert reply == "What's your name?"
