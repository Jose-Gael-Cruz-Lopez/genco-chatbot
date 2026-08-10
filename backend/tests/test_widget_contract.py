"""Contract tests for the widget's offline stub backend (widget/stub_server.py).

widget/test.html exercises the widget against the stub, so the stub's canned
/chat and /history responses must keep exactly the same shape as the real
FastAPI backend's frozen contracts:

    POST /chat               -> {"session_id", "reply", "retrieval_scores"}
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

from app.main import app

_STUB_PATH = Path(__file__).resolve().parents[2] / "widget" / "stub_server.py"

_spec = importlib.util.spec_from_file_location("widget_stub_server", _STUB_PATH)
assert _spec and _spec.loader, f"cannot load widget stub from {_STUB_PATH}"
stub_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stub_server)

client = TestClient(app)


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
