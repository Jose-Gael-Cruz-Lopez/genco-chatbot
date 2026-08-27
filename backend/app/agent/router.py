"""HTTP routes for the agent portal.

Everything here is same-origin: the portal page is served by this service, and the
session cookie is SameSite=Strict, so the permissive CORS policy the widget needs
cannot be used to reach these routes with credentials from the WordPress origin.
"""
import logging

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app import guardrails
from app.agent import auth, faq_gaps

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent")

# Login is the one unauthenticated write here, so it gets its own bucket: five
# attempts per minute per IP makes the shared password impractical to guess
# without affecting the visitor chat limiter.
_login_limiter = guardrails.RateLimiter(5)


def _client_ip(request: Request) -> str:
    # Rightmost X-Forwarded-For hop is the proxy-set one (see chat/router.py).
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.rsplit(",", 1)[-1].strip()
    return request.client.host if request.client else "unknown"


class LoginRequest(BaseModel):
    password: str


class FaqEntryRequest(BaseModel):
    title: str
    content: str
    resolve_ids: list[str] = []


@router.post("/login")
def login(req: LoginRequest, request: Request, response: Response) -> dict:
    if not auth.portal_enabled():
        raise HTTPException(status_code=503, detail="Agent portal is not configured.")
    if not _login_limiter.allow(_client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many attempts. Wait a minute.")
    if not auth.password_ok(req.password):
        logger.warning("Failed agent login from %s", _client_ip(request))
        raise HTTPException(status_code=401, detail="Wrong password.")
    response.set_cookie(
        auth.SESSION_COOKIE, auth.make_token(),
        max_age=auth.SESSION_TTL_SECONDS, path=auth.COOKIE_PATH,
        httponly=True, samesite="strict",
        # Secure is derived from the live scheme so local HTTP development can
        # still sign in, without an env switch someone forgets to flip back.
        secure=request.url.scheme == "https",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(auth.SESSION_COOKIE, path=auth.COOKIE_PATH)
    return {"ok": True}


@router.get("/faq-gaps")
def gaps(request: Request) -> dict:
    auth.require_agent(request)
    return {"gaps": faq_gaps.list_gaps()}


@router.post("/faq-entry")
def publish(req: FaqEntryRequest, request: Request) -> dict:
    auth.require_agent(request)
    try:
        faq_gaps.publish_entry(req.title, req.content, req.resolve_ids)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("publishing an FAQ entry failed (title=%r)", req.title)
        raise HTTPException(status_code=500, detail="Could not publish. Try again.")
    return {"ok": True}
