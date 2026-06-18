from unittest.mock import patch
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
