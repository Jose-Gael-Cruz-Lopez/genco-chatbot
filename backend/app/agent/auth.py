"""Agent portal authentication: one shared password, one signed cookie.

Greg is the only agent, so a user table would be premature. The password lives in
the environment and the session is a stdlib HMAC-signed token — no new dependency,
nothing stored server-side, and signing out everyone is a matter of rotating one
secret.

The portal fails CLOSED: with either secret unset every route refuses service,
because an unconfigured deploy must never expose visitor questions to the internet.
"""
import base64
import hashlib
import hmac
import json
import logging
import time

from fastapi import HTTPException, Request

from app.config import get_settings

log = logging.getLogger(__name__)

SESSION_COOKIE = "gc_agent"
COOKIE_PATH = "/agent"
SESSION_TTL_SECONDS = 12 * 60 * 60


def portal_enabled() -> bool:
    """True only when both the password and the signing secret are configured."""
    s = get_settings()
    return bool(s.AGENT_PASSWORD) and bool(s.AGENT_SESSION_SECRET)


def password_ok(password: str) -> bool:
    """Constant-time password check. A blank configured password never matches,
    so an unset AGENT_PASSWORD cannot be satisfied by sending an empty string."""
    configured = get_settings().AGENT_PASSWORD
    if not configured:
        return False
    # Compare as bytes: compare_digest rejects non-ASCII str operands with a
    # TypeError, and the submitted password is arbitrary JSON text — an accented
    # or emoji password must be a wrong password, not a 500.
    return hmac.compare_digest((password or "").encode(), configured.encode())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: str) -> str:
    secret = get_settings().AGENT_SESSION_SECRET.encode()
    return _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())


def make_token(now: float | None = None) -> str:
    """Mint a session token: base64(payload).base64(hmac-sha256(payload))."""
    exp = int((now if now is not None else time.time()) + SESSION_TTL_SECONDS)
    payload = _b64(json.dumps({"exp": exp}).encode())
    return f"{payload}.{_sign(payload)}"


def token_valid(token: str | None, now: float | None = None) -> bool:
    """Verify signature then expiry. Any malformed input is simply invalid —
    a hand-crafted cookie must never raise its way into a 500."""
    if not token or not portal_enabled():
        return False
    try:
        payload, signature = token.split(".")
        if not hmac.compare_digest(signature, _sign(payload)):
            return False
        exp = json.loads(_unb64(payload))["exp"]
    except Exception:
        return False
    return float(exp) > (now if now is not None else time.time())


def require_agent(request: Request) -> None:
    """Gate for every /agent route except the login page and POST /agent/login."""
    if not portal_enabled():
        log.warning("Agent portal route hit while AGENT_PASSWORD/"
                    "AGENT_SESSION_SECRET are unset; refusing.")
        raise HTTPException(status_code=503, detail="Agent portal is not configured.")
    if not token_valid(request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Not signed in.")
