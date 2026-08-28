# FAQ-Match Mode Implementation Plan

**Status:** ✅ Completed 2026-08-27. Test suite went 108 → 161 passing. Shipped in commits `f128f46`..`280c224`.

> Per-step `git commit` blocks below were executed as granular per-file commits by the
> orchestrator rather than run verbatim; every other step ran as written.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Genco chatbot answer from the FAQ knowledge base with **zero AI calls** — Postgres full-text match returns verbatim GC-written answers, asks "did that answer your question?", and routes a "No" to the team as a lead.

**Architecture:** A `BOT_MODE` flag (`faq` default, `generative` retained) branches once at the top of the chat turn. FAQ mode replaces embeddings+LLM with a Postgres `tsvector` match (`rag/fts.py`) and a deterministic state machine (`chat/flows.py`) persisted in `chat_sessions.flow_state`. The lead pipeline (Supabase → Resend → Pipedrive), rate limiting, widget shell, and deploy topology are reused unchanged.

**Tech Stack:** Python 3.11+ / FastAPI, pydantic-settings, Supabase Postgres (full-text search, no pgvector reads in FAQ mode), pytest + pytest-asyncio + respx, vanilla-JS single-file widget.

**Spec:** `docs/superpowers/specs/2026-08-12-faq-match-mode-design.md`

## Global Constraints

- **No AI calls in FAQ mode.** `chat/flows.py` and `rag/fts.py` must never import or call `app.llm`, `app.rag.embeddings`, or OpenRouter/OpenAI. The honest sales claim is: *the bot never writes its own text — it only shows answers the GC team wrote, word for word.*
- **Answers are verbatim.** A matched reply is the KB chunk's `content`, unmodified — no summarizing, truncating, or prefixing.
- **Frozen response contract.** `POST /chat` returns `{session_id, reply, retrieval_scores, quick_replies}` on **every** path, always HTTP 200. `quick_replies` is a `list[str]`, present (possibly empty) on generative responses too.
- **Never crash a turn.** Every external call is wrapped; failures log and return the contact fallback with HTTP 200.
- **Leads must never be lost.** Store-first ordering in `escalation.capture_lead` is unchanged and reused as-is.
- **Typed Python 3.11+**: full annotations, `list[str]` not `List[str]`.
- **TDD**: write the failing test, confirm it fails, then implement.
- **Single source of truth** for lead fields stays `chat/tools.py` — `REQUIRED_FIELDS` and `FIELD_LABELS`. Do not redefine either.
- **Contact string** used in fallbacks, verbatim: `Info@GenerationConscious.co or text (516) 619-6174`.
- **Product URL** verbatim: `https://generationconscious.co/product/laundry-detergent-sheets/`
- **Baseline to protect:** 108 passed / 2 skipped. Generative-mode tests must keep passing untouched.

---

### Task 1: Mode flag and database schema

**Files:**
- Modify: `backend/app/config.py`
- Modify: `backend/app/rag/schema.sql`
- Modify: `.env.example`
- Modify: `render.yaml`
- Test: `backend/tests/test_config_mode.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings.BOT_MODE: str` (default `"faq"`); SQL objects `kb_documents.content_tsv`, `match_documents_fts(query_text text, match_count int)`, `chat_sessions.flow_state jsonb`, table `faq_misses`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_config_mode.py`:

```python
from app.config import Settings


def test_bot_mode_defaults_to_faq():
    assert Settings().BOT_MODE == "faq"


def test_bot_mode_reads_env(monkeypatch):
    monkeypatch.setenv("BOT_MODE", "generative")
    assert Settings().BOT_MODE == "generative"


def test_faq_mode_needs_no_ai_keys():
    # FAQ mode must construct cleanly with every AI key blank.
    s = Settings(OPENROUTER_API_KEY="", EMBEDDING_API_KEY="", LANGFUSE_PUBLIC_KEY="")
    assert s.BOT_MODE == "faq"
    assert s.OPENROUTER_API_KEY == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_config_mode.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'BOT_MODE'`

- [ ] **Step 3: Add the setting**

In `backend/app/config.py`, add immediately after the `model_config` line:

```python
    # "faq" (default) = zero-AI Postgres full-text matching, verbatim KB answers.
    # "generative" = the original OpenRouter RAG pipeline, retained behind this flag.
    # FAQ mode never reads the OPENROUTER_*/EMBEDDING_*/LANGFUSE_* settings below.
    BOT_MODE: str = "faq"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_config_mode.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Extend the schema**

Append to `backend/app/rag/schema.sql` (the whole file must stay re-runnable):

```sql
-- ── FAQ-match mode (BOT_MODE=faq) ─────────────────────────────────────────
-- In FAQ mode chunks are stored with no embedding, so the column must be nullable.
alter table kb_documents alter column embedding drop not null;

-- Generated tsvector + GIN index: the zero-AI matching path.
alter table kb_documents add column if not exists content_tsv tsvector
  generated always as (to_tsvector('english', content)) stored;
create index if not exists kb_documents_content_tsv_idx
  on kb_documents using gin (content_tsv);

-- Mirrors match_documents' return shape (score column named `similarity`) so the
-- Python retrieval helpers and the frozen retrieval_scores contract stay identical.
create or replace function match_documents_fts(
  query_text text,
  match_count int default 5
)
returns table (id uuid, content text, metadata jsonb, similarity float)
language sql stable as $$
  select id, content, metadata,
         ts_rank(content_tsv, websearch_to_tsquery('english', query_text))::float
           as similarity
  from kb_documents
  where content_tsv @@ websearch_to_tsquery('english', query_text)
  order by ts_rank(content_tsv, websearch_to_tsquery('english', query_text)) desc
  limit match_count;
$$;

-- Per-session position in the deterministic FAQ state machine (null = idle).
alter table chat_sessions add column if not exists flow_state jsonb;

-- The FAQ backlog: one row per feedback event. No PII — question text and rank only.
create table if not exists faq_misses (
  id uuid primary key default gen_random_uuid(),
  question text not null,
  top_rank float,
  answered boolean default false,
  created_at timestamptz default now()
);
create index if not exists faq_misses_created_idx on faq_misses(created_at desc);
```

- [ ] **Step 6: Register the env var**

In `.env.example`, add above the `# OpenRouter (chat generation)` line:

```
# Bot mode: faq (default, zero-AI verbatim FAQ matching) | generative (OpenRouter RAG)
# In faq mode every OPENROUTER_*/EMBEDDING_*/LANGFUSE_* value below may stay blank.
BOT_MODE=faq
```

In `render.yaml`, add as the first entry under `envVars:`:

```yaml
      - key: BOT_MODE
        sync: false
```

- [ ] **Step 7: Run the full suite and commit**

Run: `cd backend && python -m pytest tests/ -q`
Expected: 111 passed, 2 skipped

```bash
git add backend/app/config.py backend/app/rag/schema.sql backend/tests/test_config_mode.py .env.example render.yaml
git commit -m "feat: add BOT_MODE flag and FAQ-match schema (tsvector, flow_state, faq_misses)"
```

---

### Task 2: Zero-AI full-text retrieval

**Files:**
- Create: `backend/app/rag/fts.py`
- Test: `backend/tests/test_fts.py` (create)

**Interfaces:**
- Consumes: `app.db.get_supabase`; SQL function `match_documents_fts` from Task 1.
- Produces:
  - `FTS_MIN_RANK: float = 0.05`
  - `retrieve_fts(query: str, k: int = 5) -> list[dict]` — rows shaped `{id, content, metadata, similarity}`, best first.
  - `best_match(query: str, k: int = 5) -> tuple[dict | None, list[float]]` — `(top hit at/above FTS_MIN_RANK else None, all ranks)`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_fts.py`:

```python
from unittest.mock import MagicMock, patch

from app.rag import fts


def _mock_sb(rows):
    sb = MagicMock()
    sb.rpc.return_value.execute.return_value = MagicMock(data=rows)
    return sb


def test_retrieve_fts_calls_the_fts_rpc_and_returns_rows():
    rows = [{"id": "1", "content": "We ship via USPS.", "metadata": {}, "similarity": 0.4}]
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(rows)) as sb:
        out = fts.retrieve_fts("do you ship", k=3)
    assert out == rows
    sb.return_value.rpc.assert_called_once_with(
        "match_documents_fts", {"query_text": "do you ship", "match_count": 3})


def test_retrieve_fts_returns_empty_list_when_no_rows():
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(None)):
        assert fts.retrieve_fts("zzz") == []


def test_best_match_returns_top_hit_above_threshold():
    rows = [{"content": "strong", "metadata": {}, "similarity": 0.9},
            {"content": "weak", "metadata": {}, "similarity": 0.01}]
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(rows)):
        hit, scores = fts.best_match("q")
    assert hit["content"] == "strong"
    assert scores == [0.9, 0.01]


def test_best_match_returns_none_below_threshold():
    rows = [{"content": "weak", "metadata": {}, "similarity": 0.001}]
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(rows)):
        hit, scores = fts.best_match("q")
    assert hit is None
    assert scores == [0.001]


def test_best_match_returns_none_on_no_rows():
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb([])):
        hit, scores = fts.best_match("q")
    assert hit is None
    assert scores == []


def test_best_match_answers_are_verbatim():
    content = "Shipping is calculated at checkout using live USPS rates."
    rows = [{"content": content, "metadata": {}, "similarity": 0.5}]
    with patch("app.rag.fts.get_supabase", return_value=_mock_sb(rows)):
        hit, _ = fts.best_match("shipping")
    assert hit["content"] == content


def test_fts_module_makes_no_ai_calls():
    src = (__import__("pathlib").Path(fts.__file__)).read_text()
    for banned in ("embeddings", "llm", "openrouter", "openai"):
        assert banned not in src.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_fts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.rag.fts'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/rag/fts.py`:

```python
"""Zero-AI retrieval: Postgres full-text matching over the KB.

This is the FAQ-mode counterpart to `rag.retrieve` and deliberately imports no
embedding or model code — BOT_MODE=faq must reach no AI service at all.
"""
import logging

from app.db import get_supabase

logger = logging.getLogger(__name__)

# Below this ts_rank a hit is too weak to show as an answer: the caller takes the
# no-match path (offer to send the question to the team) rather than risk showing
# a confidently wrong FAQ entry. Tuned against the seeded KB; raise it if weak
# matches start slipping through, lower it if good questions fall to escalation.
FTS_MIN_RANK = 0.05


def retrieve_fts(query: str, k: int = 5) -> list[dict]:
    """Full-text match against kb_documents.

    Mirrors `rag.retrieve.retrieve()`'s row shape — {id, content, metadata,
    similarity} — so the frozen retrieval_scores contract is unchanged. Rows come
    back best-match-first; an unmatchable query yields an empty list.
    """
    resp = get_supabase().rpc("match_documents_fts", {
        "query_text": query,
        "match_count": k,
    }).execute()
    return resp.data or []


def best_match(query: str, k: int = 5) -> tuple[dict | None, list[float]]:
    """Return (best hit at/above FTS_MIN_RANK else None, every rank retrieved)."""
    hits = retrieve_fts(query, k=k)
    scores = [float(h.get("similarity") or 0.0) for h in hits]
    if hits and scores[0] >= FTS_MIN_RANK:
        return hits[0], scores
    return None, scores
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_fts.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/fts.py backend/tests/test_fts.py
git commit -m "feat: add zero-AI full-text retrieval (rag/fts.py)"
```

---

### Task 3: Flow-state persistence

**Files:**
- Modify: `backend/app/chat/memory.py`
- Test: `backend/tests/test_memory.py` (extend existing file)

**Interfaces:**
- Consumes: `chat_sessions.flow_state` from Task 1; the existing private `_is_uuid` helper.
- Produces: `get_flow_state(session_id: str) -> dict | None`, `set_flow_state(session_id: str, state: dict | None) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_memory.py`:

```python
from unittest.mock import MagicMock, patch

from app.chat import memory as mem


def _sb_with_select(rows):
    sb = MagicMock()
    sb.table.return_value.select.return_value.eq.return_value.execute.return_value = (
        MagicMock(data=rows))
    return sb


def test_get_flow_state_returns_the_stored_dict():
    state = {"state": "awaiting_feedback", "question": "do you ship"}
    with patch("app.chat.memory.get_supabase",
               return_value=_sb_with_select([{"flow_state": state}])):
        assert mem.get_flow_state("11111111-1111-1111-1111-111111111111") == state


def test_get_flow_state_returns_none_for_idle_session():
    with patch("app.chat.memory.get_supabase",
               return_value=_sb_with_select([{"flow_state": None}])):
        assert mem.get_flow_state("11111111-1111-1111-1111-111111111111") is None


def test_get_flow_state_returns_none_for_unknown_session():
    with patch("app.chat.memory.get_supabase", return_value=_sb_with_select([])):
        assert mem.get_flow_state("11111111-1111-1111-1111-111111111111") is None


def test_get_flow_state_returns_none_for_non_uuid_without_touching_db():
    with patch("app.chat.memory.get_supabase") as sb:
        assert mem.get_flow_state("not-a-uuid") is None
    sb.assert_not_called()


def test_get_flow_state_returns_none_for_corrupt_non_dict_value():
    with patch("app.chat.memory.get_supabase",
               return_value=_sb_with_select([{"flow_state": "garbage"}])):
        assert mem.get_flow_state("11111111-1111-1111-1111-111111111111") is None


def test_set_flow_state_writes_the_state():
    sb = MagicMock()
    with patch("app.chat.memory.get_supabase", return_value=sb):
        mem.set_flow_state("11111111-1111-1111-1111-111111111111", {"state": "lead"})
    sb.table.return_value.update.assert_called_once_with({"flow_state": {"state": "lead"}})


def test_set_flow_state_clears_with_none():
    sb = MagicMock()
    with patch("app.chat.memory.get_supabase", return_value=sb):
        mem.set_flow_state("11111111-1111-1111-1111-111111111111", None)
    sb.table.return_value.update.assert_called_once_with({"flow_state": None})


def test_set_flow_state_ignores_non_uuid():
    with patch("app.chat.memory.get_supabase") as sb:
        mem.set_flow_state("not-a-uuid", {"state": "lead"})
    sb.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_memory.py -v`
Expected: FAIL — `AttributeError: module 'app.chat.memory' has no attribute 'get_flow_state'`

- [ ] **Step 3: Write the implementation**

Append to `backend/app/chat/memory.py`:

```python
def get_flow_state(session_id: str) -> dict | None:
    """The session's position in the FAQ state machine, or None when idle.

    A corrupt/non-dict value reads as None so a bad row can never trap a visitor
    in a broken flow — the turn is then answered as a fresh question.
    """
    if not _is_uuid(session_id):
        return None
    resp = (get_supabase().table("chat_sessions")
            .select("flow_state").eq("id", session_id).execute())
    if not resp.data:
        return None
    state = resp.data[0].get("flow_state")
    return state if isinstance(state, dict) else None


def set_flow_state(session_id: str, state: dict | None) -> None:
    """Persist the next flow state; None resets the session to idle."""
    if not _is_uuid(session_id):
        return
    (get_supabase().table("chat_sessions")
     .update({"flow_state": state}).eq("id", session_id).execute())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_memory.py -v`
Expected: PASS (existing tests + 8 new)

- [ ] **Step 5: Commit**

```bash
git add backend/app/chat/memory.py backend/tests/test_memory.py
git commit -m "feat: persist FAQ flow state on chat_sessions"
```

---

### Task 4: FAQ-gap recording

**Files:**
- Create: `backend/app/faq_misses.py`
- Test: `backend/tests/test_faq_misses.py` (create)

**Interfaces:**
- Consumes: table `faq_misses` from Task 1.
- Produces: `record_feedback(question: str, top_rank: float, answered: bool) -> None` — never raises.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_faq_misses.py`:

```python
from unittest.mock import MagicMock, patch

from app import faq_misses


def test_records_an_answered_event():
    sb = MagicMock()
    with patch("app.faq_misses.get_supabase", return_value=sb):
        faq_misses.record_feedback("do you ship to NY", 0.42, answered=True)
    sb.table.assert_called_once_with("faq_misses")
    sb.table.return_value.insert.assert_called_once_with(
        {"question": "do you ship to NY", "top_rank": 0.42, "answered": True})


def test_records_a_miss_event():
    sb = MagicMock()
    with patch("app.faq_misses.get_supabase", return_value=sb):
        faq_misses.record_feedback("do you sell dog food", 0.0, answered=False)
    sb.table.return_value.insert.assert_called_once_with(
        {"question": "do you sell dog food", "top_rank": 0.0, "answered": False})


def test_stores_no_pii_fields():
    sb = MagicMock()
    with patch("app.faq_misses.get_supabase", return_value=sb):
        faq_misses.record_feedback("q", 0.1, answered=False)
    row = sb.table.return_value.insert.call_args[0][0]
    assert set(row) == {"question", "top_rank", "answered"}


def test_never_raises_when_the_insert_fails():
    sb = MagicMock()
    sb.table.return_value.insert.side_effect = RuntimeError("supabase down")
    with patch("app.faq_misses.get_supabase", return_value=sb):
        faq_misses.record_feedback("q", 0.1, answered=False)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_faq_misses.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.faq_misses'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/faq_misses.py`:

```python
"""The FAQ backlog: one row per feedback event.

Escalated questions already become `question` leads, but a visitor who taps "No"
and then abandons before leaving contact details would otherwise be lost. This
table captures every hit and miss so the team can see what the FAQ is missing.
No PII is stored — the question text and match rank only.
"""
import logging

from app.db import get_supabase

log = logging.getLogger(__name__)


def record_feedback(question: str, top_rank: float, answered: bool) -> None:
    """Record one FAQ hit (answered=True) or miss (answered=False).

    Best-effort by design: this is analytics, and a logging failure must never
    break the visitor's turn.
    """
    try:
        get_supabase().table("faq_misses").insert({
            "question": question,
            "top_rank": top_rank,
            "answered": answered,
        }).execute()
    except Exception:
        log.exception("faq_misses insert failed (answered=%s, rank=%s)",
                      answered, top_rank)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_faq_misses.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/faq_misses.py backend/tests/test_faq_misses.py
git commit -m "feat: record FAQ hits and misses for the team's FAQ backlog"
```

---

### Task 5: The deterministic conversation state machine

**Files:**
- Create: `backend/app/chat/flows.py`
- Test: `backend/tests/test_flows.py` (create)

**Interfaces:**
- Consumes: `app.rag.fts.best_match`, `app.faq_misses.record_feedback`, `app.escalation.capture_lead`, `app.chat.tools.REQUIRED_FIELDS`, `app.chat.tools.FIELD_LABELS`.
- Produces:
  - `handle_turn(session_id: str, message: str, state: dict | None) -> tuple[str, list[str], dict | None, list[float]]` returning `(reply, quick_replies, next_flow_state, retrieval_scores)`.
  - Button-label constants `FEEDBACK_YES`, `FEEDBACK_NO`, `SEND_TO_TEAM`, `WHOLESALE_START`, `BUY_SHEETS`, `BUY_REFILL`, `ASK_TEAM`.
  - Flow-state shapes: `{"state": "awaiting_feedback", "question": str, "top_rank": float, "matched": bool}` and `{"state": "lead", "intent": str, "fields": dict}`. `None` means idle.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_flows.py`:

```python
from unittest.mock import patch

import pytest

from app.chat import flows

SID = "11111111-1111-1111-1111-111111111111"


def _hit(content="Shipping is live USPS at checkout.", source="shipping_and_tax.md",
         rank=0.5):
    return {"content": content, "metadata": {"source": source}, "similarity": rank}


@pytest.fixture
def no_db():
    """Neutralise every external call; individual tests re-patch what they assert on."""
    with patch("app.chat.flows.best_match", return_value=(None, [])) as bm, \
         patch("app.chat.flows.faq_misses.record_feedback") as rec, \
         patch("app.chat.flows.capture_lead", return_value={"id": "lead-1"}) as cap:
        yield {"best_match": bm, "record": rec, "capture": cap}


# ── idle: matching ────────────────────────────────────────────────────────
def test_good_match_answers_verbatim_with_feedback_buttons(no_db):
    content = "Shipping is calculated at checkout using live USPS rates."
    no_db["best_match"].return_value = (_hit(content), [0.5])
    reply, qr, state, scores = flows.handle_turn(SID, "do you ship?", None)
    assert reply == content
    assert qr == [flows.FEEDBACK_YES, flows.FEEDBACK_NO]
    assert state == {"state": "awaiting_feedback", "question": "do you ship?",
                     "top_rank": 0.5, "matched": True}
    assert scores == [0.5]


def test_wholesale_source_adds_the_wholesale_trigger_button(no_db):
    no_db["best_match"].return_value = (_hit(source="wholesale.md"), [0.5])
    _, qr, _, _ = flows.handle_turn(SID, "bulk pricing?", None)
    assert qr == [flows.FEEDBACK_YES, flows.FEEDBACK_NO, flows.WHOLESALE_START]


def test_no_match_offers_to_send_the_question_and_records_the_gap(no_db):
    no_db["best_match"].return_value = (None, [0.001])
    reply, qr, state, _ = flows.handle_turn(SID, "do you sell dog food", None)
    assert "couldn't find that" in reply
    assert qr == [flows.SEND_TO_TEAM]
    assert state["matched"] is False
    assert state["question"] == "do you sell dog food"
    no_db["record"].assert_called_once_with("do you sell dog food", 0.001,
                                            answered=False)


# ── feedback ──────────────────────────────────────────────────────────────
def test_yes_thanks_resets_to_idle_and_records_a_hit(no_db):
    state = {"state": "awaiting_feedback", "question": "q", "top_rank": 0.5,
             "matched": True}
    reply, qr, new_state, _ = flows.handle_turn(SID, flows.FEEDBACK_YES, state)
    assert "Glad that helped" in reply
    assert qr == []
    assert new_state is None
    no_db["record"].assert_called_once_with("q", 0.5, answered=True)


def test_no_starts_the_question_lead_flow_with_the_question_prefilled(no_db):
    state = {"state": "awaiting_feedback", "question": "how do refills work",
             "top_rank": 0.5, "matched": True}
    reply, _, new_state, _ = flows.handle_turn(SID, flows.FEEDBACK_NO, state)
    assert new_state["state"] == "lead"
    assert new_state["intent"] == "question"
    assert new_state["fields"]["question"] == "how do refills work"
    # question is prefilled, so the first prompt is for the name
    assert "name" in reply.lower()
    no_db["record"].assert_called_once_with("how do refills work", 0.5,
                                            answered=False)


def test_send_to_team_from_a_no_match_does_not_double_record(no_db):
    state = {"state": "awaiting_feedback", "question": "dog food", "top_rank": 0.0,
             "matched": False}
    _, _, new_state, _ = flows.handle_turn(SID, flows.SEND_TO_TEAM, state)
    assert new_state["fields"]["question"] == "dog food"
    no_db["record"].assert_not_called()


def test_other_text_during_feedback_is_treated_as_a_new_question(no_db):
    no_db["best_match"].return_value = (_hit("Refill answer."), [0.6])
    state = {"state": "awaiting_feedback", "question": "old", "top_rank": 0.5,
             "matched": True}
    reply, qr, new_state, _ = flows.handle_turn(SID, "how do refills work", state)
    assert reply == "Refill answer."
    assert new_state["question"] == "how do refills work"
    # no feedback row for text that was not an explicit Yes/No tap
    assert not any(c.kwargs.get("answered") is True
                   for c in no_db["record"].call_args_list)


# ── greeting buttons ──────────────────────────────────────────────────────
def test_buy_sheets_returns_the_product_url(no_db):
    reply, qr, state, _ = flows.handle_turn(SID, flows.BUY_SHEETS, None)
    assert "https://generationconscious.co/product/laundry-detergent-sheets/" in reply
    assert state is None


def test_buy_refill_stations_starts_the_refill_lead_flow(no_db):
    reply, _, state, _ = flows.handle_turn(SID, flows.BUY_REFILL, None)
    assert state == {"state": "lead", "intent": "refill_station", "fields": {}}
    assert "name" in reply.lower()


def test_question_for_the_team_asks_for_the_question_first(no_db):
    reply, _, state, _ = flows.handle_turn(SID, flows.ASK_TEAM, None)
    assert state["intent"] == "question"
    assert "question" in reply.lower()


# ── guided lead flow ──────────────────────────────────────────────────────
def test_lead_flow_collects_one_field_at_a_time_in_order(no_db):
    state = {"state": "lead", "intent": "wholesale", "fields": {}}
    reply, _, state, _ = flows.handle_turn(SID, "Ada Lovelace", state)
    assert state["fields"] == {"name": "Ada Lovelace"}
    assert "email" in reply.lower()
    reply, _, state, _ = flows.handle_turn(SID, "ada@example.com", state)
    assert state["fields"]["email"] == "ada@example.com"
    assert "phone" in reply.lower()


def test_invalid_email_re_prompts_and_stays_on_the_field(no_db):
    state = {"state": "lead", "intent": "wholesale", "fields": {"name": "Ada"}}
    reply, _, new_state, _ = flows.handle_turn(SID, "not-an-email", state)
    assert "email" in reply.lower()
    assert "email" not in new_state["fields"]


def test_non_numeric_count_re_prompts(no_db):
    state = {"state": "lead", "intent": "wholesale",
             "fields": {"name": "A", "email": "a@b.co", "phone": "555",
                        "organization": "Org"}}
    reply, _, new_state, _ = flows.handle_turn(SID, "lots of them", state)
    assert "number" in reply.lower()
    assert "estimated_sheets" not in new_state["fields"]


def test_completed_lead_is_captured_and_resets_to_idle(no_db):
    state = {"state": "lead", "intent": "wholesale",
             "fields": {"name": "A", "email": "a@b.co", "phone": "555",
                        "organization": "Org"}}
    reply, qr, new_state, _ = flows.handle_turn(SID, "500", state)
    no_db["capture"].assert_called_once()
    args = no_db["capture"].call_args[0]
    assert args[0] == SID and args[1] == "wholesale"
    assert args[2]["estimated_sheets"] == 500     # coerced to int
    assert new_state is None
    assert "Thanks" in reply


def test_capture_failure_offers_the_human_path_and_resets(no_db):
    no_db["capture"].side_effect = RuntimeError("supabase down")
    state = {"state": "lead", "intent": "question",
             "fields": {"question": "q", "name": "A"}}
    reply, _, new_state, _ = flows.handle_turn(SID, "a@b.co", state)
    assert "Info@GenerationConscious.co" in reply
    assert new_state is None


# ── cancel and corrupt state ──────────────────────────────────────────────
def test_cancel_exits_any_flow(no_db):
    state = {"state": "lead", "intent": "wholesale", "fields": {"name": "A"}}
    reply, qr, new_state, _ = flows.handle_turn(SID, "cancel", state)
    assert new_state is None
    assert qr == [flows.BUY_SHEETS, flows.BUY_REFILL, flows.ASK_TEAM]
    assert "cancel" in reply.lower()


def test_unknown_state_resets_to_idle_and_answers_the_message(no_db):
    no_db["best_match"].return_value = (_hit("An answer."), [0.6])
    reply, _, new_state, _ = flows.handle_turn(SID, "do you ship", {"state": "bogus"})
    assert reply == "An answer."
    assert new_state["state"] == "awaiting_feedback"


def test_lead_state_with_unknown_intent_resets_to_idle(no_db):
    no_db["best_match"].return_value = (_hit("An answer."), [0.6])
    reply, _, new_state, _ = flows.handle_turn(
        SID, "do you ship", {"state": "lead", "intent": "nope", "fields": {}})
    assert reply == "An answer."
    assert new_state["state"] == "awaiting_feedback"


def test_button_matching_ignores_emoji_and_punctuation(no_db):
    state = {"state": "awaiting_feedback", "question": "q", "top_rank": 0.5,
             "matched": True}
    # same words, no emoji / different dash
    reply, _, new_state, _ = flows.handle_turn(SID, "Yes, that answered it", state)
    assert new_state is None


def test_flows_module_makes_no_ai_calls():
    src = (__import__("pathlib").Path(flows.__file__)).read_text()
    for banned in ("app.llm", "embeddings", "openrouter", "openai"):
        assert banned not in src.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_flows.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.chat.flows'`

- [ ] **Step 3: Write the implementation**

Create `backend/app/chat/flows.py`:

```python
"""The deterministic FAQ-mode conversation state machine.

No model is ever called from this module: matched answers are verbatim KB chunks
and every other reply is a fixed string defined here. That is what makes the
sales claim honest — the bot only shows text the GC team wrote.

Flow states (persisted as chat_sessions.flow_state; None means idle):
  {"state": "awaiting_feedback", "question": str, "top_rank": float,
   "matched": bool}
  {"state": "lead", "intent": str, "fields": dict}
"""
import logging
import re

from app import faq_misses
from app.chat.tools import FIELD_LABELS, REQUIRED_FIELDS
from app.escalation import capture_lead
from app.rag.fts import best_match

log = logging.getLogger(__name__)

# ── Button labels (the widget sends a tap as a normal user message) ────────
FEEDBACK_YES = "\U0001F44D Yes, that answered it"
FEEDBACK_NO = "✉️ No — ask the team"
SEND_TO_TEAM = "✉️ Send my question to the team"
WHOLESALE_START = "Start wholesale inquiry"
BUY_SHEETS = "Buy Sheets"
BUY_REFILL = "Buy Refill Stations"
ASK_TEAM = "Question for the team"

PRODUCT_URL = "https://generationconscious.co/product/laundry-detergent-sheets/"
CONTACT = "Info@GenerationConscious.co or text (516) 619-6174"

# KB source file -> extra quick-reply buttons offered with its answers.
# Single source of truth for answer-driven flow triggers.
_SOURCE_TRIGGERS: dict[str, list[str]] = {
    "wholesale.md": [WHOLESALE_START],
}

_BUY_SHEETS_REPLY = (
    "Great — you can choose your sheet count, scent, and one-time or "
    f"subscription here: {PRODUCT_URL}"
)
_NO_MATCH_REPLY = (
    "I couldn't find that in our FAQ — but our team can answer it personally."
)
_THANKS_REPLY = "Glad that helped! Anything else I can look up for you?"
_CANCEL_REPLY = "No problem — that's cancelled. What else can I help you with?"
_LEAD_DONE = {
    "question": ("Thanks — I've sent your question to our team. They usually "
                 "reply the same day."),
    "wholesale": ("Thanks — I've passed your wholesale inquiry to our team. "
                  "They respond within 24 hours (usually ~15 minutes)."),
    "refill_station": ("Thanks — I've passed your refill-station details to our "
                       "team. They respond within 24 hours (usually ~15 minutes)."),
}
_LEAD_FALLBACK = (
    "I wasn't able to submit your details just now, but our team still wants to "
    f"hear from you — please email {CONTACT} and they'll take care of you."
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_INT_FIELDS = ("estimated_sheets", "num_laundry_rooms", "num_students")
_NORM_RE = re.compile(r"[^a-z0-9 ]+")

# Field prompts are built from FIELD_LABELS (the single source of truth); only
# labels that don't read well in the generic template are overridden here.
_FIELD_PROMPTS = {
    "question": "Sure — what's your question for the team?",
}


def _norm(text: str) -> str:
    """Compare button taps by their words alone.

    Emoji, variation selectors, and dash characters differ between platforms and
    between the widget and a hand-typed reply; matching on letters and digits
    keeps a tap recognisable either way.
    """
    return " ".join(_NORM_RE.sub(" ", (text or "").lower()).split())


def _greeting_buttons() -> list[str]:
    return [BUY_SHEETS, BUY_REFILL, ASK_TEAM]


def _prompt_for(field: str) -> str:
    if field in _FIELD_PROMPTS:
        return _FIELD_PROMPTS[field]
    return f"What's {FIELD_LABELS.get(field, field.replace('_', ' '))}?"


def _validate_field(field: str, value: str) -> tuple[bool, str]:
    """(ok, re-prompt). Mirrors tools.validate_lead's rules, one field at a time."""
    value = value.strip()
    if not value:
        return False, f"I didn't catch that. {_prompt_for(field)}"
    if field == "email" and not _EMAIL_RE.match(value):
        return False, "That email doesn't look right — could you type it again?"
    if field in _INT_FIELDS and not re.fullmatch(r"\d[\d,]*", value.replace(" ", "")):
        return False, (f"Please give {FIELD_LABELS[field]} as a number "
                       "(digits only, for example 500).")
    return True, ""


def handle_turn(session_id: str, message: str,
                state: dict | None) -> tuple[str, list[str], dict | None, list[float]]:
    """Run one FAQ-mode turn.

    Returns (reply, quick_replies, next_flow_state, retrieval_scores). An unknown
    or corrupt state resets to idle rather than trapping the visitor.
    """
    text = message.strip()
    norm = _norm(text)
    if norm == "cancel":
        return _CANCEL_REPLY, _greeting_buttons(), None, []

    name = (state or {}).get("state")
    if name == "awaiting_feedback":
        return _handle_feedback(session_id, state or {}, text, norm)
    if name == "lead":
        return _handle_lead_step(session_id, state or {}, text)
    if name is not None:
        log.warning("Unknown flow state %r — resetting to idle.", name)
    return _handle_idle(session_id, text, norm)


def _handle_idle(session_id: str, text: str,
                 norm: str) -> tuple[str, list[str], dict | None, list[float]]:
    if norm == _norm(BUY_SHEETS):
        return _BUY_SHEETS_REPLY, [], None, []
    for label, intent in ((BUY_REFILL, "refill_station"),
                          (ASK_TEAM, "question"),
                          (WHOLESALE_START, "wholesale"),
                          (SEND_TO_TEAM, "question")):
        if norm == _norm(label):
            return _start_lead(session_id, intent, {})

    hit, scores = best_match(text)
    top = scores[0] if scores else 0.0
    if hit is None:
        # Every unanswerable question is a FAQ gap worth recording, even if the
        # visitor abandons before leaving contact details.
        faq_misses.record_feedback(text, top, answered=False)
        return (_NO_MATCH_REPLY, [SEND_TO_TEAM],
                {"state": "awaiting_feedback", "question": text,
                 "top_rank": top, "matched": False}, scores)
    source = (hit.get("metadata") or {}).get("source") or ""
    buttons = [FEEDBACK_YES, FEEDBACK_NO] + _SOURCE_TRIGGERS.get(source, [])
    return (hit["content"], buttons,
            {"state": "awaiting_feedback", "question": text,
             "top_rank": top, "matched": True}, scores)


def _handle_feedback(session_id: str, state: dict, text: str,
                     norm: str) -> tuple[str, list[str], dict | None, list[float]]:
    question = state.get("question") or ""
    top = float(state.get("top_rank") or 0.0)
    matched = bool(state.get("matched"))

    if matched and norm == _norm(FEEDBACK_YES):
        faq_misses.record_feedback(question, top, answered=True)
        return _THANKS_REPLY, [], None, []
    if norm in (_norm(FEEDBACK_NO), _norm(SEND_TO_TEAM)):
        # A no-match was already recorded when it happened; don't double-count.
        if matched:
            faq_misses.record_feedback(question, top, answered=False)
        prefill = {"question": question} if question else {}
        return _start_lead(session_id, "question", prefill)
    if norm == _norm(WHOLESALE_START):
        return _start_lead(session_id, "wholesale", {})
    # Only explicit Yes/No taps are recorded as feedback; anything else typed is
    # simply a new question.
    return _handle_idle(session_id, text, norm)


def _start_lead(session_id: str, intent: str,
                fields: dict) -> tuple[str, list[str], dict | None, list[float]]:
    return _ask_next(session_id,
                     {"state": "lead", "intent": intent, "fields": dict(fields)})


def _ask_next(session_id: str,
              state: dict) -> tuple[str, list[str], dict | None, list[float]]:
    """Prompt for the next missing field, or submit once all are collected.

    The next field is derived from REQUIRED_FIELDS rather than stored, so a
    partially-written state can never point at the wrong field.
    """
    missing = [f for f in REQUIRED_FIELDS[state["intent"]]
               if not state["fields"].get(f)]
    if missing:
        return _prompt_for(missing[0]), [], state, []
    return _submit(session_id, state)


def _submit(session_id: str,
            state: dict) -> tuple[str, list[str], dict | None, list[float]]:
    intent = state["intent"]
    payload = dict(state["fields"])
    for field in _INT_FIELDS:
        if field in payload:
            payload[field] = int(str(payload[field]).replace(",", "").strip())
    try:
        capture_lead(session_id, intent, payload)
    except Exception:
        # A fully-collected lead exists at this exact moment: log the payload so
        # it stays recoverable, then offer the human path. Never lose a lead.
        log.exception("capture_lead failed in FAQ flow (lead recoverable): "
                      "intent=%s fields=%s", intent, payload)
        return _LEAD_FALLBACK, [], None, []
    return _LEAD_DONE[intent], [], None, []


def _handle_lead_step(session_id: str, state: dict,
                      text: str) -> tuple[str, list[str], dict | None, list[float]]:
    intent = state.get("intent")
    if intent not in REQUIRED_FIELDS:
        log.warning("Lead flow with unknown intent %r — resetting to idle.", intent)
        return _handle_idle(session_id, text, _norm(text))
    fields = dict(state.get("fields") or {})
    missing = [f for f in REQUIRED_FIELDS[intent] if not fields.get(f)]
    if not missing:
        return _submit(session_id,
                       {"state": "lead", "intent": intent, "fields": fields})
    field = missing[0]
    ok, reprompt = _validate_field(field, text)
    if not ok:
        return reprompt, [], state, []
    fields[field] = text.strip()
    return _ask_next(session_id,
                     {"state": "lead", "intent": intent, "fields": fields})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_flows.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/chat/flows.py backend/tests/test_flows.py
git commit -m "feat: add deterministic FAQ-mode conversation state machine"
```

---

### Task 6: Router branch and the `quick_replies` contract

**Files:**
- Modify: `backend/app/chat/router.py`
- Test: `backend/tests/test_chat_router_faq.py` (create)
- Test: `backend/tests/test_chat_router.py` (extend — assert `quick_replies` present on generative paths)
- Test: `backend/tests/test_widget_contract.py` (extend — `quick_replies` in the frozen shape)

**Interfaces:**
- Consumes: `flows.handle_turn` (Task 5), `memory.get_flow_state` / `memory.set_flow_state` (Task 3), `Settings.BOT_MODE` (Task 1).
- Produces: `POST /chat` returning `{session_id, reply, retrieval_scores, quick_replies}` on every path.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_chat_router_faq.py`:

```python
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

SID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
def faq_client(monkeypatch):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "faq")
    import importlib
    from app.chat import router as router_mod
    importlib.reload(router_mod)
    import app.main as main_mod
    importlib.reload(main_mod)
    with patch("app.chat.router.memory.get_or_create_session", return_value=SID), \
         patch("app.chat.router.memory.save_message"), \
         patch("app.chat.router.memory.get_flow_state", return_value=None), \
         patch("app.chat.router.memory.set_flow_state") as set_state:
        yield TestClient(main_mod.app), set_state
    get_settings.cache_clear()


def test_faq_turn_returns_the_frozen_contract(faq_client):
    client, _ = faq_client
    with patch("app.chat.router.flows.handle_turn",
               return_value=("An answer.", ["A", "B"], {"state": "x"}, [0.5])):
        r = client.post("/chat", json={"session_id": SID, "message": "hi"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"session_id", "reply", "retrieval_scores", "quick_replies"}
    assert body["reply"] == "An answer."
    assert body["quick_replies"] == ["A", "B"]
    assert body["retrieval_scores"] == [0.5]


def test_faq_turn_persists_the_next_flow_state(faq_client):
    client, set_state = faq_client
    with patch("app.chat.router.flows.handle_turn",
               return_value=("r", [], {"state": "lead"}, [])):
        client.post("/chat", json={"session_id": SID, "message": "hi"})
    set_state.assert_called_once_with(SID, {"state": "lead"})


def test_faq_mode_calls_no_model(faq_client):
    client, _ = faq_client
    with patch("app.chat.router.flows.handle_turn",
               return_value=("r", [], None, [])), \
         patch("app.llm.chat_completion") as llm_call:
        client.post("/chat", json={"session_id": SID, "message": "hi"})
    llm_call.assert_not_called()


def test_flow_failure_returns_contact_fallback_with_200(faq_client):
    client, _ = faq_client
    with patch("app.chat.router.flows.handle_turn",
               side_effect=RuntimeError("supabase down")):
        r = client.post("/chat", json={"session_id": SID, "message": "hi"})
    assert r.status_code == 200
    assert "Info@GenerationConscious.co" in r.json()["reply"]
    assert r.json()["quick_replies"] == []


def test_rate_limit_still_applies_in_faq_mode(faq_client):
    client, _ = faq_client
    with patch("app.chat.router._rate_limiter.allow", return_value=False):
        r = client.post("/chat", json={"session_id": SID, "message": "hi"})
    assert r.status_code == 200
    assert "quickly" in r.json()["reply"]
    assert r.json()["quick_replies"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_chat_router_faq.py -v`
Expected: FAIL — `AttributeError: module 'app.chat.router' has no attribute 'flows'`

- [ ] **Step 3: Wire the branch into the router**

In `backend/app/chat/router.py`, add to the imports:

```python
from app.chat import flows, memory, prompts
```

(replacing the existing `from app.chat import memory, prompts`)

Add this constant next to the other reply constants:

```python
# FAQ mode never calls a model, so its only failure is the database. Same
# never-crash rule: log, offer the human path, keep the 200 contract.
_FAQ_FALLBACK_REPLY = (
    "I'm having trouble looking that up right now. Please email "
    "Info@GenerationConscious.co or text (516) 619-6174 and the team will help."
)
```

Add the FAQ turn handler above the `@router.post("/chat")` function:

```python
def _faq_turn(req: "ChatRequest") -> dict:
    """One zero-AI turn: full-text match + deterministic flows, no model call."""
    session_id = memory.get_or_create_session(req.session_id)
    memory.save_message(session_id, "user", req.message)
    try:
        state = memory.get_flow_state(session_id)
        reply, quick_replies, next_state, scores = flows.handle_turn(
            session_id, req.message, state)
        memory.set_flow_state(session_id, next_state)
    except Exception:
        logger.exception("FAQ turn failed; returning the contact fallback.")
        reply, quick_replies, scores = _FAQ_FALLBACK_REPLY, [], []
    memory.save_message(session_id, "assistant", reply)
    return {"session_id": session_id, "reply": reply,
            "retrieval_scores": scores, "quick_replies": quick_replies}
```

Then edit the `chat()` function body. Replace the three guard-gate returns and add the branch so the top of the function reads:

```python
@router.post("/chat")
def chat(req: ChatRequest, request: Request) -> dict:
    # Guard gates run BEFORE any Supabase call: throttled/capped/declined requests must not
    # create chat_sessions rows (or cause any other DB load). They echo the client-supplied
    # session_id (or "") since no session is looked up or minted on these paths.
    echo_id = req.session_id or ""
    # Rate-limit by client IP, not the browser-supplied session_id (which a client can rotate/omit
    # to mint a fresh bucket every request). Applies in BOTH modes.
    if not _rate_limiter.allow(_client_ip(request)):
        return {"session_id": echo_id,
                "reply": "You're sending messages quickly — give me a moment and try again.",
                "retrieval_scores": [], "quick_replies": []}
    # FAQ mode branches here: no model to protect from injection, no spend to cap.
    if _settings.BOT_MODE == "faq":
        return _faq_turn(req)
    if _cost.exceeded():
        logger.warning(
            "Daily cost cap exceeded; returning static unavailable message "
            "(fallback model NOT invoked).")
        return {"session_id": echo_id,
                "reply": "I'm momentarily unavailable. Please email Info@GenerationConscious.co and the team will help.",
                "retrieval_scores": [], "quick_replies": []}
    # Substring guard (always on, cheap) + optional ML scanner (LLM Guard) when installed.
    if guardrails.is_injection_attempt(req.message) or injection_scanner.is_injection(req.message):
        return {"session_id": echo_id,
                "reply": "I can only help with Generation Conscious products and orders. How can I help with that?",
                "retrieval_scores": [], "quick_replies": []}
```

Finally, add `"quick_replies": []` to the two remaining generative `return` statements (the double-model-failure return and the final success return), so every path returns the same four keys.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_chat_router_faq.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Extend the generative contract tests**

In `backend/tests/test_chat_router.py` and `backend/tests/test_widget_contract.py`, update every assertion on the response key set to include `quick_replies`, and add to `test_widget_contract.py`:

```python
def test_quick_replies_is_always_a_list_in_the_response(...):
    # (mirror the file's existing fixture/patch style)
    body = response.json()
    assert "quick_replies" in body
    assert isinstance(body["quick_replies"], list)
```

- [ ] **Step 6: Run the full suite and commit**

Run: `cd backend && python -m pytest tests/ -q`
Expected: all pass, no generative regressions

```bash
git add backend/app/chat/router.py backend/tests/
git commit -m "feat: branch chat router on BOT_MODE and add quick_replies to the contract"
```

---

### Task 7: Embedding-free ingest in FAQ mode

**Files:**
- Modify: `backend/app/rag/ingest.py`
- Test: `backend/tests/test_ingest.py` (extend)

**Interfaces:**
- Consumes: `Settings.BOT_MODE` (Task 1).
- Produces: `ingest_all()` upserting `embedding=None` rows in FAQ mode, unchanged behavior in generative mode.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_ingest.py`:

```python
def test_faq_mode_ingest_makes_no_embedding_calls(monkeypatch, tmp_path):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "faq")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("# Shipping\nWe ship via USPS.")
    monkeypatch.setattr("app.rag.ingest.KB_DIR", kb)
    sb = MagicMock()
    with patch("app.rag.ingest.get_supabase", return_value=sb), \
         patch("app.rag.ingest.embed_batch") as embed:
        count = ingest.ingest_all()
    embed.assert_not_called()
    assert count == 1
    rows = sb.table.return_value.upsert.call_args[0][0]
    assert all(r["embedding"] is None for r in rows)
    get_settings.cache_clear()


def test_generative_mode_ingest_still_embeds(monkeypatch, tmp_path):
    from app.config import get_settings
    get_settings.cache_clear()
    monkeypatch.setenv("BOT_MODE", "generative")
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "a.md").write_text("# Shipping\nWe ship via USPS.")
    monkeypatch.setattr("app.rag.ingest.KB_DIR", kb)
    with patch("app.rag.ingest.get_supabase"), \
         patch("app.rag.ingest.embed_batch", return_value=[[0.1] * 1536]) as embed:
        ingest.ingest_all()
    embed.assert_called_once()
    get_settings.cache_clear()
```

(Match the existing file's imports — add `from unittest.mock import MagicMock, patch` and `from app.rag import ingest` if not already present.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_ingest.py -v`
Expected: FAIL — `embed.assert_not_called()` fails; embeddings are still requested

- [ ] **Step 3: Write the implementation**

In `backend/app/rag/ingest.py`, add to the imports:

```python
from app.config import get_settings
```

Replace the single `vectors = embed_batch(...)` line with:

```python
    # FAQ mode matches with Postgres full-text search and reaches no AI service at
    # all, so chunks are stored with a NULL embedding. Generative mode embeds
    # BEFORE any database write — if that raises, the old KB keeps serving.
    if get_settings().BOT_MODE == "faq":
        vectors: list[list[float] | None] = [None] * len(all_chunks)
    else:
        vectors = embed_batch([c["content"] for c in all_chunks])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_ingest.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/rag/ingest.py backend/tests/test_ingest.py
git commit -m "feat: skip embedding calls during ingest in FAQ mode"
```

---

### Task 8: Widget quick-reply rendering and the offline stub

**Files:**
- Modify: `widget/dist/widget.js`
- Modify: `widget/stub_server.py`
- Test: manual via `widget/test.html?stub=1`

**Interfaces:**
- Consumes: the `quick_replies` field from Task 6.
- Produces: buttons rendered under any bot message that carries them.

- [ ] **Step 1: Extract the quick-reply renderer**

In `widget/dist/widget.js`, replace the `greet()` function with a reusable renderer plus a greeting that uses it:

```javascript
  /* Buttons under a bot message. Labels are set with textContent (never
   * innerHTML) so server-supplied text can't inject markup. Tapping sends the
   * label as a normal user message — the same mechanism the greeting uses. */
  function quickReplies(labels) {
    if (!labels || !labels.length) return;
    var qr = document.createElement("div"); qr.className = "gc-qr";
    labels.forEach(function (label) {
      var b = document.createElement("button"); b.textContent = label;
      b.onclick = function () { qr.remove(); send(label); };
      qr.appendChild(b);
    });
    msgs.appendChild(qr); msgs.scrollTop = msgs.scrollHeight;
  }
  function greet() {
    bubble("bot", "How can we support your sustainability journey?");
    quickReplies(["Buy Sheets", "Buy Refill Stations", "Question for the team"]);
  }
```

- [ ] **Step 2: Render server-sent quick replies**

In the same file, inside `send()`'s `.then(function (data) {...})`, after the `bubble("bot", ...)` line, add:

```javascript
      quickReplies(data.quick_replies);
```

- [ ] **Step 3: Add FAQ responses to the offline stub**

In `widget/stub_server.py`, make the `/chat` handler return `quick_replies` and drive a canned FAQ conversation: a matched answer with `["👍 Yes, that answered it", "✉️ No — ask the team"]`, a "No" tap that starts the guided question flow (question → name → email), and a final confirmation. Every stub response must include all four contract keys (`session_id`, `reply`, `retrieval_scores`, `quick_replies`). Follow the file's existing handler style.

- [ ] **Step 4: Verify manually**

Run: `cd widget && python stub_server.py` then open `http://localhost:5500/test.html?stub=1`
Expected: greeting buttons appear; typing a question returns an answer with 👍/✉️ buttons; tapping ✉️ walks the guided question flow one field at a time and ends with a confirmation.

- [ ] **Step 5: Commit**

```bash
git add widget/dist/widget.js widget/stub_server.py
git commit -m "feat: render server-sent quick replies in the widget and stub FAQ mode"
```

---

### Task 9: FAQ-mode evaluation fixtures

**Files:**
- Modify: `eval/run_eval.py`
- Test: run the eval itself

**Interfaces:**
- Consumes: FAQ-mode `POST /chat` responses.
- Produces: a FAQ fixture set runnable with `python eval/run_eval.py --mock`.

- [ ] **Step 1: Add the fixtures**

In `eval/run_eval.py`, add a FAQ-mode fixture set following the file's existing case structure. Each case is a question plus expected keyword(s) checked against the verbatim KB answer:

- `"how much is shipping"` → expects `USPS` / `checkout`
- `"do you charge sales tax"` → expects `New York`
- `"how do I buy sheets"` → expects `product/laundry-detergent-sheets`
- `"do you do bulk orders"` → expects wholesale keywords
- `"how do refill stations work"` → expects refill-station keywords
- `"do you sell dog food"` → escalate case: expects the no-match offer (`couldn't find`)

- [ ] **Step 2: Run the eval**

Run: `python eval/run_eval.py --mock`
Expected: all FAQ cases pass

- [ ] **Step 3: Commit**

```bash
git add eval/run_eval.py
git commit -m "test: add FAQ-mode eval fixtures"
```

---

### Task 10: Documentation and launch checklist

**Files:**
- Modify: `README.md`
- Modify: `VERIFICATION.md`
- Modify: `LAUNCH_CHECKLIST.md`

**Interfaces:**
- Consumes: everything above.
- Produces: docs that describe FAQ mode as the default.

- [ ] **Step 1: Update the README**

- Lead with FAQ mode as the default and state the claim plainly: **no AI service is called; answers are the GC team's own words, verbatim.**
- Shrink the go-live key list to **Supabase, Resend, Pipedrive** (plus the WordPress embed).
- Move `OPENROUTER_*`, `EMBEDDING_*`, `LANGFUSE_*`, and `DAILY_COST_CAP_USD` into a "generative mode only" subsection.
- Add `BOT_MODE` to the env table (default `faq`).
- Cost section: FAQ mode ≈ **$0/month** in AI spend.
- Document the `faq_misses` table as the FAQ backlog view.

- [ ] **Step 2: Update VERIFICATION.md**

Add FAQ-mode checks (match-quality spot-check against the seeded KB, feedback → email round-trip, guided lead flow end-to-end) and mark the existing generative checks (LangFuse tracing, cost cap, fallback model) as mode-conditional.

- [ ] **Step 3: Update LAUNCH_CHECKLIST.md**

- Add: `BOT_MODE=faq` set in production.
- Add: `schema.sql` re-applied in the Supabase SQL editor (adds `content_tsv`, `match_documents_fts`, `flow_state`, `faq_misses`).
- Add: KB re-ingested after the schema change (`python -m app.rag.ingest`).
- Add: FAQ feedback round-trip verified (tap "No" → lead lands in inbox + Pipedrive).
- Move the AI-billing items (OpenRouter/OpenAI accounts, `DAILY_COST_CAP_USD`) and the LangFuse item off the critical path into a "generative mode only" section.

- [ ] **Step 4: Run the full suite and commit**

Run: `cd backend && python -m pytest tests/ -q`
Expected: all pass

```bash
git add README.md VERIFICATION.md LAUNCH_CHECKLIST.md
git commit -m "docs: document FAQ-match mode as the default"
```

---

## Definition of Done

- [ ] `cd backend && python -m pytest tests/ -q` — all pass, no generative regressions.
- [ ] `grep -ri "openrouter\|openai\|embed" backend/app/chat/flows.py backend/app/rag/fts.py` returns nothing.
- [ ] With `BOT_MODE=faq` and every AI key blank, the app boots and answers a question.
- [ ] `POST /chat` returns all four contract keys on every path, always HTTP 200.
- [ ] `widget/test.html?stub=1` walks the full flow: greeting → answer → feedback → guided lead → confirmation.
