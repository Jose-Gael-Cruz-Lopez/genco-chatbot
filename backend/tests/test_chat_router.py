import json
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from app.main import app

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
