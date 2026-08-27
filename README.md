# Genco Intel Chatbot — Handoff Guide

A standalone FAQ chatbot for **Generation Conscious**, embedded on their WordPress site via a
single `<script>` tag. It greets users with three options (Buy Sheets / Buy Refill Stations /
Question for the team), captures leads for wholesale and refill-station inquiries, and routes
anything it cannot answer to the team at `Info@GenerationConscious.co` or via text to
(516) 619-6174.

**In the default mode (`BOT_MODE=faq`) the bot calls no AI service at all.** Questions are matched
against the knowledge base with Postgres full-text search, and the reply is the matching FAQ entry
**word for word** — the bot never writes its own text, it only shows answers the GC team wrote.
After every answer it asks "did that answer your question?"; a "No" sends the question to the team
as a lead. That is the honest claim to make to schools and other AI-wary buyers.

The original generative (LLM) pipeline is still in the codebase, retained behind
`BOT_MODE=generative`. Everything in this guide marked **generative mode only** is irrelevant
while the default is running.

---

## The two modes

| | `faq` (default) | `generative` |
|---|---|---|
| Answers come from | the KB chunk itself, verbatim | an LLM writing over retrieved KB context |
| AI services called | **none** | OpenRouter (chat) + OpenAI (embeddings) |
| Matching | Postgres full-text search (`ts_rank`) | pgvector cosine similarity |
| Conversation | deterministic state machine (`chat/flows.py`) | model-driven, with tool calls |
| Keys needed | Supabase, Resend, Pipedrive | the above **plus** OpenRouter, OpenAI, LangFuse |
| AI spend | **$0** | metered, capped by `DAILY_COST_CAP_USD` |

Both modes share the same entry point, the same guard gates, the same lead pipeline
(Supabase → Resend → Pipedrive), the same widget, and the same deploy topology. Switching is one
environment variable and a restart.

---

## Architecture

```
Widget (vanilla JS + quick-reply buttons) ──POST /chat──> FastAPI backend
                                                            ├─ BOT_MODE=faq (default)
                                                            │    ├─ rag/fts.py     Postgres full-text match (verbatim answers)
                                                            │    ├─ chat/flows.py  deterministic state machine (feedback + lead steps)
                                                            │    ├─ faq_misses.py  FAQ backlog (every hit and miss, no PII)
                                                            │    └─ leads          Supabase → Resend email → Pipedrive CRM
                                                            └─ BOT_MODE=generative
                                                                 ├─ rag/           pgvector + embeddings + retrieval
                                                                 ├─ llm.py         OpenRouter completions (+ fallback model)
                                                                 ├─ guardrails     injection guard, cost cap
                                                                 └─ observability  LangFuse tracing of every turn
```

Rate limiting, the UUID session guard, and the store-first lead pipeline run identically in both
modes — FAQ mode routes through the same entry code.

### The `/chat` contract

`POST /chat` returns the same four keys on **every** path, always HTTP 200:

```json
{"session_id": "…", "reply": "…", "retrieval_scores": [0.42], "quick_replies": ["…"]}
```

`quick_replies` is the list of buttons the widget renders under the bot message; tapping one sends
its label as an ordinary user message. It is always present and is an empty list in generative
mode. In FAQ mode `retrieval_scores` carries `ts_rank` values (not cosine similarities) — same
shape, different scale.

---

## Go-Live Runbook

The build is done; this is the runbook to take it live, roughly in order. The **critical path is
1 → 2 → 4 → 5**; branding (3) and the optional extras (6) slot in around them. The most likely
place to stall is the company-dependent items in step 1 — **fire those off first.**

### 1. Get the keys (the real gate)

In FAQ mode, three services must be wired into `backend/.env`:

- **Supabase** — project URL + `service_role` key,
- **Resend** — API key + a verified sending domain,
- **Pipedrive** — API token + company subdomain.

Two are the company's to provide — loop Greg in now: the **Pipedrive API token** and **Resend's
sending domain**. There is no LLM bill to set up: FAQ mode reaches no AI service, so the
`OPENROUTER_*`, `EMBEDDING_*`, and `LANGFUSE_*` values may all stay blank.

**Start Resend first:** verifying `generationconscious.co` as a sending domain needs DNS records and
can take a while to propagate. Until it's done, lead-notification emails silently fail.

> **Generative mode only:** switching to `BOT_MODE=generative` additionally requires OpenRouter and
> OpenAI (embeddings — a *separate* key) with billing on the company's account, plus LangFuse
> (public + secret) for tracing.

### 2. Stand it up locally and run the VERIFICATION.md checks

Fill `backend/.env` (set `BOT_MODE=faq`), paste `backend/app/rag/schema.sql` into the Supabase SQL
editor, run `python -m app.rag.ingest`, confirm `/health`. Then work the checks in
`VERIFICATION.md` — it lists which apply in FAQ mode and which are generative-only. Two to watch:

- **Check 11** — the FAQ feedback round-trip: tap "✉️ No — ask the team", finish the guided
  question flow, and confirm the lead lands in Supabase **and** the inbox **and** Pipedrive. This
  is the business goal of the whole pivot; don't wave it through.
- **Check 10** — the match-quality spot check. Eyeball the printed `ts_rank` values against
  `FTS_MIN_RANK` (`0.05` in `backend/app/rag/fts.py`); below it the bot offers the team instead of
  showing a weak answer.

### 3. Brand the widget

The `CONFIG` block at the top of `widget/dist/widget.js` carries the brand values **approved by the
GC team on 2026-08-10**: `PRIMARY` `#FF0719` and `LOGO` pointing at the site's wordmark image (the
Mission copy in the KB was approved the same day). No action needed unless the brand changes —
if it does, edit the CONFIG block and re-open `widget/test.html`
to confirm it renders correctly. Two ways to view it (documented in `widget/test.html` itself):

- **Zero keys (offline stub):** open `widget/test.html?stub=1` directly from disk (an inline fetch
  mock answers `/chat` and `/history`), or run `python widget/stub_server.py` (stdlib only) and
  open the page. Either walks the full FAQ conversation — greeting buttons → answer → 👍/✉️
  feedback → guided lead flow → confirmation — without any credentials.
- **Real backend:** serve the page over HTTP so its origin passes CORS —
  `cd widget && python -m http.server 5500`, then open `http://localhost:5500/test.html`
  (this origin is in the dev `ALLOWED_ORIGINS` default; opening via `file://` is CORS-blocked).

### 4. Deploy to Render

Create the service from the **root `render.yaml`** blueprint, set every env var from the
[Environment Variables](#environment-variables) table in the Render dashboard (all `sync: false`, so
you paste them there — **including `BOT_MODE=faq`**), let it build, then confirm `/health` is green
and `curl -I https://YOUR-HOST/widget/widget.js` returns **200**.

### 5. Embed and lock down

You'll need temporary WP admin from Greg. Paste the embed snippet into WordPress (header/footer
plugin or Elementor block) with `data-backend-url` pointing at the Render host. In the Render env,
set `ALLOWED_ORIGINS` to **just** `https://generationconscious.co` (drop all dev origins). Then re-run a
lead end-to-end against production and do the **mobile + desktop QA** pass.

### 6. Optional, after it's live

- Review the `faq_misses` table weekly and turn the gaps into new KB entries — see
  [The FAQ backlog](#the-faq-backlog-faq_misses).
- **Generative mode only:** set the CI secret (`gh secret set OPENAI_API_KEY`) to activate the
  faithfulness gate, and add `requirements-ml.txt` to the image on an instance sized **~1–2 GB**
  to run the ML injection scanner (see
  [Optional ML Enhancements](#optional-ml-enhancements-generative-mode-only)).
  Neither applies while `BOT_MODE=faq` — there is no generated text to judge and no model to
  protect from prompt injection.

---

## Prerequisites

- Python 3.11+
- pip
- A Supabase project with the schema applied (see `backend/app/rag/schema.sql`)

---

## Local Development Setup

```bash
# 1. Create and activate virtualenv
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r backend/requirements.txt

# 3. Configure environment — the env file lives at backend/.env
# (config.py anchors env_file to backend/.env; a repo-root .env is never read)
cp .env.example backend/.env
# Open backend/.env and fill in Supabase, Resend, and Pipedrive. In the default
# BOT_MODE=faq, every OPENROUTER_*/EMBEDDING_*/LANGFUSE_* value may stay blank.

# 4. Apply the database schema in Supabase SQL editor
# (paste the contents of backend/app/rag/schema.sql — the whole file is re-runnable)

# 5. Ingest the knowledge base
cd backend
python -m app.rag.ingest

# 6. Start the dev server
uvicorn app.main:app --reload
```

The API is available at `http://localhost:8000`. Health check: `GET /health`.

## Run tests

```bash
cd backend
python -m pytest -v
```

---

## Environment Variables

All values live in `backend/.env` (git-ignored). Set the same variables in the Render (or
Railway/Fly) dashboard as service environment variables.

| Variable | Description | Where to get it |
|---|---|---|
| `BOT_MODE` | `faq` (default) = zero-AI full-text matching with verbatim KB answers. `generative` = the original OpenRouter RAG pipeline. | Set to `faq` unless you deliberately want the LLM pipeline |
| `SUPABASE_URL` | Your Supabase project URL (`https://xxxx.supabase.co`) | Supabase dashboard → Project Settings → API |
| `SUPABASE_SERVICE_KEY` | Supabase `service_role` key — **server-side only, never expose to the browser** | Supabase dashboard → Project Settings → API |
| `RESEND_API_KEY` | Resend API key for lead notification emails | [resend.com](https://resend.com) → API Keys |
| `FROM_EMAIL` | Sender address for lead emails (e.g. `bot@generationconscious.co`) | Must be a verified Resend domain |
| `ESCALATION_EMAIL` | Destination for lead notifications (default: `Info@GenerationConscious.co`) | GC team preference |
| `PIPEDRIVE_API_TOKEN` | Pipedrive API token | Pipedrive → User menu → Personal preferences → API |
| `PIPEDRIVE_DOMAIN` | Pipedrive company subdomain (e.g. `yourcompany`) | Your Pipedrive account URL |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins. Dev default (identical in `config.py` and `.env.example`): `http://localhost:8000,http://localhost:5500,http://127.0.0.1:5500`. The origin must exactly match how `widget/test.html` is served — `localhost` and `127.0.0.1` are different origins. | Production: set to exactly `https://generationconscious.co` before go-live |
| `RATE_LIMIT_PER_MINUTE` | Max chat requests per IP per minute (default: `20`). Applies in **both** modes. | Tune based on traffic |

### Generative mode only

These are read only when `BOT_MODE=generative`. In FAQ mode they may all stay blank — the app
boots and answers questions with every one of them empty.

| Variable | Description | Where to get it |
|---|---|---|
| `OPENROUTER_API_KEY` | API key for LLM chat completions | [openrouter.ai](https://openrouter.ai) → Keys |
| `OPENROUTER_MODEL` | Primary model (default: `anthropic/claude-3.5-sonnet`) | OpenRouter model list |
| `OPENROUTER_MODEL_FALLBACK` | Fallback model used when the primary model call fails after retries (default: `openai/gpt-4o-mini`). Note: the daily cost cap returns a static message — it does NOT invoke the fallback model. | OpenRouter model list |
| `EMBEDDING_API_KEY` | OpenAI API key used **only** for embeddings | [platform.openai.com](https://platform.openai.com) → API Keys |
| `EMBEDDING_MODEL` | Embedding model (default: `text-embedding-3-small`) | OpenAI docs |
| `LANGFUSE_PUBLIC_KEY` | LangFuse public key | [cloud.langfuse.com](https://cloud.langfuse.com) → Settings → API Keys |
| `LANGFUSE_SECRET_KEY` | LangFuse secret key | Same location |
| `LANGFUSE_HOST` | LangFuse host (default: `https://cloud.langfuse.com`) | Use default unless self-hosting |
| `DAILY_COST_CAP_USD` | Hard daily spend cap in USD (default: `10.0`). Meaningless in FAQ mode — there is no spend. | Increase if volume warrants |

---

## Deploying to Render

1. Push this repository to GitHub (or GitLab/Bitbucket).
2. In the [Render dashboard](https://render.com), create a new **Web Service**, or let Render
   auto-detect the **`render.yaml` Blueprint at the repo root**.
3. The Blueprint builds `./backend/Dockerfile` with the **repository root as the build context**
   (`dockerContext: .`), so the widget is bundled into the image (see "Serving the widget" below).
4. Set all environment variables from the table above in the Render **Environment** tab —
   `BOT_MODE=faq` included; the blueprint declares every key as `sync: false`, so nothing is
   inherited automatically.
5. Render will build the image and deploy; the `/health` endpoint is used as the health check.

**Alternatives:** Railway (`railway.app`) and Fly.io (`fly.toml`) work the same way — any host
that can run the Docker image (built from the repo root) and inject env vars with a `/health`
check will work.

### Docker image (for local testing)

The build context is the **repository root** (not `backend/`) so `widget/dist/` is bundled:

```bash
# from the repo root
docker build -f backend/Dockerfile -t genco-chatbot .
docker run -p 8000:8000 --env-file backend/.env genco-chatbot
```

---

## WordPress Embed Snippet

Paste the following into a header/footer plugin (e.g. Insert Headers and Footers) or an
Elementor custom-code block on the Generation Conscious site:

```html
<script src="https://YOUR-BACKEND-HOST/widget/widget.js"
        data-backend-url="https://YOUR-BACKEND-HOST"></script>
```

Replace `YOUR-BACKEND-HOST` with the actual Render (or Railway/Fly) service URL, e.g.
`https://genco-chatbot.onrender.com`.

The `widget.js` file is served by the backend's `/widget` static-files mount
(`backend/app/main.py`) — no separate CDN setup required (though CDN hosting is a valid
alternative if you want to decouple widget deploys from backend deploys).

### Serving the widget in production

**This is now handled by default (Option 1 below).** The Dockerfile builds from the repository
root and bundles `widget/dist/` into the image at `/widget/dist`, which is exactly where
`backend/app/main.py` resolves the `/widget` mount to
(`Path(__file__).resolve().parent.parent.parent / "widget" / "dist"`). So a default deploy
serves `GET /widget/widget.js` and the embed snippet works out of the box. (Historically the
image was built with `backend/` as the context, which excluded `widget/dist/` and caused a
silent 404 — that's been fixed.)

Verify after deploy with the LAUNCH_CHECKLIST `curl -I` check. If you'd rather decouple the
widget from the backend, use the CDN option instead.

---

**Option 1 — Bundle the widget into the backend Docker image (default, already configured)**

`render.yaml` (repo root) sets `dockerfilePath: ./backend/Dockerfile` and `dockerContext: .`,
and `backend/Dockerfile` does:

```dockerfile
COPY backend/ /app
COPY widget/dist /widget/dist
```

Build manually from the repo root for local testing:

```bash
# from repo root, not backend/
docker build -f backend/Dockerfile -t genco-chatbot .
docker run -p 8000:8000 --env-file backend/.env genco-chatbot
```

`widget/dist/widget.js` is committed to the repo (it's a single hand-authored file, no build
step), so the `COPY` always has something to copy.

---

**Option 2 — Host widget.js on a CDN / static host (alternative)**

Upload `widget/dist/widget.js` to any static hosting service (e.g. Cloudflare Pages, Netlify,
AWS S3 + CloudFront, GitHub Pages). The backend serves only the API; only the `src` in the
embed snippet changes.

Adjusted embed snippet (replace `YOUR-CDN-HOST` and `YOUR-BACKEND-HOST`):

```html
<script src="https://YOUR-CDN-HOST/widget.js"
        data-backend-url="https://YOUR-BACKEND-HOST"></script>
```

- `src` points to the CDN URL where `widget.js` is hosted.
- `data-backend-url` still points at the FastAPI backend — widget POST requests go there.

This approach decouples widget releases from backend deploys: you can update the widget by
re-uploading to the CDN without triggering a backend redeploy.

---

## Adding / Updating Knowledge Base Content

The knowledge base lives in `backend/knowledge_base/*.md`. Each file is plain Markdown, split into
chunks at its Markdown headings.

**In FAQ mode a chunk is shown to the visitor word for word**, so every chunk must read as a
complete, standalone answer — no "see above", no half-sentences, no internal notes. Editing the KB
*is* editing the bot's script.

To add a new topic or update an existing answer:

1. Edit or add a `.md` file in `backend/knowledge_base/`.
2. Re-run the ingest script:
   ```bash
   cd backend
   python -m app.rag.ingest
   ```
   The script chunks and upserts documents into the `kb_documents` Supabase table. In FAQ mode it
   makes **no embedding calls** — rows are stored with `embedding = NULL` and matched through the
   generated `content_tsv` column. In generative mode it embeds first, then upserts, then prunes
   stale rows (so a failed embedding call leaves the old KB serving).
3. Restart the backend (or trigger a Render redeploy) so the updated content is live.

### The FAQ backlog (`faq_misses`)

Every feedback event is recorded in the `faq_misses` table — the growing-FAQ loop Greg asked for.
One row per event, **no PII**: the question text, the top `ts_rank`, and whether it was answered.

| Column | Meaning |
|---|---|
| `question` | what the visitor typed |
| `top_rank` | best `ts_rank` the FAQ produced (`0.0` when nothing matched) |
| `answered` | `true` = tapped "👍 Yes, that answered it"; `false` = a "No" tap or a no-match |
| `created_at` | timestamp |

Read it in the Supabase Table Editor, or query the gaps directly:

```sql
select question, top_rank, created_at
from faq_misses
where answered = false
order by created_at desc
limit 50;
```

Rows with `answered = false` and a low `top_rank` are questions the KB cannot answer at all — the
best candidates for new KB entries. Rows with `answered = false` and a *high* `top_rank` are worse
news: the FAQ matched something, but the wrong thing, or the right entry is badly worded.

Only explicit 👍/✉️ taps and true no-matches are recorded — a visitor who simply types a new
question instead of tapping is not counted either way.

### Manual feedback loop for escalated questions

When a user asks something the bot cannot answer:

1. It offers to send the question to the team; the completed flow lands as a `question` lead in
   Supabase, the inbox, and Pipedrive (and the gap is recorded in `faq_misses` either way).
2. The team answers the question manually (via email or Pipedrive).
3. If the answer is worth adding to the KB, write it up as a Markdown file in
   `backend/knowledge_base/`.
4. Re-ingest (`python -m app.rag.ingest`) and redeploy.

This is the **approved pathway for growing the KB** — it keeps humans in the loop before any
new content is served by the bot.

### Embedding model migration note (generative mode only)

The generative pipeline's embedding model is `text-embedding-3-small` (1536 dimensions).
**Changing the embedding model requires:**

1. A schema migration in Supabase to update the vector column dimension.
2. A full re-ingest of all KB documents with the new model.

Do not change `EMBEDDING_MODEL` without performing both steps — mixed-dimension vectors will
produce nonsense retrieval scores. FAQ mode is unaffected: it stores no vectors.

---

## Managing Cost

**In FAQ mode, AI spend is $0/month** — no OpenRouter call, no embedding call, on any turn,
including ingest. The only running costs are the hosting instance (Render), Supabase, Resend, and
the existing Pipedrive seat, all of which have free or already-paid tiers at this volume. There is
nothing to meter and nothing to cap, which is why `DAILY_COST_CAP_USD` is skipped entirely in this
mode.

### Generative mode only

- `DAILY_COST_CAP_USD` (default `10.0`) is a hard daily cap. When the cap is hit, the backend
  returns a static cost-cap message ("I'm momentarily unavailable…") — it does NOT call the
  fallback model. The fallback model (`OPENROUTER_MODEL_FALLBACK`) is invoked only when the
  primary model call fails after its internal retries. Adjust the cap in the Render environment
  variables.
- **The cost tracker is in-memory and per-process** (`CostTracker` in `backend/app/guardrails.py`):
  it resets to zero on every deploy, restart, or crash, and each uvicorn worker or extra instance
  keeps its own independent accumulator — so the "daily" cap is per-process, not global, and
  effectively multiplies by the worker/instance count. Pin the deploy to a single worker, or swap
  in a shared store (Redis, or a Supabase row keyed by UTC date) before scaling out.
- **Spend is an estimate from hardcoded prices**: the tracker multiplies token usage by the
  per-1K-token USD rates in `_RATES` (`backend/app/guardrails.py`), deliberately set on the high
  side so the cap trips early rather than late. If you change `OPENROUTER_MODEL` or
  `OPENROUTER_MODEL_FALLBACK`, add the new model's prices to `_RATES` — unknown models are billed
  at the `default` (sonnet-class) rate, which never undercounts but will overcount cheap models
  and trip the cap sooner than real spend warrants.
- **Embedding spend is not counted**: the tracker only meters OpenRouter chat completions. Query
  embeddings billed to `EMBEDDING_API_KEY` (fractions of a cent per turn with
  `text-embedding-3-small`) sit outside the cap — watch them on the OpenAI billing page.
- To reduce cost per turn, switch `OPENROUTER_MODEL` to a cheaper model (e.g. `openai/gpt-4o-mini`).
  The fallback model is already cheap by default.
- Monitor actual spend in LangFuse and in your OpenRouter billing dashboard.

### Guard-blocked turns (both modes)

Requests stopped by the rate limit — and, in generative mode, by the daily cost cap or the
injection guard — get their static reply without touching Supabase: no `chat_messages` row, no
trace. Those exchanges therefore do not reappear via `GET /history` after a page reload. This is
deliberate: the gates run before any DB access so abusive traffic costs nothing downstream.

---

## Reading LangFuse (generative mode only)

FAQ mode emits no traces — it calls no model, and LangFuse's keys stay unset (`trace_turn` no-ops
without them). Operational visibility in FAQ mode is the `faq_misses` table plus the `emailed` /
`pushed_to_pipedrive` flags on `leads`.

In generative mode, open [cloud.langfuse.com](https://cloud.langfuse.com) and navigate to your
project.

- **Traces view:** each `/chat` call produces one trace. Click a trace to see the
  retrieve → generate → respond spans with latency and token usage.
- **Escalation events:** traces tagged `escalation` show questions the bot could not ground
  in the KB — use these to identify KB gaps.
- **Cost tracking:** the `usage` metadata on each generation span shows token counts. Multiply
  by the model's per-token price to estimate cost. LangFuse's cost dashboard automates this if
  you configure model prices.

---

## Where Leads Land

All captured leads (wholesale, refill-station, and question intents) are stored in **two places**,
identically in both modes — the lead pipeline is shared code:

1. **Supabase `leads` table** — durable store; survives any downstream failures.
2. **`Info@GenerationConscious.co`** via Resend — email notification sent immediately.
3. **Pipedrive** — a Person and a Deal are created via the Pipedrive API.

If either notification fails, the Supabase row persists with `emailed=false` or
`pushed_to_pipedrive=false` so the team can retry manually. Contact the team at
`Info@GenerationConscious.co` or text (516) 619-6174.

In FAQ mode the fields are collected one at a time by the guided flow in `backend/app/chat/flows.py`
(validated per field, "cancel" exits); in generative mode the model emits a `capture_lead` tool call
that the server validates. Both call the same `capture_lead()`.

---

## PII Retention Policy

- **`leads` table:** kept indefinitely as business records (name, email, phone, organization,
  intent). Review and purge as required by applicable privacy law.
- **`chat_messages` table:** contains conversation history including any PII users type in chat.
  Purge on a chosen window (e.g. 90 days) using a scheduled SQL job:
  ```sql
  DELETE FROM chat_messages
  WHERE created_at < NOW() - INTERVAL '90 days';
  ```
  Schedule this in Supabase's pg_cron extension or via a cron job on your server.
- **`faq_misses` table:** no PII by design — question text, match rank, and a boolean only. Keep it
  as long as it's useful for growing the FAQ. (A question a visitor typed *could* contain personal
  details they volunteered; treat it with the same care as `chat_messages` if that ever shows up.)

---

## Fast-Follow: Token Streaming (generative mode only)

`POST /chat` returns the full reply as a single JSON body. The approved design
(docs/superpowers/specs/2026-06-18-genco-chatbot-design.md, "Token streaming") shipped it this way
and flags **SSE streaming of `/chat` replies as the first fast-follow enhancement** for the
generative pipeline — perceived latency was the widget's weakest point there. **FAQ mode makes this
moot:** a full-text match returns in milliseconds, with no tokens to stream.

If generative mode is ever made the default again, implementing streaming means adding a streaming
variant of the endpoint (SSE or chunked) plus widget changes to render tokens incrementally; keep
the existing frozen `{session_id, reply, retrieval_scores, quick_replies}` contract intact for the
embedded widget until both sides are updated.

---

## Optional ML Enhancements (generative mode only)

Neither of these applies while `BOT_MODE=faq`: there is no model to protect from prompt injection
and no generated text to judge for faithfulness.

Two opt-in enhancements live behind `backend/requirements-ml.txt`. They are **not** installed by
default, so the core app, the default Docker image, and the standard test suite carry zero extra
dependencies. Install them only when you want them:

```bash
pip install -r backend/requirements-ml.txt
```

> Heads-up: these pull in heavy deps (`llm-guard` → transformers/torch; `deepeval` → a judge LLM).
> `torch` may lack wheels on the newest Python — install in a Python 3.11–3.12 env (matching the
> production image) or in CI.

### ML prompt-injection scanning (LLM Guard)

`backend/app/injection_scanner.py` wraps LLM Guard's `PromptInjection` scanner and runs **in front
of** the substring guard in `chat/router.py`, on the generative branch:

```python
if guardrails.is_injection_attempt(req.message) or injection_scanner.is_injection(req.message):
    ...  # decline
```

It is **lazy and graceful**: the model loads on first use and is cached; if `llm-guard` isn't
installed (or the model fails to load), `is_injection()` returns `False` and the substring guard
remains the active defense — the app runs unchanged. To enable it in production, add
`requirements-ml.txt` to the image build and redeploy. Only the `PromptInjection` scanner is
enabled (latency scales with scanner count).

> **Sizing:** enabling this loads `torch` + a DeBERTa model into memory. A 512 MB instance (e.g.
> Render's smallest) will **OOM** — size the instance to **~1–2 GB** before turning it on. While
> it's off (the default), the substring guard has no such requirement.

### Faithfulness CI gate (DeepEval)

`backend/tests/test_faithfulness_eval.py` uses an LLM judge (`FaithfulnessMetric`) to score whether
an answer is *faithful* to its retrieved KB context.

**Scope — read this honestly:** the committed cases are **fixed answer/context pairs**, so this is a
**metric-wiring + drift-regression** gate (it proves the faithfulness check works and catches
regressions in representative answers), **not** proof that the live bot is faithful. Verifying the
live bot would mean running real user queries through `retrieve → generate` and judging *those*
outputs — a separate, live-key step. (In FAQ mode the question doesn't arise: the answer *is* the
KB text, so faithfulness is structural rather than measured.)

It self-skips unless DeepEval is installed **and** a judge key is set, so it gates CI only where
those are present:

```bash
pip install -r backend/requirements-ml.txt
export OPENAI_API_KEY=...                  # judge model
export DEEPEVAL_JUDGE_MODEL=gpt-4.1-mini   # optional override (default)
pytest backend/tests/test_faithfulness_eval.py -v
```

A faithfulness score below `0.7` fails the build. `run_eval.py` (routing/grounding-score check plus
the FAQ-mode fixtures) remains the lightweight, no-key eval; DeepEval is the deeper, judge-based
gate.

---

## Project Structure

```
genco-chatbot/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, CORS, static widget mount
│   │   ├── config.py            # pydantic-settings Settings (BOT_MODE; reads backend/.env)
│   │   ├── db.py                # Supabase client factory
│   │   ├── llm.py               # OpenRouter completions + fallback model (generative only)
│   │   ├── observability.py     # LangFuse tracing (generative only)
│   │   ├── escalation.py        # should_escalate + capture_lead (store-first, both modes)
│   │   ├── email_service.py     # Resend lead-notification email
│   │   ├── pipedrive.py         # Pipedrive person + deal creation
│   │   ├── faq_misses.py        # FAQ backlog: one row per feedback event (no PII)
│   │   ├── guardrails.py        # rate limit (both modes), cost cap + injection guard (generative)
│   │   ├── injection_scanner.py # optional ML injection scanner (LLM Guard, lazy, generative)
│   │   ├── rag/                 # fts.py (FAQ full-text match), embeddings.py, ingest.py,
│   │   │                        #   retrieve.py (generative), schema.sql
│   │   └── chat/                # router.py (mode branch), flows.py (FAQ state machine),
│   │                            #   prompts.py, memory.py (incl. flow_state), tools.py
│   ├── knowledge_base/          # *.md source documents — the bot's script, shown verbatim
│   ├── tests/                   # pytest suite
│   ├── requirements.txt
│   ├── requirements-ml.txt      # optional ML extras (LLM Guard, DeepEval)
│   ├── pytest.ini
│   └── Dockerfile               # built from repo root (bundles widget/dist)
├── eval/                        # run_eval.py + test_set.jsonl (repo root — run from here)
├── widget/
│   ├── dist/
│   │   └── widget.js            # embed widget (single hand-authored file, renders quick replies)
│   ├── test.html                # widget QA page (offline stub via ?stub=1)
│   └── stub_server.py           # zero-key stub backend for widget QA (canned FAQ conversation)
├── render.yaml                  # Render Blueprint (repo root; dockerContext: .)
├── .env.example                 # template — copy to backend/.env
├── VERIFICATION.md              # deferred live-key checks
├── LAUNCH_CHECKLIST.md          # pre-launch gate checklist
└── README.md                    # this file
```

The Supabase schema lives at `backend/app/rag/schema.sql` (there is no `backend/schema.sql`), and
lead logic is flat modules under `backend/app/` — there are no `leads/` or `guardrails/` packages.
Re-applying the whole schema file is safe and idempotent; it upgrades an existing project in place
with the FAQ objects (`content_tsv` + GIN index, `match_documents_fts`, `chat_sessions.flow_state`,
`faq_misses`).
