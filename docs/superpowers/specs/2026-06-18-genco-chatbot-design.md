# Genco Intel Chatbot — Build Design

**Date:** 2026-06-18
**Status:** Approved
**Source spec:** `claude_code_build_prompts (1).md` (7 sequenced build prompts + KB/behavior block)

## Overview

A standalone RAG (retrieval-augmented generation) chatbot for **Generation Conscious**, a
sustainable laundry-detergent-sheet company. The bot is embedded on their WordPress site via a
single `<script>` tag but runs as an independent service — it does not run inside WordPress.

The bot answers **only** from a curated knowledge base, greets users with three options
(Buy Sheets / Buy Refill Stations / Question for the team), captures leads for wholesale and
refill-station inquiries, and escalates anything it can't ground in the KB to the team.

## Architecture

```
Widget (vanilla JS, single file) ──POST /chat──> FastAPI backend
                                                   ├─ rag/         Supabase pgvector + embeddings + ingestion + retrieval
                                                   ├─ chat/        router, prompts, conversation memory
                                                   ├─ llm.py       OpenRouter chat completions (+ fallback model)
                                                   ├─ leads        escalation detection → Resend email + Pipedrive CRM
                                                   ├─ guardrails    prompt-injection resistance, on-topic, rate limit, cost cap
                                                   └─ observability LangFuse tracing of every turn
```

**Key separations of concern:**
- **Generation** goes through OpenRouter (`OPENROUTER_API_KEY`, with `OPENROUTER_MODEL_FALLBACK`).
- **Embeddings** use OpenAI `text-embedding-3-small` (1536-dim) via a separate `EMBEDDING_API_KEY`.
- **Retrieval** is Supabase Postgres + pgvector, queried through a `match_documents` SQL function.
- **Leads** always land in two places: email to `Info@GenerationConscious.co` (Resend) and Pipedrive.

## Components (the 7 build prompts)

| # | Component | Depends on | Key deliverables |
|---|-----------|-----------|------------------|
| 1 | Scaffold + CLAUDE.md + config | — | Dir structure, `requirements.txt`, `main.py` (CORS + `/health`), `config.py` (typed Settings), `.env.example`, `.gitignore`, `CLAUDE.md`, `README.md` |
| 2 | RAG layer | P1 | `schema.sql` (vector ext, `kb_documents`, `match_documents`, ivfflat index), `embeddings.py`, `ingest.py` (CLI), `retrieve.py`, seeded `knowledge_base/*.md`, `test_retrieval.py` |
| 3 | Chat backend | P2 | `chat_sessions`/`chat_messages` tables, `memory.py`, `prompts.py` (persona + rules), `llm.py`, `observability.py`, `chat/router.py` (`POST /chat` + **`GET /history?session_id=`** for widget rehydration) |
| 4 | Escalation + lead capture | P3 | `leads` table, `email_service.py` (Resend), `pipedrive.py`, `escalation.py` (`should_escalate`, `capture_lead`), router wiring using **structured tool-calling** for field collection (see Reliability) |
| 5 | Embeddable widget | P3 (API contract only) | `widget/dist/widget.js` (launcher + panel, responsive, localStorage session, **repaints prior messages via `GET /history` on load**), quick-reply greeting, `widget/test.html` |
| 6 | Guardrails + safety + eval | P4 | `guardrails.py` (injection/on-topic/consent), rate limiting (`RATE_LIMIT_PER_MINUTE`), cost cap (`DAILY_COST_CAP_USD`), `eval/test_set.jsonl` (~25 cases), `eval/run_eval.py` |
| 7 | Deploy + handoff | all | `Dockerfile`, Render config, production widget hosting + WordPress embed snippet, handoff `README.md`, `LAUNCH_CHECKLIST.md` |

## Execution Plan

Build **sequentially in prompt order**, parallelizing only the genuinely independent work.
After each prompt: check acceptance criteria, then commit with the exact message the prompt specifies.

1. **P1** — solo. Immediately after, paste the "Knowledge base & conversation behavior" block from
   the source spec into the `CLAUDE.md` placeholder so all later work inherits the bot's source of truth.
2. **P2** — solo (needs P1 scaffold + CLAUDE.md).
3. **P3** — solo (needs P2 retrieval). This fixes the `/chat` request/response contract.
4. **Parallel wave** — after P3, run **P4 (leads)** and **P5 (widget)** as two concurrent subagents.
   P5 only needs the frozen `/chat` contract; P4 extends `chat/router.py`. No file overlap:
   P4 owns backend lead modules + `router.py`, P5 stays entirely in `widget/`.
   **P4's router changes must be additive** — it may add fields to the `/chat` response and add lead
   sub-flows, but must not change or remove the `{session_id, reply, retrieval_scores}` shape or the
   `GET /history` contract P5 is building against.
5. **P6** — solo (needs P4 chat flow).
6. **P7** — solo (needs everything).

## Testing Strategy (no live keys)

Building to spec with `.env.example` placeholders; no external services are wired yet. Acceptance
criteria are split:

**Verifiable offline (must pass before each commit):**
- Scaffold runs: `uvicorn app.main:app` boots, `GET /health` returns `{"status":"ok"}`.
- All imports resolve; typed config loads from env.
- Unit tests with **mocked** external I/O (OpenRouter, embeddings, Supabase, Resend, Pipedrive, LangFuse).
- Widget renders and runs a conversation against a **stubbed** backend in `widget/test.html`.
- Eval harness executes against mocks and reports pass/fail.

**Deferred to live verification (captured in `VERIFICATION.md` + P7's `LAUNCH_CHECKLIST.md`):**
- Real KB ingestion populating `kb_documents` with sensible similarity scores.
- Real LLM-grounded replies reflecting the seeded KB.
- End-to-end email to `Info@GenerationConscious.co`, real Pipedrive person+deal, LangFuse traces.
- Rate-limit and cost-cap fallbacks under real load.

## Reliability — Lead Capture (the bot's whole point)

**Structured capture, not free-form parsing.** The model does not hand back prose for us to regex.
Instead `capture_lead` is defined as an OpenRouter **tool/function call** with typed parameters:
`name`, `email`, `phone`, `organization`, `intent`, plus intent-specific extras
(`estimated_sheets` for wholesale; `num_laundry_rooms`, `num_students` for refill stations;
`question` for team questions). The model emits the tool call only when it has the fields; we
**validate server-side before storing** — required-field presence per intent and **email format**
(reject/re-prompt on invalid). This turns "usually captures" into "reliably captures."

**Leads must never be lost. Order is store-first, then notify:**
1. Insert the validated lead into Supabase `leads` with `emailed=false`, `pushed_to_pipedrive=false`.
2. Attempt Resend email → set `emailed=true` on success.
3. Attempt Pipedrive person+deal → set `pushed_to_pipedrive=true` on success.
4. If **either** notification fails, the row still persists in Supabase with its failure flag unset,
   ready for retry. Failures are logged and never crash the chat turn.

A `GET /history?session_id=` endpoint returns the stored messages so the widget can repaint the
visible conversation on reload — `session_id` in localStorage alone only persists the ID, not what
the user saw.

## Data Retention & PII

We store PII (names, emails, phones in `leads`; conversation text in `chat_messages`). The plan
defines a **retention window** (default: leads kept indefinitely as business records; `chat_messages`
purged after a configurable window, e.g. 90 days) and documents it in the handoff guide.
**Embedding dimension (1536) is fixed in the schema** — switching embedding models later requires a
schema migration + full re-ingest; this is noted in the handoff doc.

## Deferred / Fast-Follow (noted, not blocking v1)

- **Token streaming:** if it's not much extra over the OpenRouter call, stream the reply (SSE) so the
  widget feels dramatically faster. If it adds meaningful complexity, ship non-streaming v1 and flag
  streaming as the first fast-follow.
- **Manual feedback loop:** "add approved escalated questions back to the KB" is, for v1, a manual
  process — edit a markdown file in `knowledge_base/`, re-run `python -m app.rag.ingest`. No automated
  approval tooling. The handoff doc sets this expectation explicitly.

## Conventions

- Typed Python 3.11+, `pip` + `requirements.txt`, small focused modules.
- No secrets in code; everything sensitive in git-ignored `.env`.
- Every external call wrapped for errors (tenacity retry where the spec calls for it) and LangFuse-traced.
- Commit messages use the exact strings each prompt specifies.

## Bot Behavior (source of truth — verbatim from spec)

**Greeting:** "How can we support your sustainability journey?" with three options:
Buy Sheets · Buy Refill Stations · Question for the team.

**Flows:**
- **Home delivery:** guide to online store / checkout. **The real store/product URL must be supplied**
  (placeholder `STORE_URL` in the KB until provided — the bot links to it instead of deflecting). Without
  it the bot cannot actually send buyers to purchase.
- **Wholesale:** collect Name, Email, Phone, Organization, Estimated total sheet purchase → lead to `Info@GenerationConscious.co` (or email/text (516) 619-6174).
- **Refill stations:** collect Name, Email, Phone, Organization, Total Number of Laundry Rooms and Students (Tenants) → lead.
- **Learn more:** Lifecycle Assessment (Drive link), Workforce Development Case Study (email/text with contact info), Mission (redirect to website), Refill Station example (Instagram link).
- **Submit a question:** capture question + contact; team responds within 24h (avg ~15 min).

**Shipping & tax (now live — true facts the bot may state):** shipping is calculated at checkout
using live USPS rates; sales tax applies to New York orders only. The bot may share these facts and
direct users to checkout for the exact calculated amounts. It still must not quote specific dollar
figures it hasn't been given.

**Lead destinations:** all leads → `Info@GenerationConscious.co` **and** Pipedrive.
**Contact:** `Info@GenerationConscious.co` · text (516) 619-6174.
**Hard rule:** never invent prices, product specs, or policies — direct such questions to checkout or
the team. (Shipping = live USPS at checkout; tax = NY orders only, per above — these are now known facts,
not things to invent.)
