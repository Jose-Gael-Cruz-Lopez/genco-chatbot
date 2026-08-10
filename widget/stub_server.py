"""Offline stub backend for widget/test.html — zero keys, zero dependencies.

Serves canned versions of the two frozen widget endpoints with permissive CORS
so the full conversation UI (greeting, quick replies, typing indicator, reply
bubbles, reload-rehydrate) can be exercised without Supabase/OpenRouter keys:

    POST /chat               -> {"session_id", "reply", "retrieval_scores"}
    GET  /history?session_id -> {"session_id", "messages": [{"role", "content", "created_at"}]}

History is kept in-memory per session_id for the lifetime of the process, so a
page reload repaints the conversation via /history exactly like the real
backend.

Test hooks (stub-only, for exercising the widget's error handling):
    - send the message "fail"  -> HTTP 500 JSON (widget must show the friendly
      error bubble, not an empty one)
    - send the message "empty" -> HTTP 200 with reply "" (widget must fall back
      to the friendly error text)

Run (stdlib only, any Python 3.11+ works):

    python widget/stub_server.py [port]     # default port 8000

then open widget/test.html (its default backend URL is http://localhost:8000).
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_HISTORY: dict[str, list[dict[str, str]]] = {}

_CANNED: dict[str, str] = {
    "buy sheets": (
        "You can pick your sheet count, scent, and one-time vs. subscription on our "
        "product page: https://generationconscious.co/product/laundry-detergent-sheets/"
    ),
    "buy refill stations": (
        "Happy to connect you with the team! Could you share your name, email, phone, "
        "organization, number of laundry rooms, and number of students (tenants)?"
    ),
    "question for the team": (
        "Of course — what's your question? I'll pass it to the team along with your "
        "name and email (they usually reply within 15 minutes)."
    ),
}

_DEFAULT_REPLY = (
    "(stubbed) Thanks — this canned reply proves the conversation UI works offline. "
    "You said: {message}"
)


def _reply_for(message: str) -> str:
    normalized = message.strip().lower()
    if normalized == "empty":
        return ""
    return _CANNED.get(normalized, _DEFAULT_REPLY.format(message=message))


class StubHandler(BaseHTTPRequestHandler):
    """Canned /chat and /history with permissive CORS."""

    def _send_json(self, body: dict[str, object], status: int = 200) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
