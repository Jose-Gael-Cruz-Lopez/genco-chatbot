import re
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


@pytest.fixture
def client(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_PASSWORD", "hunter2")
    monkeypatch.setenv("AGENT_SESSION_SECRET", "test-signing-secret")
    import importlib
    import app.agent.router as agent_router
    import app.main as main_mod
    importlib.reload(agent_router)
    importlib.reload(main_mod)
    yield TestClient(main_mod.app)
    get_settings.cache_clear()


def _login(client):
    r = client.post("/agent/login", json={"password": "hunter2"})
    assert r.status_code == 200
    return r


def test_login_with_the_right_password_sets_a_cookie(client):
    r = _login(client)
    assert "gc_agent" in r.cookies


def test_login_with_the_wrong_password_401s_and_sets_nothing(client):
    r = client.post("/agent/login", json={"password": "nope"})
    assert r.status_code == 401
    assert "gc_agent" not in r.cookies


def test_faq_gaps_requires_a_session(client):
    assert client.get("/agent/faq-gaps").status_code == 401


def test_faq_gaps_returns_gaps_once_signed_in(client):
    _login(client)
    gaps = [{"question": "do you ship", "count": 3, "ids": ["1"],
             "last_asked": "2026-08-01"}]
    with patch("app.agent.router.faq_gaps.list_gaps", return_value=gaps):
        r = client.get("/agent/faq-gaps")
    assert r.status_code == 200
    assert r.json() == {"gaps": gaps}


def test_publishing_requires_a_session(client):
    r = client.post("/agent/faq-entry", json={"title": "T", "content": "C",
                                              "resolve_ids": []})
    assert r.status_code == 401


def test_publishing_an_entry_succeeds(client):
    _login(client)
    with patch("app.agent.router.faq_gaps.publish_entry") as pub:
        r = client.post("/agent/faq-entry",
                        json={"title": "Shipping", "content": "USPS.",
                              "resolve_ids": ["1"]})
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    pub.assert_called_once_with("Shipping", "USPS.", ["1"])


def test_publishing_blank_content_returns_a_readable_error(client):
    _login(client)
    with patch("app.agent.router.faq_gaps.publish_entry",
               side_effect=ValueError("An answer is required.")):
        r = client.post("/agent/faq-entry",
                        json={"title": "T", "content": "", "resolve_ids": []})
    assert r.status_code == 400
    assert r.json()["detail"] == "An answer is required."


def test_logout_clears_the_session(client):
    _login(client)
    client.post("/agent/logout")
    assert client.get("/agent/faq-gaps").status_code == 401


def test_routes_503_when_the_portal_is_unconfigured(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_PASSWORD", "")
    monkeypatch.setenv("AGENT_SESSION_SECRET", "")
    import importlib
    import app.agent.router as agent_router
    import app.main as main_mod
    importlib.reload(agent_router)
    importlib.reload(main_mod)
    c = TestClient(main_mod.app)
    assert c.post("/agent/login", json={"password": "x"}).status_code == 503
    assert c.get("/agent/faq-gaps").status_code == 503
    get_settings.cache_clear()


def test_login_is_rate_limited(client):
    for _ in range(5):
        client.post("/agent/login", json={"password": "nope"})
    assert client.post("/agent/login", json={"password": "hunter2"}).status_code == 429


def test_portal_page_is_served_at_agent(client):
    r = client.get("/agent")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Generation Conscious" in r.text


def test_portal_page_never_assigns_innerhtml(client):
    # The portal renders visitor-typed question text, so assigning innerHTML there
    # is an XSS hole. Matches assignment specifically: the file's own comments
    # mention the property by name precisely to warn against it.
    assert re.search(r"innerHTML\s*=", client.get("/agent").text) is None
