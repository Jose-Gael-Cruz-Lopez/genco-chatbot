"""Offline stub backend for widget/test.html — zero keys, zero dependencies.

Serves canned versions of the two frozen widget endpoints with permissive CORS
so the full conversation UI (greeting, quick replies, typing indicator, reply
bubbles, reload-rehydrate) can be exercised without Supabase/OpenRouter keys:

    POST /chat               -> {"session_id", "reply", "retrieval_scores", "quick_replies"}
    GET  /history?session_id -> {"session_id", "messages": [{"role", "content", "created_at"}]}

The canned conversation mirrors FAQ-match mode (BOT_MODE=faq): a keyword-matched
answer comes back with 👍/✉️ feedback buttons, tapping ✉️ walks the guided lead
flow one field at a time, and an unmatched question offers to send it to the
team. Replies are deliberately the same strings the real backend returns so the
widget is exercised against the shapes it will meet in production.

History and flow position are kept in-memory per session_id for the lifetime of
the process, so a page reload repaints the conversation via /history exactly
like the real backend.

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
import re
import sys
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_HISTORY: dict[str, list[dict[str, str]]] = {}
# Session -> (lead intent, index of the field being collected). Stands in for
# chat_sessions.flow_state; absent means idle.
_FLOW: dict[str, tuple[str, int]] = {}

# Button labels, verbatim from backend/app/chat/flows.py — the widget sends a tap
# back as a normal user message, so these strings are the stub's flow triggers.
_FEEDBACK_YES = "\U0001F44D Yes, that answered it"
_FEEDBACK_NO = "✉️ No — ask the team"
_SEND_TO_TEAM = "✉️ Send my question to the team"
_WHOLESALE_START = "Start wholesale inquiry"
_BUY_SHEETS = "Buy Sheets"
_BUY_REFILL = "Buy Refill Stations"
_ASK_TEAM = "Question for the team"

_PRODUCT_URL = "https://generationconscious.co/product/laundry-detergent-sheets/"

_BUY_SHEETS_REPLY = (
    "Great — you can choose your sheet count, scent, and one-time or "
    f"subscription here: {_PRODUCT_URL}"
)
_NO_MATCH_REPLY = (
    "I couldn't find that in our FAQ — but our team can answer it personally."
)
_HANDOFF_LEAD_IN = "No problem — I'll pass this to our team so they can answer you personally."
_THANKS_REPLY = "Glad that helped! Anything else I can look up for you?"
_CANCEL_REPLY = "No problem — that's cancelled. What else can I help you with?"

# Keyword -> verbatim answer, standing in for the Postgres full-text match. Each
# entry may add flow-trigger buttons of its own, the way a KB source does.
_FAQ_ANSWERS: list[tuple[tuple[str, ...], str, list[str]]] = [
    (("ship", "shipping", "delivery"),
     "Shipping is calculated at checkout using live USPS rates.", []),
    (("tax", "taxes"), "Sales tax applies to New York orders only.", []),
    (("buy", "sheets", "order"), _BUY_SHEETS_REPLY, []),
    (("wholesale", "bulk"),
     "We do offer wholesale pricing — the team sizes each order individually.",
     [_WHOLESALE_START]),
    (("refill", "station", "campus"),
     "Refill stations live in your laundry room and are restocked on a "
     "subscription, so residents never buy a bottle again.", []),
]

# Field prompts, verbatim from flows._prompt_for, in REQUIRED_FIELDS order.
_LEAD_PROMPTS: dict[str, list[str]] = {
    "question": ["Sure — what's your question for the team?",
                 "What's your name?",
                 "What's your email address?"],
    "wholesale": ["What's your name?",
                  "What's your email address?",
                  "What's your phone number?",
                  "What's your organization's name?",
                  "What's your estimated total sheet purchase?"],
    "refill_station": ["What's your name?",
                       "What's your email address?",
                       "What's your phone number?",
                       "What's your organization's name?",
                       "What's the number of laundry rooms?",
                       "What's the number of students or tenants?"],
}
_LEAD_DONE: dict[str, str] = {
    "question": ("Thanks — I've sent your question to our team. They usually "
                 "reply the same day."),
    "wholesale": ("Thanks — I've passed your wholesale inquiry to our team. "
                  "They respond within 24 hours (usually ~15 minutes)."),
    "refill_station": ("Thanks — I've passed your refill-station details to our "
                       "team. They respond within 24 hours (usually ~15 minutes)."),
}

_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def _norm(text: str) -> str:
    """Match a button tap by its words alone, ignoring emoji and punctuation."""
    return " ".join(_NORM_RE.sub(" ", text.lower()).split())


def _start_lead(session_id: str, intent: str, step: int = 0) -> tuple[str, list[str]]:
    _FLOW[session_id] = (intent, step)
    return _LEAD_PROMPTS[intent][step], []


def _lead_step(session_id: str, intent: str, step: int) -> tuple[str, list[str]]:
    """Accept the answer to the current field and ask for the next, or finish.

    The stub takes any answer: field validation is the real backend's job, and
    what's under test here is the widget walking the flow one prompt at a time.
    """
    step += 1
    if step < len(_LEAD_PROMPTS[intent]):
        _FLOW[session_id] = (intent, step)
        return _LEAD_PROMPTS[intent][step], []
    del _FLOW[session_id]
    return _LEAD_DONE[intent], []


def _turn(session_id: str, message: str) -> tuple[str, list[str]]:
    """One canned FAQ turn -> (reply, quick_replies)."""
    text = message.strip()
    normalized = _norm(text)
    if normalized == "cancel":
        _FLOW.pop(session_id, None)
        return _CANCEL_REPLY, [_BUY_SHEETS, _BUY_REFILL, _ASK_TEAM]
    flow = _FLOW.get(session_id)
    if flow is not None:
        return _lead_step(session_id, *flow)
    if normalized == _norm(_BUY_SHEETS):
        return _BUY_SHEETS_REPLY, []
    if normalized == _norm(_BUY_REFILL):
        return _start_lead(session_id, "refill_station")
    if normalized == _norm(_ASK_TEAM):
        return _start_lead(session_id, "question")
    if normalized == _norm(_SEND_TO_TEAM):
        # Escalating a no-match: the question is already known, so the real flow
        # skips to the name and leads in with the handoff acknowledgement.
        reply, quick = _start_lead(session_id, "question", step=1)
        return f"{_HANDOFF_LEAD_IN} {reply}", quick
    if normalized == _norm(_FEEDBACK_NO):
        # Same as above, reached from an answer the visitor found unhelpful.
        reply, quick = _start_lead(session_id, "question", step=1)
        return f"{_HANDOFF_LEAD_IN} {reply}", quick
    if normalized == _norm(_FEEDBACK_YES):
        return _THANKS_REPLY, []
    if normalized == _norm(_WHOLESALE_START):
        return _start_lead(session_id, "wholesale")
    words = set(normalized.split())
    for keywords, answer, triggers in _FAQ_ANSWERS:
        if words.intersection(keywords):
            return answer, [_FEEDBACK_YES, _FEEDBACK_NO] + triggers
    return _NO_MATCH_REPLY, [_SEND_TO_TEAM]


class StubHandler(BaseHTTPRequestHandler):
    """Canned /chat and /history with permissive CORS."""

    def _send_json(self, body: dict[str, object], status: int = 200) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802 (http.server API)
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/history":
            self._send_json({"detail": "not found"}, status=404)
            return
        session_id = (parse_qs(parsed.query).get("session_id") or [""])[0]
        self._send_json({"session_id": session_id, "messages": _HISTORY.get(session_id, [])})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/chat":
            self._send_json({"detail": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            payload = {}
        message = str(payload.get("message") or "")
        if message.strip().lower() == "fail":
            self._send_json({"detail": "stubbed internal error"}, status=500)
            return
        session_id = str(payload.get("session_id") or "stub-session-1")
        if message.strip().lower() == "empty":
            reply, quick_replies = "", []
        else:
            reply, quick_replies = _turn(session_id, message)
        now = datetime.now(UTC).isoformat()
        history = _HISTORY.setdefault(session_id, [])
        history.append({"role": "user", "content": message, "created_at": now})
        if reply:
            history.append({"role": "assistant", "content": reply, "created_at": now})
        self._send_json({
            "session_id": session_id,
            "reply": reply,
            "retrieval_scores": [0.91, 0.84, 0.42],
            "quick_replies": quick_replies,
        })

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 (http.server API)
        sys.stderr.write("[stub] " + format % args + "\n")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    server = ThreadingHTTPServer(("127.0.0.1", port), StubHandler)
    print(
        f"Genco stub backend on http://localhost:{port} — POST /chat, GET /history "
        "(Ctrl-C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
