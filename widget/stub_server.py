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
        reply = _reply_for(message)
        now = datetime.now(UTC).isoformat()
        history = _HISTORY.setdefault(session_id, [])
        history.append({"role": "user", "content": message, "created_at": now})
        if reply:
            history.append({"role": "assistant", "content": reply, "created_at": now})
        self._send_json(
            {"session_id": session_id, "reply": reply, "retrieval_scores": [0.91, 0.84, 0.42]}
        )

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
