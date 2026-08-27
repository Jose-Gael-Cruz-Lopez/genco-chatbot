"""Contract tests for the widget's offline stub backend (widget/stub_server.py).

widget/test.html exercises the widget against the stub, so the stub's canned
/chat and /history responses must keep exactly the same shape as the real
FastAPI backend's frozen contracts:

    POST /chat               -> {"session_id", "reply", "retrieval_scores",
                                 "quick_replies"}
    GET  /history?session_id -> {"session_id", "messages": [{"role", "content", "created_at"}]}

The stub is started on an ephemeral loopback port; the real backend is served
in-process via TestClient with its external calls (LLM, retrieval, Supabase
memory) mocked. No browser needed.
"""

import importlib.util
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.chat import router as chat_router
from app.main import app

_STUB_PATH = Path(__file__).resolve().parents[2] / "widget" / "stub_server.py"

_spec = importlib.util.spec_from_file_location("widget_stub_server", _STUB_PATH)
assert _spec and _spec.loader, f"cannot load widget stub from {_STUB_PATH}"
stub_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stub_server)

client = TestClient(app)

# quick_replies is additive: the real backend sends it on every path, and the stub
# picks it up with the widget's quick-reply rendering. Either way the stub must carry
# the frozen core and invent no key the real backend does not send.
_FROZEN_CHAT_KEYS = {"session_id", "reply", "retrieval_scores"}


@pytest.fixture(autouse=True)
def generative_mode():
    # BOT_MODE defaults to "faq"; the real-backend half of these contract checks
    # mocks the generative pipeline, so pin the mode the mocks belong to.
    with patch.object(chat_router._settings, "BOT_MODE", "generative"):
        yield


@pytest.fixture(scope="module")
def stub_port():
    server = ThreadingHTTPServer(("127.0.0.1", 0), stub_server.StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def _stub_post_chat(port: int, payload: dict) -> dict:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/chat",
        json.dumps(payload).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def _stub_get_history(port: int, session_id: str) -> dict:
    url = f"http://127.0.0.1:{port}/history?session_id={session_id}"
    with urllib.request.urlopen(url) as resp:
        return json.loads(resp.read())


def _real_chat_response() -> dict:
    with patch("app.chat.router.llm.chat_completion", return_value={
            "content": "Grounded reply.", "tool_calls": None,
            "model": "test", "usage": {}}), \
         patch("app.chat.router.retrieve", return_value=[
            {"content": "ctx", "metadata": {}, "similarity": 0.9}]), \
         patch("app.chat.router.memory") as mem:
        mem.get_or_create_session.return_value = "real-sess"
        mem.get_recent_messages.return_value = []
        resp = client.post("/chat", json={"message": "how do I buy sheets"},
                           headers={"X-Forwarded-For": "10.42.0.1"})
    assert resp.status_code == 200
    return resp.json()


def _real_history_response() -> dict:
    with patch("app.chat.router.memory") as mem:
        mem.get_recent_messages.return_value = [
            {"role": "user", "content": "hi",
             "created_at": "2026-08-10T00:00:00+00:00"},
            {"role": "assistant", "content": "hello",
             "created_at": "2026-08-10T00:00:01+00:00"},
        ]
        resp = client.get("/history", params={"session_id": "real-sess"})
    assert resp.status_code == 200
    return resp.json()


def test_stub_chat_matches_real_chat_schema(stub_port):
    stub = _stub_post_chat(stub_port, {"message": "buy sheets",
                                       "session_id": "contract-chat"})
    real = _real_chat_response()
    assert set(real) == _FROZEN_CHAT_KEYS | {"quick_replies"}
    assert _FROZEN_CHAT_KEYS <= set(stub) <= set(real)
    for body in (stub, real):
        assert isinstance(body["session_id"], str) and body["session_id"]
        assert isinstance(body["reply"], str) and body["reply"]
        assert isinstance(body["retrieval_scores"], list)
        assert all(isinstance(s, (int, float)) for s in body["retrieval_scores"])
        assert isinstance(body.get("quick_replies", []), list)
        assert all(isinstance(q, str) for q in body.get("quick_replies", []))


def test_stub_history_matches_real_history_schema(stub_port):
    _stub_post_chat(stub_port, {"message": "buy sheets",
                                "session_id": "contract-history"})
    stub = _stub_get_history(stub_port, "contract-history")
    real = _real_history_response()
    assert set(stub) == set(real) == {"session_id", "messages"}
    assert stub["session_id"] == "contract-history"
    for body in (stub, real):
        assert isinstance(body["messages"], list)
        assert body["messages"], "history must be non-empty for the shape check"
        for msg in body["messages"]:
            assert set(msg) == {"role", "content", "created_at"}
            assert msg["role"] in ("user", "assistant")
            assert isinstance(msg["content"], str)
            assert isinstance(msg["created_at"], str) and msg["created_at"]


def test_stub_buy_sheets_reply_uses_home_delivery_product_url(stub_port):
    # The stub's canned "Buy Sheets" quick-reply must model the real home-delivery flow:
    # the product page (variant picker), never /checkout/ or the location-subscription page.
    stub = _stub_post_chat(stub_port, {"message": "buy sheets",
                                       "session_id": "contract-url"})
    assert "https://generationconscious.co/product/laundry-detergent-sheets/" in stub["reply"]
    assert "/checkout/" not in stub["reply"]
    assert "location-subscription" not in stub["reply"]


def test_quick_replies_is_always_a_list_in_the_response():
    # The widget renders data.quick_replies on every reply, so the key must be
    # present and a list on every path — including generative turns, which never
    # offer buttons and send an empty list.
    body = _real_chat_response()
    assert "quick_replies" in body
    assert isinstance(body["quick_replies"], list)
    assert body["quick_replies"] == []
