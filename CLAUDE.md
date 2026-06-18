# Genco Intel Chatbot — CLAUDE.md

## Project Overview

A standalone RAG (retrieval-augmented generation) chatbot for **Generation Conscious**, a
sustainable laundry-detergent-sheet company. The bot is embedded on their WordPress site via a
single `<script>` tag but runs as an independent FastAPI service — it does not run inside WordPress.

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
                                                   ├─ guardrails   prompt-injection resistance, on-topic, rate limit, cost cap
                                                   └─ observability LangFuse tracing of every turn
```

Key separations of concern:
- **Generation** goes through OpenRouter (`OPENROUTER_API_KEY`, with `OPENROUTER_MODEL_FALLBACK`).
- **Embeddings** use OpenAI `text-embedding-3-small` (1536-dim) via a separate `EMBEDDING_API_KEY`.
- **Retrieval** is Supabase Postgres + pgvector, queried through a `match_documents` SQL function.
- **Leads** always land in two places: email to `Info@GenerationConscious.co` (Resend) and Pipedrive.

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend framework | FastAPI (Python 3.11+) |
| Config | pydantic-settings (`Settings` class, `.env`) |
| Vector store | Supabase + pgvector (`match_documents` RPC) |
| Embeddings | OpenAI `text-embedding-3-small` (1536-dim) |
| LLM generation | OpenRouter (primary: `anthropic/claude-3.5-sonnet`, fallback: `openai/gpt-4o-mini`) |
| Lead email | Resend |
| Lead CRM | Pipedrive |
| Observability | LangFuse (trace every turn) |
| Retries | tenacity |
| Testing | pytest + pytest-asyncio + respx |
| Widget | Vanilla JS single file (`widget/dist/widget.js`) |

## Coding Conventions

- **Typed Python 3.11+**: all functions have full type annotations; use `list[str]` not `List[str]`.
- **Small, focused modules**: one concern per file; avoid large god-modules.
- **No secrets in code**: everything sensitive lives in git-ignored `.env`; import via `get_settings()`.
- **Every external call wrapped**: use `tenacity` retry where the spec calls for it; wrap all
  OpenRouter, Supabase, Resend, and Pipedrive calls with error handling that logs and never crashes
  the chat turn.
- **LangFuse tracing**: every chat turn must emit a LangFuse trace spanning the full
  retrieve → generate → respond pipeline.
- **Commit messages**: use the exact strings each build prompt specifies.
- **Test-Driven Development**: write the failing test first, confirm failure, then implement.

## Knowledge base & conversation behavior

### Bot Behavior (source of truth — verbatim from spec)

**Greeting:** "How can we support your sustainability journey?" with three options:
Buy Sheets · Buy Refill Stations · Question for the team.

### Store URLs (must not be confused)

- **Home-delivery product page** (the "Buy Sheets" target): `https://generationconscious.co/product/laundry-detergent-sheets/`
  — where buyers pick variant (sheet count, scent, one-time vs. subscription) and add to cart. Send the
  home-delivery flow **here**, not to `/checkout/` (an empty cart without a chosen variant).
- **Shop (all products):** `https://generationconscious.co/shop/` — general store, optional "browse everything" fallback only.
- **Location-Specific Refill Subscription:** `https://generationconscious.co/product/location-subscription/`
  — the gated campus/building refill product. **Do NOT** use for home delivery; the refill-station path
  stays lead capture (email + Pipedrive), not a direct link.

### Flows

- **Home delivery:** guide the user to the detergent-sheets product page
  `https://generationconscious.co/product/laundry-detergent-sheets/` to choose options and buy.
- **Wholesale:** collect Name, Email, Phone, Organization, Estimated total sheet purchase → lead to `Info@GenerationConscious.co` (or email/text (516) 619-6174).
- **Refill stations:** collect Name, Email, Phone, Organization, Total Number of Laundry Rooms and Students (Tenants) → lead.
- **Learn more:** Lifecycle Assessment (Drive link), Workforce Development Case Study (email/text with contact info), Mission (redirect to website), Refill Station example (Instagram link).
- **Submit a question:** capture question + contact; team responds within 24h (avg ~15 min).

### Shipping & tax (now live — true facts the bot may state)

Shipping is calculated at checkout using live USPS rates; sales tax applies to New York orders only.
The bot may share these facts and direct users to checkout for the exact calculated amounts. It still
must not quote specific dollar figures it hasn't been given.

### Lead destinations

All leads → `Info@GenerationConscious.co` **and** Pipedrive.
**Contact:** `Info@GenerationConscious.co` · text (516) 619-6174.

### Hard rule

Never invent prices, product specs, or policies — direct such questions to checkout or the team.
(Shipping = live USPS at checkout; tax = NY orders only, per above — these are now known facts,
not things to invent.)

## Lead Intents & Required Fields

Lead capture uses structured OpenRouter tool/function calls. The model emits a `capture_lead` tool
call only when it has all required fields; the server validates before storing.

### Intent: `wholesale`
Required fields: `name`, `email`, `phone`, `organization`, `estimated_sheets`

### Intent: `refill_station`
Required fields: `name`, `email`, `phone`, `organization`, `num_laundry_rooms`, `num_students`

### Intent: `question`
Required fields: `name`, `email`, `question`

All intents also carry an `intent` field set to the intent name. Email format is validated
server-side; required-field absence triggers a re-prompt, not a silent failure.

**Leads must never be lost. Order is store-first, then notify:**
1. Insert the validated lead into Supabase `leads` with `emailed=false`, `pushed_to_pipedrive=false`.
2. Attempt Resend email → set `emailed=true` on success.
3. Attempt Pipedrive person+deal → set `pushed_to_pipedrive=true` on success.
4. If either notification fails, the row still persists in Supabase with its failure flag unset,
   ready for retry. Failures are logged and never crash the chat turn.
