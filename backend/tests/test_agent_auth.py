import base64
import json

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from app.config import get_settings


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_PASSWORD", "hunter2")
    monkeypatch.setenv("AGENT_SESSION_SECRET", "test-signing-secret")
    yield
    get_settings.cache_clear()


def _request(cookie: str | None = None) -> Request:
    headers = [(b"cookie", f"gc_agent={cookie}".encode())] if cookie else []
    return Request({"type": "http", "method": "GET", "path": "/agent/faq-gaps",
                    "headers": headers, "query_string": b"", "scheme": "https"})


def test_portal_enabled_only_when_both_secrets_present(monkeypatch):
    from app.agent import auth
    assert auth.portal_enabled() is True
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_SESSION_SECRET", "")
    assert auth.portal_enabled() is False


def test_correct_password_accepted_and_wrong_rejected():
    from app.agent import auth
    assert auth.password_ok("hunter2") is True
    assert auth.password_ok("wrong") is False
    assert auth.password_ok("") is False


def test_blank_configured_password_never_authenticates(monkeypatch):
    from app.agent import auth
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_PASSWORD", "")
    assert auth.password_ok("") is False


def test_round_trip_token_is_valid():
    from app.agent import auth
    assert auth.token_valid(auth.make_token()) is True


def test_expired_token_rejected():
    from app.agent import auth
    old = auth.make_token(now=1000.0)
    assert auth.token_valid(old, now=1000.0 + auth.SESSION_TTL_SECONDS + 1) is False


def test_tampered_signature_rejected():
    from app.agent import auth
    payload, _sig = auth.make_token().split(".")
    assert auth.token_valid(f"{payload}.deadbeef") is False


def test_tampered_payload_rejected():
    from app.agent import auth
    _payload, sig = auth.make_token().split(".")
    forged = base64.urlsafe_b64encode(
        json.dumps({"exp": 9999999999}).encode()).decode().rstrip("=")
    assert auth.token_valid(f"{forged}.{sig}") is False


@pytest.mark.parametrize("bad", [None, "", "garbage", "a.b.c", "no-dot"])
def test_malformed_tokens_rejected_without_raising(bad):
    from app.agent import auth
    assert auth.token_valid(bad) is False


def test_require_agent_allows_a_valid_cookie():
    from app.agent import auth
    auth.require_agent(_request(auth.make_token()))  # must not raise


def test_require_agent_401s_without_a_cookie():
    from app.agent import auth
    with pytest.raises(HTTPException) as e:
        auth.require_agent(_request())
    assert e.value.status_code == 401


def test_require_agent_503s_when_portal_disabled(monkeypatch):
    from app.agent import auth
    token = auth.make_token()
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_SESSION_SECRET", "")
    with pytest.raises(HTTPException) as e:
        auth.require_agent(_request(token))
    assert e.value.status_code == 503
