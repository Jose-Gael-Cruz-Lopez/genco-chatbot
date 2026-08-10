import json
import logging
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.chat import router as chat_router
from app import guardrails

client = TestClient(app)


@patch("app.chat.router.llm.chat_completion", return_value={
    "content": "Go to the product page.", "tool_calls": None,
    "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "Buy sheets at the product page.",
     "metadata": {"title": "Buying"}, "similarity": 0.8}])
@patch("app.chat.router.memory")
def test_chat_returns_contract(mem, _ret, _llm):
    mem.get_or_create_session.return_value = "sess-1"
    mem.get_recent_messages.return_value = []
    resp = client.post("/chat", json={"message": "how do I buy sheets"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "sess-1"
    assert body["reply"] == "Go to the product page."
    assert body["retrieval_scores"] == [0.8]


_LEAD_TOOL_CALL = {
    "function": {
        "name": "capture_lead",
        "arguments": json.dumps({
            "intent": "wholesale",
            "name": "A",
            "email": "a@b.com",
            "phone": "1",
            "organization": "Org",
            "estimated_sheets": 500,
        }),
    }
}


@patch("app.chat.router.capture_lead")
@patch("app.chat.router.llm.chat_completion", return_value={
    "content": None,
    "tool_calls": [_LEAD_TOOL_CALL],
    "model": "test",
    "usage": {},
})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "Wholesale info.", "metadata": {}, "similarity": 0.7}
])
@patch("app.chat.router.memory")
def test_lead_capture_success(mem, _ret, _llm, mock_capture):
    mem.get_or_create_session.return_value = "sess-lead-1"
    mem.get_recent_messages.return_value = []
    mock_capture.return_value = {"id": "row-1"}

    resp = client.post("/chat", json={"message": "I want to buy wholesale"})
    assert resp.status_code == 200
    body = resp.json()

    # Response must have exactly the three frozen keys
    assert set(body.keys()) == {"session_id", "reply", "retrieval_scores"}
    assert body["session_id"] == "sess-lead-1"
    # Reply must be the confirmation string
    assert body["reply"] == (
        "Thanks — I've passed this to our team. They respond within 24 hours "
        "(usually ~15 minutes)."
    )
    # capture_lead called once with session_id, intent, and the remaining fields
    mock_capture.assert_called_once_with(
        "sess-lead-1",
        "wholesale",
        {"name": "A", "email": "a@b.com", "phone": "1",
         "organization": "Org", "estimated_sheets": 500},
    )


@patch("app.chat.router.capture_lead",
       side_effect=ValueError("missing required field: estimated_sheets"))
@patch("app.chat.router.llm.chat_completion", return_value={
    "content": None,
    "tool_calls": [_LEAD_TOOL_CALL],
    "model": "test",
    "usage": {},
})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "Wholesale info.", "metadata": {}, "similarity": 0.7}
])
@patch("app.chat.router.memory")
def test_lead_capture_validation_reprompt(mem, _ret, _llm, mock_capture):
    mem.get_or_create_session.return_value = "sess-lead-2"
    mem.get_recent_messages.return_value = []

    resp = client.post("/chat", json={"message": "I want to buy wholesale"})
    assert resp.status_code == 200
    body = resp.json()

    # Response shape must still be the frozen three keys
    assert set(body.keys()) == {"session_id", "reply", "retrieval_scores"}
    # Reply must contain the re-prompt text with the missing field info
    assert "missing required field: estimated_sheets" in body["reply"]
    assert "I still need a bit more info" in body["reply"]


@patch("app.chat.router.llm.chat_completion", return_value={
    "content": "Here is an off-topic answer the model made up.",
    "tool_calls": None, "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "weakly related", "metadata": {}, "similarity": 0.1}])
@patch("app.chat.router.memory")
def test_weak_retrieval_forces_escalation(mem, _ret, _llm):
    # Top similarity 0.1 is below LOW_SIMILARITY (0.25) and there is no lead tool-call,
    # so the grounding safety net must override the model's reply with the connect-to-team message.
    mem.get_or_create_session.return_value = "sess-esc"
    mem.get_recent_messages.return_value = []
    resp = client.post("/chat", json={"message": "what's the capital of France?"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"session_id", "reply", "retrieval_scores"}
    assert body["reply"] == chat_router._ESCALATION_REPLY
    assert "Here is an off-topic answer" not in body["reply"]


@patch("app.chat.router.llm.chat_completion", return_value={
    "content": "Grounded answer from the KB.",
    "tool_calls": None, "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "strongly related", "metadata": {}, "similarity": 0.82}])
@patch("app.chat.router.memory")
def test_strong_retrieval_keeps_model_reply(mem, _ret, _llm):
    # Good retrieval (0.82) must NOT trigger escalation — the model's grounded reply stands.
    mem.get_or_create_session.return_value = "sess-ok"
    mem.get_recent_messages.return_value = []
    resp = client.post("/chat", json={"message": "how do I buy sheets"})
    assert resp.json()["reply"] == "Grounded answer from the KB."


@patch("app.chat.router.injection_scanner.is_injection", return_value=True)
@patch("app.chat.router.memory")
def test_ml_scanner_blocks_flagged_message(mem, _scan):
    # A message the substring guard wouldn't catch, but the ML scanner flags, must be declined.
    mem.get_or_create_session.return_value = "sess-ml"
    resp = client.post("/chat", json={"message": "please summarize the attached document"})
    assert resp.status_code == 200
    assert "only help with Generation Conscious" in resp.json()["reply"]


@patch("app.chat.router.llm.chat_completion", return_value={
    "content": "ok", "tool_calls": None, "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "x", "metadata": {}, "similarity": 0.8}])
@patch("app.chat.router.memory")
def test_rate_limit_keyed_on_ip_not_session(mem, _ret, _llm):
    # Same client IP, rotating/omitting session_id, must still hit the per-IP limit.
    mem.get_or_create_session.side_effect = lambda s: s or "new-session"
    mem.get_recent_messages.return_value = []
    headers = {"X-Forwarded-For": "9.9.9.9"}
    with patch.object(chat_router, "_rate_limiter", guardrails.RateLimiter(per_minute=1)):
        first = client.post("/chat", json={"message": "hi"}, headers=headers)
        # different (omitted) session, SAME ip -> still limited
        second = client.post("/chat", json={"message": "hi"}, headers=headers)
        # different ip -> allowed
        other = client.post("/chat", json={"message": "hi"},
                            headers={"X-Forwarded-For": "8.8.8.8"})
    assert "give me a moment" in second.json()["reply"]
    assert "give me a moment" not in first.json()["reply"]
    assert "give me a moment" not in other.json()["reply"]


@patch("app.chat.router.llm.chat_completion", return_value={
    "content": "ok", "tool_calls": None, "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "x", "metadata": {}, "similarity": 0.8}])
@patch("app.chat.router.memory")
def test_rate_limit_keys_on_rightmost_xff_hop(mem, _ret, _llm):
    # The rightmost X-Forwarded-For hop is appended by the proxy (Render) and is not
    # client-spoofable; rotating forged leftmost values must NOT mint fresh buckets.
    mem.get_or_create_session.side_effect = lambda s: s or "new-session"
    mem.get_recent_messages.return_value = []
    with patch.object(chat_router, "_rate_limiter", guardrails.RateLimiter(per_minute=1)):
        first = client.post("/chat", json={"message": "hi"},
                            headers={"X-Forwarded-For": "1.1.1.1, 9.9.9.9"})
        # forged leftmost rotated, same real (rightmost) hop -> still limited
        second = client.post("/chat", json={"message": "hi"},
                             headers={"X-Forwarded-For": "2.2.2.2, 9.9.9.9"})
    assert "give me a moment" not in first.json()["reply"]
    assert "give me a moment" in second.json()["reply"]


# --- #2: malformed tool calls / unexpected capture_lead failures must not 500 ---

_MALFORMED_TOOL_CALL = {
    "function": {
        "name": "capture_lead",
        # truncated JSON — a known LLM failure mode, especially on the fallback model
        "arguments": '{"intent": "wholesale", "name": "A"',
    }
}

_NO_INTENT_TOOL_CALL = {
    "function": {
        "name": "capture_lead",
        "arguments": json.dumps({"name": "A", "email": "a@b.com"}),
    }
}


@patch("app.chat.router.capture_lead")
@patch("app.chat.router.llm.chat_completion", return_value={
    "content": None, "tool_calls": [_MALFORMED_TOOL_CALL], "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "Wholesale info.", "metadata": {}, "similarity": 0.7}])
@patch("app.chat.router.memory")
def test_malformed_tool_json_reprompts_not_500(mem, _ret, _llm, mock_capture):
    mem.get_or_create_session.return_value = "sess-bad-json"
    mem.get_recent_messages.return_value = []
    resp = client.post("/chat", json={"message": "wholesale please"},
                       headers={"X-Forwarded-For": "10.0.0.1"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"session_id", "reply", "retrieval_scores"}
    assert body["reply"] == chat_router._TOOL_REPROMPT_REPLY
    mock_capture.assert_not_called()


@patch("app.chat.router.capture_lead")
@patch("app.chat.router.llm.chat_completion", return_value={
    "content": None, "tool_calls": [_NO_INTENT_TOOL_CALL], "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "Wholesale info.", "metadata": {}, "similarity": 0.7}])
@patch("app.chat.router.memory")
def test_missing_intent_reprompts_not_500(mem, _ret, _llm, mock_capture):
    mem.get_or_create_session.return_value = "sess-no-intent"
    mem.get_recent_messages.return_value = []
    resp = client.post("/chat", json={"message": "wholesale please"},
                       headers={"X-Forwarded-For": "10.0.0.2"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == chat_router._TOOL_REPROMPT_REPLY
    mock_capture.assert_not_called()


@patch("app.chat.router.capture_lead",
       side_effect=RuntimeError("supabase APIError: insert failed"))
@patch("app.chat.router.llm.chat_completion", return_value={
    "content": None, "tool_calls": [_LEAD_TOOL_CALL], "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "Wholesale info.", "metadata": {}, "similarity": 0.7}])
@patch("app.chat.router.memory")
def test_capture_lead_unexpected_error_offers_contact_fallback(mem, _ret, _llm,
                                                               mock_capture, caplog):
    mem.get_or_create_session.return_value = "sess-db-down"
    mem.get_recent_messages.return_value = []
    with caplog.at_level(logging.ERROR, logger="app.chat.router"):
        resp = client.post("/chat", json={"message": "here are my details"},
                           headers={"X-Forwarded-For": "10.0.0.3"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"session_id", "reply", "retrieval_scores"}
    # the human path is offered instead of a raw error
    assert "Info@GenerationConscious.co" in body["reply"]
    assert "(516) 619-6174" in body["reply"]
    # the raw tool-call payload is logged so the lead fields are recoverable
    assert "a@b.com" in caplog.text


# --- #7: double LLM failure (primary + fallback) must not 500 ---

@patch("app.chat.router.llm.chat_completion", side_effect=Exception("openrouter down"))
@patch("app.chat.router.retrieve", return_value=[
    {"content": "x", "metadata": {}, "similarity": 0.8}])
@patch("app.chat.router.memory")
def test_double_llm_failure_returns_contact_info_not_500(mem, _ret, _llm):
    mem.get_or_create_session.return_value = "sess-outage"
    mem.get_recent_messages.return_value = []
    resp = client.post("/chat", json={"message": "how do I buy sheets"},
                       headers={"X-Forwarded-For": "10.0.0.4"})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"session_id", "reply", "retrieval_scores"}
    assert "Info@GenerationConscious.co" in body["reply"]
    assert body["retrieval_scores"] == [0.8]
    assert _llm.call_count == 2  # primary attempted, then fallback attempted
    # the fallback reply is persisted so history stays consistent
    mem.save_message.assert_any_call("sess-outage", "assistant", body["reply"])


# --- #3: the grounding safety net must not hijack mid-lead-collection turns ---

@patch("app.chat.router.llm.chat_completion", return_value={
    "content": "Thanks John — what's your organization?",
    "tool_calls": None, "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "weakly related", "metadata": {}, "similarity": 0.05}])
@patch("app.chat.router.memory")
def test_lead_flow_field_answer_not_hijacked_by_low_similarity(mem, _ret, _llm):
    mem.get_or_create_session.return_value = "sess-flow-1"
    mem.get_recent_messages.return_value = [
        {"role": "user", "content": "Buy Refill Stations", "created_at": "t1"},
        {"role": "assistant",
         "content": "Could I get your name and email? This is only used to connect you "
                    "with the Generation Conscious team.", "created_at": "t2"},
    ]
    resp = client.post("/chat", json={"message": "John Smith, john@acme.com"},
                       headers={"X-Forwarded-For": "10.0.0.5"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Thanks John — what's your organization?"


@patch("app.chat.router.llm.chat_completion", return_value={
    "content": "Got it — how many laundry rooms do you have?",
    "tool_calls": None, "model": "test", "usage": {}})
@patch("app.chat.router.retrieve", return_value=[
    {"content": "weakly related", "metadata": {}, "similarity": 0.1}])
@patch("app.chat.router.memory")
def test_lead_flow_keyword_answer_not_hijacked(mem, _ret, _llm):
    # "City Urgent Care" contains high-risk keyword "urgent" but is a field answer.
    mem.get_or_create_session.return_value = "sess-flow-2"
    mem.get_recent_messages.return_value = [
        {"role": "assistant", "content": "What's your organization?", "created_at": "t1"},
    ]
    resp = client.post("/chat", json={"message": "City Urgent Care"},
                       headers={"X-Forwarded-For": "10.0.0.6"})
    assert resp.status_code == 200
    assert resp.json()["reply"] == "Got it — how many laundry rooms do you have?"


# --- #10: guard gates must run before any Supabase call ---

@patch("app.chat.router.memory")
def test_throttled_request_does_not_touch_db(mem):
    with patch.object(chat_router, "_rate_limiter", guardrails.RateLimiter(per_minute=0)):
        resp = client.post("/chat", json={"session_id": "abc", "message": "hi"},
                           headers={"X-Forwarded-For": "10.0.0.7"})
    assert resp.status_code == 200
    body = resp.json()
    assert "give me a moment" in body["reply"]
    assert body["session_id"] == "abc"  # client id echoed, no session minted
    mem.get_or_create_session.assert_not_called()
    mem.save_message.assert_not_called()


@patch("app.chat.router.memory")
def test_throttled_request_without_session_echoes_empty(mem):
    with patch.object(chat_router, "_rate_limiter", guardrails.RateLimiter(per_minute=0)):
        resp = client.post("/chat", json={"message": "hi"},
                           headers={"X-Forwarded-For": "10.0.0.8"})
    assert resp.json()["session_id"] == ""
    mem.get_or_create_session.assert_not_called()


