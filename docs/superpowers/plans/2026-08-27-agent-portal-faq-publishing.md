# Agent Portal & FAQ Publishing Implementation Plan

**Status:** ✅ Completed 2026-08-27. Test suite went 161 → 202 passing. Includes the `managed_by` fix that stops a re-ingest deleting portal-authored FAQ entries.

> Per-step `git commit` blocks below were executed as granular per-file commits by the
> orchestrator rather than run verbatim; every other step ran as written.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Greg a password-protected portal where he sees every question the FAQ couldn't answer and turns each one into a permanent FAQ entry — so the same question never reaches him twice.

**Architecture:** A new `app/agent/` package holds password auth (stdlib HMAC-signed cookie), the `/agent/*` routes, and the FAQ-gap/publishing logic. A single self-contained `portal/dist/portal.html` is served at `/agent`, mirroring how `widget/dist/widget.js` already ships. Knowledge-base rows gain a `managed_by` owner column so ingest can prune file-authored rows without destroying portal-authored ones.

**Tech Stack:** Python 3.11+ / FastAPI, pydantic-settings, Supabase Postgres, stdlib `hmac`/`hashlib`/`base64` for auth (no new dependency), pytest, vanilla-JS single-file portal.

**Spec:** `docs/superpowers/specs/2026-08-27-live-agent-portal-design.md` — this plan implements §2 (Authentication), §6 (portal shell + FAQ gaps pane), and §7 (FAQ publishing and the ingest ownership fix). §1, §3, §4, and the live-chat half of §5 are **Plan 2** and are deliberately out of scope here.

## Global Constraints

- **No new dependencies.** Auth uses `hmac`, `hashlib`, `base64`, `json`, `time` from the stdlib. `backend/requirements.txt` must not grow.
- **Still zero AI.** Nothing in this plan may import `app.llm`, `app.rag.embeddings`, or any model client.
- **Never crash a turn.** Every Supabase call is wrapped; failures log and degrade, they do not 500 a visitor route.
- **XSS-safe rendering.** The portal displays visitor-typed question text. Use `textContent` only — never `innerHTML` — exactly as `widget/dist/widget.js` already does.
- **Fail closed.** With `AGENT_PASSWORD` or `AGENT_SESSION_SECRET` unset, `/agent/*` must be unusable, never open.
- **Typed Python 3.11+**: full annotations, `list[str]` not `List[str]`.
- **TDD**: write the failing test, run it and confirm it fails, then implement.
- **Baseline to protect:** 161 passed / 2 skipped. No existing test may break.
- **Secure cookie flag** is set from `request.url.scheme == "https"` so local HTTP development can still log in.
- **Session cookie name** is `gc_agent`; **cookie path** is `/agent`; **TTL** is 12 hours.

---

### Task 1: Ownership column and the ingest fix

This is the data-loss bug. Do it first, before anything can write a portal-authored row.

**Files:**
- Modify: `backend/app/rag/schema.sql`
- Modify: `backend/app/rag/ingest.py`
- Test: `backend/tests/test_ingest.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: `kb_documents.managed_by text not null default 'file'`; `faq_misses.resolved boolean not null default false`; `ingest_all()` that prunes only `managed_by='file'` rows.

- [ ] **Step 1: Write the failing regression test**

Append to `backend/tests/test_ingest.py`:

```python
def test_ingest_does_not_delete_portal_authored_entries(monkeypatch, tmp_path):
    """Regression: FAQ entries Greg publishes from the portal must survive a re-ingest.

    Before the managed_by fix, ingest deleted every row whose content_hash was not
    derived from the markdown files — which is every entry written in the portal.
    """
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "faq")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("# Shipping\nWe ship via USPS.")
    monkeypatch.setattr("app.rag.ingest.KB_DIR", kb)
    sb = MagicMock()
    with patch("app.rag.ingest.get_supabase", return_value=sb):
        ingest.ingest_all()
    # The prune must be scoped to file-managed rows, so portal rows are untouched.
    sb.table.return_value.delete.return_value.eq.assert_called_once_with(
        "managed_by", "file")
    get_settings.cache_clear()


def test_ingest_marks_its_own_rows_as_file_managed(monkeypatch, tmp_path):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "faq")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("# Shipping\nWe ship via USPS.")
    monkeypatch.setattr("app.rag.ingest.KB_DIR", kb)
    sb = MagicMock()
    with patch("app.rag.ingest.get_supabase", return_value=sb):
        ingest.ingest_all()
    rows = sb.table.return_value.upsert.call_args[0][0]
    assert rows and all(r["managed_by"] == "file" for r in rows)
    get_settings.cache_clear()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ingest.py -k portal_authored -v`
Expected: FAIL — `AssertionError: Expected 'eq' to be called once. Called 0 times.`

- [ ] **Step 3: Extend the schema**

Append to `backend/app/rag/schema.sql`:

```sql
-- ── Portal-authored knowledge (agent portal) ──────────────────────────────
-- Who owns a row: 'file' = authored in backend/knowledge_base/*.md and owned by
-- ingest; 'portal' = written by the team in the agent portal and owned by the
-- database. Ingest prunes ONLY file-managed rows — without this, the first
-- re-ingest after publishing deletes every portal-written answer.
alter table kb_documents add column if not exists managed_by text not null default 'file';
create index if not exists kb_documents_managed_by_idx on kb_documents(managed_by);

-- A gap leaves the portal's list once someone publishes an answer for it.
alter table faq_misses add column if not exists resolved boolean not null default false;
create index if not exists faq_misses_unresolved_idx on faq_misses(resolved, created_at desc);
```

- [ ] **Step 4: Fix ingest**

In `backend/app/rag/ingest.py`, add `"managed_by": "file",` to the dict inside `rows_by_hash.setdefault(...)`, immediately after the `"metadata"` key.

Then replace the prune block:

```python
    # Only now remove rows that are no longer part of the KB — and only rows this
    # script owns. Portal-authored entries (managed_by='portal') are the team's
    # work and must survive every re-ingest.
    sb.table("kb_documents").delete().eq("managed_by", "file").not_.in_(
        "content_hash", list(rows_by_hash)
    ).execute()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_ingest.py -v`
Expected: PASS (all, including both new tests)

- [ ] **Step 6: Commit**

```bash
git add backend/app/rag/schema.sql backend/app/rag/ingest.py backend/tests/test_ingest.py
git commit -m "fix: stop ingest deleting portal-authored FAQ entries"
```

---

### Task 2: Portal settings

**Files:**
- Modify: `backend/app/config.py`
- Modify: `.env.example`
- Modify: `render.yaml`
- Test: `backend/tests/test_config_mode.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.AGENT_PASSWORD: str` (default `""`), `Settings.AGENT_SESSION_SECRET: str` (default `""`).

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_config_mode.py`:

```python
def test_agent_portal_settings_default_to_empty():
    s = Settings()
    assert s.AGENT_PASSWORD == ""
    assert s.AGENT_SESSION_SECRET == ""


def test_agent_portal_settings_read_env(monkeypatch):
    monkeypatch.setenv("AGENT_PASSWORD", "hunter2")
    monkeypatch.setenv("AGENT_SESSION_SECRET", "s3cret")
    s = Settings()
    assert s.AGENT_PASSWORD == "hunter2"
    assert s.AGENT_SESSION_SECRET == "s3cret"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_config_mode.py -k agent_portal -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'AGENT_PASSWORD'`

- [ ] **Step 3: Add the settings**

In `backend/app/config.py`, add after the `PIPEDRIVE_DOMAIN` line:

```python
    # Agent portal (/agent). BOTH must be set for the portal to work; with either
    # blank every /agent route refuses service, so an unconfigured deploy fails
    # closed rather than exposing visitor questions to the internet.
    AGENT_PASSWORD: str = ""
    AGENT_SESSION_SECRET: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_config_mode.py -v`
Expected: PASS

- [ ] **Step 5: Register the env vars**

In `.env.example`, append:

```
# Agent portal (/agent) — BOTH required for the portal; leave blank to disable it.
# AGENT_SESSION_SECRET signs the login cookie: use a long random string
# (python -c "import secrets;print(secrets.token_urlsafe(48))") and keep it stable,
# because changing it signs everyone out.
AGENT_PASSWORD=
AGENT_SESSION_SECRET=
```

In `render.yaml`, add under `envVars:`:

```yaml
      - key: AGENT_PASSWORD
        sync: false
      - key: AGENT_SESSION_SECRET
        sync: false
```

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/tests/test_config_mode.py .env.example render.yaml
git commit -m "feat: add agent portal settings"
```

---

### Task 3: Agent authentication

**Files:**
- Create: `backend/app/agent/__init__.py` (empty)
- Create: `backend/app/agent/auth.py`
- Test: `backend/tests/test_agent_auth.py` (create)

**Interfaces:**
- Consumes: `Settings.AGENT_PASSWORD`, `Settings.AGENT_SESSION_SECRET` (Task 2).
- Produces:
  - `SESSION_COOKIE: str = "gc_agent"`, `SESSION_TTL_SECONDS: int = 43200`, `COOKIE_PATH: str = "/agent"`
  - `portal_enabled() -> bool`
  - `password_ok(password: str) -> bool`
  - `make_token(now: float | None = None) -> str`
  - `token_valid(token: str | None, now: float | None = None) -> bool`
  - `require_agent(request: Request) -> None` — raises `HTTPException(503)` when disabled, `HTTPException(401)` when unauthenticated.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_auth.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_agent_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/agent/__init__.py` as an empty file.

Create `backend/app/agent/auth.py`:

```python
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
    return hmac.compare_digest(password or "", configured)


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_agent_auth.py -v`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/ backend/tests/test_agent_auth.py
git commit -m "feat: add agent portal authentication (stdlib signed cookie)"
```

---

### Task 4: FAQ gaps and publishing

**Files:**
- Create: `backend/app/agent/faq_gaps.py`
- Test: `backend/tests/test_faq_gaps.py` (create)

**Interfaces:**
- Consumes: `app.db.get_supabase`, `app.chat.flows._norm` (the existing normaliser).
- Produces:
  - `list_gaps(limit: int = 50) -> list[dict]` — each `{"question": str, "count": int, "ids": list[str], "last_asked": str}`, most-asked first.
  - `publish_entry(title: str, content: str, resolve_ids: list[str]) -> dict` — upserts a `managed_by='portal'` row into `kb_documents` and marks those `faq_misses` rows resolved. Raises `ValueError` on blank title or content.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_faq_gaps.py`:

```python
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from app.agent import faq_gaps


def _sb_rows(rows):
    sb = MagicMock()
    chain = sb.table.return_value.select.return_value.eq.return_value
    chain.order.return_value.limit.return_value.execute.return_value = MagicMock(data=rows)
    return sb


def test_gaps_group_the_same_question_asked_different_ways():
    rows = [
        {"id": "1", "question": "Do you ship to NY?", "created_at": "2026-08-01"},
        {"id": "2", "question": "do you ship to ny", "created_at": "2026-08-02"},
        {"id": "3", "question": "What is a refill station?", "created_at": "2026-08-03"},
    ]
    with patch("app.agent.faq_gaps.get_supabase", return_value=_sb_rows(rows)):
        gaps = faq_gaps.list_gaps()
    assert gaps[0]["count"] == 2
    assert set(gaps[0]["ids"]) == {"1", "2"}
    assert gaps[1]["count"] == 1


def test_gaps_are_ordered_most_asked_first():
    rows = [
        {"id": "1", "question": "rare question", "created_at": "2026-08-01"},
        {"id": "2", "question": "common", "created_at": "2026-08-02"},
        {"id": "3", "question": "common", "created_at": "2026-08-03"},
    ]
    with patch("app.agent.faq_gaps.get_supabase", return_value=_sb_rows(rows)):
        gaps = faq_gaps.list_gaps()
    assert gaps[0]["count"] == 2
    assert gaps[0]["question"] == "common"


def test_gaps_show_the_most_recent_wording_and_last_asked():
    rows = [
        {"id": "1", "question": "do you ship", "created_at": "2026-08-01"},
        {"id": "2", "question": "Do you ship?", "created_at": "2026-08-09"},
    ]
    with patch("app.agent.faq_gaps.get_supabase", return_value=_sb_rows(rows)):
        gaps = faq_gaps.list_gaps()
    assert gaps[0]["question"] == "Do you ship?"
    assert gaps[0]["last_asked"] == "2026-08-09"


def test_gaps_returns_empty_on_db_failure():
    sb = MagicMock()
    sb.table.side_effect = RuntimeError("supabase down")
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        assert faq_gaps.list_gaps() == []


def test_publish_writes_a_portal_owned_row():
    sb = MagicMock()
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        faq_gaps.publish_entry("Shipping", "We ship via USPS.", [])
    row = sb.table.return_value.upsert.call_args[0][0]
    assert row["managed_by"] == "portal"
    assert row["content"] == "We ship via USPS."
    assert row["embedding"] is None
    assert row["metadata"] == {"source": "portal", "title": "Shipping"}
    assert row["content_hash"] == hashlib.sha256(b"We ship via USPS.").hexdigest()


def test_publish_marks_the_gap_resolved():
    sb = MagicMock()
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        faq_gaps.publish_entry("T", "Body", ["1", "2"])
    sb.table.return_value.update.assert_called_once_with({"resolved": True})
    sb.table.return_value.update.return_value.in_.assert_called_once_with(
        "id", ["1", "2"])


def test_publish_rejects_blank_input():
    with pytest.raises(ValueError):
        faq_gaps.publish_entry("", "Body", [])
    with pytest.raises(ValueError):
        faq_gaps.publish_entry("Title", "   ", [])


def test_publish_upserts_so_a_corrected_answer_replaces_the_old_one():
    sb = MagicMock()
    with patch("app.agent.faq_gaps.get_supabase", return_value=sb):
        faq_gaps.publish_entry("T", "Body", [])
    assert sb.table.return_value.upsert.call_args.kwargs["on_conflict"] == "content_hash"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_faq_gaps.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.faq_gaps'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/agent/faq_gaps.py`:

```python
"""The FAQ backlog, and turning it into knowledge.

This is the loop that makes the portal worth having: every question the FAQ could
not answer is surfaced here, and publishing an answer puts it in the knowledge base
immediately — content_tsv is a generated column, so the next visitor to ask gets it
without a re-ingest or a redeploy.
"""
import hashlib
import logging

from app.chat.flows import _norm
from app.db import get_supabase

log = logging.getLogger(__name__)


def list_gaps(limit: int = 50) -> list[dict]:
    """Unanswered questions, grouped by wording and ordered most-asked first.

    Grouping happens here rather than in SQL so it reuses the one normalisation
    rule the conversation code already uses; a generated SQL column would split
    groups on inconsistent spacing.
    """
    try:
        resp = (get_supabase().table("faq_misses")
                .select("id,question,created_at")
                .eq("resolved", False)
                .order("created_at", desc=True)
                .limit(1000).execute())
        rows = resp.data or []
    except Exception:
        log.exception("faq_misses read failed; returning no gaps")
        return []

    grouped: dict[str, dict] = {}
    for row in rows:
        question = row.get("question") or ""
        key = _norm(question)
        if not key:
            continue
        gap = grouped.get(key)
        if gap is None:
            # Rows arrive newest-first, so the first wording seen is the latest one.
            grouped[key] = {"question": question, "count": 1,
                            "ids": [row["id"]], "last_asked": row.get("created_at")}
        else:
            gap["count"] += 1
            gap["ids"].append(row["id"])
    ordered = sorted(grouped.values(),
                     key=lambda g: (-g["count"], g["last_asked"] or ""))
    return ordered[:limit]


def publish_entry(title: str, content: str, resolve_ids: list[str]) -> dict:
    """Publish an answer to the knowledge base and clear the gap it fills.

    The row is managed_by='portal', so ingest will never delete it (see
    rag/ingest.py). embedding stays NULL: FAQ mode matches on full text.
    """
    title = (title or "").strip()
    content = (content or "").strip()
    if not title:
        raise ValueError("A title is required.")
    if not content:
        raise ValueError("An answer is required.")

    row = {
        "content": content,
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "embedding": None,
        "metadata": {"source": "portal", "title": title},
        "managed_by": "portal",
    }
    sb = get_supabase()
    # Upsert, so republishing a corrected answer replaces it instead of erroring.
    sb.table("kb_documents").upsert(row, on_conflict="content_hash").execute()
    if resolve_ids:
        try:
            sb.table("faq_misses").update({"resolved": True}).in_(
                "id", resolve_ids).execute()
        except Exception:
            # The answer is published, which is the part that matters; a gap that
            # fails to clear reappears in the list and can be dismissed again.
            log.exception("failed to mark faq_misses resolved: %s", resolve_ids)
    return row
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_faq_gaps.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/faq_gaps.py backend/tests/test_faq_gaps.py
git commit -m "feat: group FAQ gaps and publish answers to the knowledge base"
```

---

### Task 5: Agent routes

**Files:**
- Create: `backend/app/agent/router.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_agent_routes.py` (create)

**Interfaces:**
- Consumes: `app.agent.auth` (Task 3), `app.agent.faq_gaps` (Task 4).
- Produces: router mounted in `main.py` exposing `POST /agent/login`, `POST /agent/logout`, `GET /agent/faq-gaps`, `POST /agent/faq-entry`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_agent_routes.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_agent_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.agent.router'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/agent/router.py`:

```python
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
```

In `backend/app/main.py`, add after the chat router include:

```python
from app.agent.router import router as agent_router
app.include_router(agent_router)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_agent_routes.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/agent/router.py backend/app/main.py backend/tests/test_agent_routes.py
git commit -m "feat: add agent portal routes (login, FAQ gaps, publishing)"
```

---

### Task 6: The portal page

**Files:**
- Create: `portal/dist/portal.html`
- Modify: `backend/app/main.py`
- Modify: `backend/Dockerfile`
- Test: manual, plus a route test appended to `backend/tests/test_agent_routes.py`

**Interfaces:**
- Consumes: the routes from Task 5.
- Produces: `GET /agent` serving the portal HTML.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_agent_routes.py`:

```python
def test_portal_page_is_served_at_agent(client):
    r = client.get("/agent")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Generation Conscious" in r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_agent_routes.py -k portal_page -v`
Expected: FAIL — 404, the route does not exist

- [ ] **Step 3: Serve the page**

In `backend/app/main.py`, replace the widget mount block with:

```python
_widget_dir = Path(__file__).resolve().parent.parent.parent / "widget" / "dist"
if _widget_dir.exists():
    app.mount("/widget", StaticFiles(directory=str(_widget_dir)), name="widget")

# The portal is a single self-contained file, served at /agent so the session
# cookie's Path=/agent covers both the page and its API calls.
_portal_file = Path(__file__).resolve().parent.parent.parent / "portal" / "dist" / "portal.html"


@app.get("/agent", include_in_schema=False)
def agent_portal() -> FileResponse:
    return FileResponse(str(_portal_file), media_type="text/html")
```

Add `from fastapi.responses import FileResponse` to the imports at the top.

In `backend/Dockerfile`, add after the widget COPY line:

```dockerfile
# Bundle the agent portal so the backend can serve it at /agent.
COPY portal/dist /portal/dist
```

- [ ] **Step 4: Write the portal page**

Create `portal/dist/portal.html`. It is one self-contained file — no build step, no framework, no external requests (the artifact CSP equivalent here is simply that the backend serves it offline-capable).

Requirements this file must meet, all of them testable by hand:

- A login screen (password field + Sign in). On 401 show "Wrong password."; on 429 show "Too many attempts. Wait a minute."; on 503 show "The portal isn't configured yet."
- Once signed in, hide the login screen and show the **FAQ gaps** pane.
- The pane lists gaps from `GET /agent/faq-gaps`, each showing the question text and, when `count > 1`, "asked N times".
- Each gap expands to a title input, an answer textarea, and a **Publish to FAQ** button that POSTs to `/agent/faq-entry` with that gap's `ids` as `resolve_ids`, then removes the gap from the list and shows "Published — visitors get this answer now."
- A **Sign out** control that POSTs `/agent/logout` and returns to the login screen.
- Empty state: "No unanswered questions. The FAQ is keeping up."
- **All question text is rendered with `textContent`, never `innerHTML`** — it is visitor-typed and untrusted.
- All fetches use `credentials: "same-origin"`.
- A `<title>` of "Generation Conscious — Agent Portal".
- A second pane placeholder is NOT required; Plan 2 adds the Live pane.

Use the same visual language as the widget: `#FF0719` primary, system font stack, white surface, simple flat controls. Keep it under ~250 lines.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_agent_routes.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Verify by hand**

```bash
cd backend && AGENT_PASSWORD=test AGENT_SESSION_SECRET=devsecret \
  ../venv/bin/python -m uvicorn app.main:app --port 8877
```

Open `http://localhost:8877/agent`. Confirm: wrong password is rejected; the right one signs in; the gaps list renders (empty state is fine without a database); sign-out returns to login.

- [ ] **Step 7: Commit**

```bash
git add portal/dist/portal.html backend/app/main.py backend/Dockerfile backend/tests/test_agent_routes.py
git commit -m "feat: add the agent portal page with the FAQ gaps pane"
```

---

### Task 7: Documentation

**Files:**
- Modify: `README.md`
- Modify: `VERIFICATION.md`
- Modify: `LAUNCH_CHECKLIST.md`

- [ ] **Step 1: Update the README**

Add an "Agent portal" section covering: what `/agent` is, the two secrets and how to generate `AGENT_SESSION_SECRET`, and — most importantly — the **two sources of knowledge**: markdown files in `backend/knowledge_base/` are owned by ingest (`managed_by='file'`), entries published in the portal are owned by the database (`managed_by='portal'`) and survive every re-ingest. Say plainly that portal entries are edited in the portal and file entries in the repo.

- [ ] **Step 2: Update VERIFICATION.md**

Add: sign in at `/agent`; publish an answer for a gap; confirm the gap disappears; ask the bot that question and confirm the new answer comes back; **re-run `python -m app.rag.ingest` and confirm the published entry still exists** (the regression this plan exists to prevent).

- [ ] **Step 3: Update LAUNCH_CHECKLIST.md**

Add to the Mode & Database section: `schema.sql` re-applied for `managed_by` and `resolved`. Add a new "Agent portal" section: `AGENT_PASSWORD` and `AGENT_SESSION_SECRET` set in production, `/agent` reachable over HTTPS, and a wrong password rejected.

- [ ] **Step 4: Run the full suite and commit**

Run: `cd backend && python -m pytest tests/ -q`
Expected: all pass

```bash
git add README.md VERIFICATION.md LAUNCH_CHECKLIST.md
git commit -m "docs: document the agent portal and knowledge ownership"
```

---

## Definition of Done

- [ ] `cd backend && python -m pytest tests/ -q` — all pass, baseline of 161 not regressed.
- [ ] `grep -rn "innerHTML" portal/dist/portal.html` returns nothing.
- [ ] `grep -rniE "openrouter|openai|embed" backend/app/agent/` returns nothing.
- [ ] With both agent secrets unset, `GET /agent/faq-gaps` returns 503 and the rest of the app is unaffected.
- [ ] Signing in, publishing an answer, and seeing the gap clear all work by hand.
- [ ] A published entry survives `python -m app.rag.ingest`.
