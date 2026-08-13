# Genco Chatbot — FAQ-Match Mode (No-AI Pivot)

**Date:** 2026-08-12
**Status:** Approved
**Supersedes:** the generative chat flow of `2026-06-18-genco-chatbot-design.md` as the *default*
mode. The generative mode is retained behind a config flag; everything else in the original design
(lead pipeline, escalation email, widget shell, deploy topology) carries forward unchanged.

## Motivation

Generation Conscious sells to schools, and schools are frequently anti-AI. The client (Greg)
requested a pivot, verbatim goals from his message:

1. "Intelligence that can read the question and then select a best fit answer from the FAQs" —
   matching, not generation.
2. Ask the customer "did it answer your question?" — and if no, send the question to
   `Info@GenerationConscious.co` where Greg personally responds.
3. "This can also help us add to the FAQ so it's even more robust" — unanswered questions become
   the FAQ backlog.

The honest sales claim this design supports: **the bot never writes its own text — it only shows
answers the GC team wrote, word for word.** No AI service is called in this mode at all.

## Decisions (made with the owner, 2026-08-12)

| Decision | Choice |
|---|---|
| Matching engine | **Zero-AI Postgres full-text search** in Supabase (no OpenAI, no OpenRouter, no embeddings) |
| Existing generative mode | **Kept behind a config flag** (`BOT_MODE=generative`), off by default |
| Lead collection UX | **Guided chat steps** — one field at a time, scripted, reusing existing validation |
| Live agent portal | **Out of scope for v1** — the "No" path emails Greg; he replies by email |

## Architecture

```
Widget (vanilla JS + quick-reply buttons) ──POST /chat──> FastAPI backend
                                                            ├─ BOT_MODE=faq (default)
                                                            │    ├─ rag/fts      Postgres full-text match (verbatim answers)
                                                            │    ├─ chat/flows   deterministic state machine (feedback + lead steps)
                                                            │    └─ leads        UNCHANGED: Supabase → Resend → Pipedrive
                                                            └─ BOT_MODE=generative
                                                                 └─ the existing OpenRouter RAG pipeline, untouched
```

`POST /chat` and `GET /history` keep their frozen shapes. `POST /chat` gains one **additive**
field: `quick_replies: list[str]` (possibly empty) that the widget renders as tappable buttons;
tapping a button sends its text as a normal user message.

## Components

### 1. Mode flag

- `Settings.bot_mode: str = "faq"` (`faq` | `generative`), from `BOT_MODE` env var.
- `chat/router.py` branches once, at the top of the turn, after the guard gates.
- AI-related settings (`OPENROUTER_*`, `EMBEDDING_*`, `LANGFUSE_*`, `DAILY_COST_CAP_USD`) become
  **optional with empty/default values**. Generative mode validates their presence at startup and
  fails fast with a clear message; FAQ mode never reads them.
- Guard gates in FAQ mode: rate limiting stays exactly as is (before any DB access). The
  injection guard and cost cap are skipped — there is no model to protect and no spend to cap.

### 2. Full-text matching (`rag/fts.py` + schema)

- `kb_documents` gains a generated `content_tsv tsvector` column
  (`to_tsvector('english', content)`) with a GIN index.
- New SQL function `match_documents_fts(query_text text, match_count int)` using
  `websearch_to_tsquery('english', query_text)`, returning `content`, `metadata`, and
  `ts_rank` score, ordered by rank.
- `retrieve_fts(query: str, k: int) -> list[dict]` mirrors the embeddings `retrieve()` shape;
  the response's `retrieval_scores` carries the ranks (floats — contract shape preserved).
- **Match threshold:** `FTS_MIN_RANK` (default `0.05`, tunable in code). Below it → the no-match
  path (offer to send the question to the team) instead of showing a weak answer.
- **Answers are verbatim:** the reply is the best-matching chunk's content, unmodified. KB
  authoring rule (already true of the current files): every chunk must read as a complete,
  standalone answer.
- Ingest in FAQ mode makes **no embedding calls**: chunks are upserted with `embedding = NULL`
  (column becomes nullable). The non-destructive order from the 2026-08 hardening (build first,
  upsert, then prune stale) is preserved. Ingest with `BOT_MODE=generative` behaves as today.

### 3. Conversation state machine (`chat/flows.py`)

`chat_sessions` gains `flow_state jsonb` (nullable). States:

- **idle** — a typed message runs FTS matching. Good match → verbatim answer +
  `quick_replies: ["👍 Yes, that answered it", "✉️ No — ask the team"]`, state →
  `awaiting_feedback` with the original question stored. Weak/no match → "I couldn't find that
  in our FAQ" + `quick_replies: ["✉️ Send my question to the team"]`; tapping it enters the
  question lead flow with the question prefilled.
- **awaiting_feedback** — "Yes" → short thanks + reset to idle. "No" → question lead flow with
  the stored question prefilled. Any other typed text → treated as a new question (idle
  handling); no feedback row is written (only explicit Yes/No taps are recorded).
- **lead:{intent}** — one field at a time in `REQUIRED_FIELDS[intent]` order, prompts built from
  `FIELD_LABELS` (both already the single source of truth). Per-field validation on collection
  (email regex, integer fields must parse); invalid input → friendly re-prompt, stay on the
  field. The word "cancel" exits any flow. On completion → the **existing** `capture_lead`
  (store-first → Resend → Pipedrive, unchanged) and a confirmation that Greg's team will reply
  (question intent: "usually the same day").

Flow triggers:

- Greeting buttons (unchanged text): "Buy Sheets" → static reply with the product-page URL;
  "Buy Refill Stations" → `lead:refill_station`; "Question for the team" → `lead:question`
  (asks for the question first, then name, then email).
- Wholesale: when the best-matching chunk comes from `wholesale.md`, the answer carries a
  `quick_replies: ["Start wholesale inquiry"]` trigger → `lead:wholesale`. The
  source-file → trigger mapping lives in one code dict.

### 4. FAQ-gap capture (`faq_misses` table)

Greg's stated goal is a growing FAQ. Escalated questions already land as `question` leads, but a
user who taps "No" and then abandons before leaving contact info would otherwise be lost. A
minimal `faq_misses` table records every feedback event: `question text`, `top_rank float`,
`answered boolean` (Yes/No tap), `created_at`. No PII. This is the FAQ backlog view; reading it
is a Supabase table view, no UI in v1.

### 5. Widget

Additive changes only, keeping the single-IIFE structure and XSS-safe `textContent` rendering:

- Render `quick_replies` from any `/chat` response as buttons under the bot message; tapping
  sends the text as a user message (the mechanism the greeting already uses).
- The `?stub=1` offline stub gains FAQ-mode canned responses (answer + feedback buttons + lead
  steps) so the full flow is testable with zero keys.

### 6. Observability

No LangFuse in FAQ mode (its keys stay unset; `trace_turn` already no-ops without keys — no code
change). Operational visibility = the `faq_misses` table plus the existing `leads` flags.

## Error handling

- FTS query/DB failure mid-turn → same never-crash rule: log, reply with the contact-info
  fallback, HTTP 200.
- `flow_state` JSON that fails to parse or names an unknown state → reset to idle, log, answer
  the message as a fresh question (never trap a user in a broken flow).
- All existing hardening (UUID session guard, guard-gates-before-DB, store-first leads) applies
  identically — FAQ mode routes through the same entry code.

## Schema changes (in `backend/app/rag/schema.sql`, idempotent)

1. `alter table kb_documents alter column embedding drop not null` (if currently not null).
2. `alter table kb_documents add column if not exists content_tsv tsvector generated always as (to_tsvector('english', content)) stored` + GIN index.
3. `create or replace function match_documents_fts(...)`.
4. `alter table chat_sessions add column if not exists flow_state jsonb`.
5. `create table if not exists faq_misses (...)`.

Re-applying the whole file stays safe (existing pattern). The operator applies it once in the
Supabase SQL editor; re-running after this change upgrades an existing project in place.

## Testing (TDD, per CLAUDE.md)

- `rag/fts`: mocked Supabase RPC — ranking order, threshold behavior, verbatim pass-through.
- `chat/flows`: every state transition per intent — happy paths, invalid field re-prompts,
  cancel, unknown/corrupt state reset, feedback Yes/No, wholesale trigger mapping.
- Router (FAQ mode): contract test — `{session_id, reply, retrieval_scores, quick_replies}` on
  every path, HTTP 200 always; mode-flag branch test (generative untouched, its tests keep
  passing).
- `faq_misses`: written on Yes and No, no PII stored.
- Widget stub: canned FAQ conversation incl. feedback buttons and a full guided lead flow.
- Eval: `run_eval.py --mock` gains a FAQ-mode fixture set (keyword checks against verbatim KB
  answers; escalate cases expect the no-match offer).

## Docs impact (in scope)

- README: FAQ mode is the default story; the go-live key list shrinks to **Supabase, Resend,
  Pipedrive** (plus WP embed). OpenRouter/OpenAI/LangFuse move to a "generative mode only"
  subsection. Cost section: FAQ mode ≈ $0.
- VERIFICATION: FAQ-mode checks (match quality spot-check against the seeded KB, feedback →
  email round-trip, guided lead flow end-to-end); generative checks marked mode-conditional.
- LAUNCH_CHECKLIST: add FAQ feedback round-trip; drop AI-billing items from the critical path.

## Out of scope (v1)

- Live agent chat portal (phase 2 if volume justifies; the email reply loop covers it now).
- Merging the website's `/faqs/` page (university/grant-oriented content) into the KB — content
  task for later, flows through the normal KB-update loop.
- Removing generative-mode code (deliberately retained).
- Any WordPress site changes (embed happens at deploy time via Elementor Pro Custom Code).
