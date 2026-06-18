# Genco Intel Chatbot — Handoff Guide

A standalone RAG chatbot for **Generation Conscious**, embedded on their WordPress site via a
single `<script>` tag. The bot answers only from a curated knowledge base, greets users with
three options (Buy Sheets / Buy Refill Stations / Question for the team), captures leads for
wholesale and refill-station inquiries, and escalates anything it cannot ground in the KB to the
team at `Info@GenerationConscious.co` or via text to (516) 619-6174.

---

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

---

## Prerequisites

- Python 3.11+
- pip
- A Supabase project with the schema applied (see `backend/schema.sql`)

---

## Local Development Setup

```bash
# 1. Create and activate virtualenv
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r backend/requirements.txt

# 3. Configure environment
cp .env.example .env
# Open .env and fill in all keys (see the Environment Variables section below)

# 4. Apply the database schema in Supabase SQL editor
# (paste the contents of backend/schema.sql)

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

All values live in `.env` (git-ignored). Set the same variables in the Render (or Railway/Fly)
dashboard as service environment variables.

| Variable | Description | Where to get it |
|---|---|---|
| `OPENROUTER_API_KEY` | API key for LLM chat completions | [openrouter.ai](https://openrouter.ai) → Keys |
| `OPENROUTER_MODEL` | Primary model (default: `anthropic/claude-3.5-sonnet`) | OpenRouter model list |
| `OPENROUTER_MODEL_FALLBACK` | Fallback model on primary failure or cost-cap (default: `openai/gpt-4o-mini`) | OpenRouter model list |
| `EMBEDDING_API_KEY` | OpenAI API key used **only** for embeddings | [platform.openai.com](https://platform.openai.com) → API Keys |
| `EMBEDDING_MODEL` | Embedding model (default: `text-embedding-3-small`) | OpenAI docs |
| `SUPABASE_URL` | Your Supabase project URL (`https://xxxx.supabase.co`) | Supabase dashboard → Project Settings → API |
| `SUPABASE_SERVICE_KEY` | Supabase `service_role` key — **server-side only, never expose to the browser** | Supabase dashboard → Project Settings → API |
| `LANGFUSE_PUBLIC_KEY` | LangFuse public key | [cloud.langfuse.com](https://cloud.langfuse.com) → Settings → API Keys |
| `LANGFUSE_SECRET_KEY` | LangFuse secret key | Same location |
| `LANGFUSE_HOST` | LangFuse host (default: `https://cloud.langfuse.com`) | Use default unless self-hosting |
| `RESEND_API_KEY` | Resend API key for lead notification emails | [resend.com](https://resend.com) → API Keys |
| `FROM_EMAIL` | Sender address for lead emails (e.g. `bot@generationconscious.co`) | Must be a verified Resend domain |
| `ESCALATION_EMAIL` | Destination for lead notifications (default: `Info@GenerationConscious.co`) | GC team preference |
| `PIPEDRIVE_API_TOKEN` | Pipedrive API token | Pipedrive → User menu → Personal preferences → API |
| `PIPEDRIVE_DOMAIN` | Pipedrive company subdomain (e.g. `yourcompany`) | Your Pipedrive account URL |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins (e.g. `https://generationconscious.co`) | Set to the live site before go-live; include `http://localhost:5500` for dev |
| `RATE_LIMIT_PER_MINUTE` | Max chat requests per IP per minute (default: `20`) | Tune based on traffic |
| `DAILY_COST_CAP_USD` | Hard daily spend cap in USD (default: `10.0`) | Increase if volume warrants |

---

## Deploying to Render

1. Push this repository to GitHub (or GitLab/Bitbucket).
2. In the [Render dashboard](https://render.com), create a new **Web Service**.
3. Select **Docker** as the environment and point it at `backend/Dockerfile`
   (or use the `backend/render.yaml` file — Render can auto-detect it).
4. Set all environment variables from the table above in the Render **Environment** tab.
5. Render will build the image and deploy; the `/health` endpoint is used as the health check.

**Alternatives:** Railway (`railway.app`) and Fly.io (`fly.toml`) work the same way — any host
that can run the Docker image and inject env vars with a `/health` check will work.

### Docker image (for local testing)

```bash
cd backend
docker build -t genco-chatbot .
docker run -p 8000:8000 --env-file ../.env genco-chatbot
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

**The gap:** `backend/app/main.py` mounts `widget/dist/` via a path resolved relative to the
repo root (`Path(__file__).resolve().parent.parent.parent / "widget" / "dist"`). In local
development this works because both `backend/` and `widget/dist/` are present on disk. In
production, however, the Docker image is built with `backend/` as the build context
(`render.yaml`: `dockerContext: .`, evaluated from the `backend/` directory). The Dockerfile's
`COPY . .` therefore copies only the contents of `backend/` — `widget/dist/` is never included.
At startup the `if _widget_dir.exists()` guard silently skips the mount, and
`GET /widget/widget.js` returns **404** even though the embed snippet points there.

Choose one of the two remedies below before going live:

---

**Option 1 — Bundle the widget into the backend Docker image**

Build the Docker image from the **repository root** so both `backend/` and `widget/dist/` are
available to the build context.

1. In `backend/render.yaml`, change `dockerContext` from `.` to `..` (the repo root):

   ```yaml
   dockerfilePath: ./backend/Dockerfile
   dockerContext: ..
   ```

2. Update `backend/Dockerfile` so its `COPY` paths account for the new context root:

   ```dockerfile
   FROM python:3.11-slim
   WORKDIR /app
   # Copy and install Python dependencies
   COPY backend/requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt
   # Copy backend application code
   COPY backend/ .
   # Copy pre-built widget so the /widget static mount is satisfied
   COPY widget/dist/ widget/dist/
   EXPOSE 8000
   HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
   CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
   ```

   The `COPY widget/dist/ widget/dist/` line places the built widget at
   `/app/widget/dist/` inside the container, which is exactly where `main.py`'s
   three-`parent`-level path resolves to (`/app` is the `WORKDIR`).

3. If you build the image manually (e.g. for local testing), run from the repo root:

   ```bash
   # from repo root, not backend/
   docker build -f backend/Dockerfile -t genco-chatbot .
   docker run -p 8000:8000 --env-file .env genco-chatbot
   ```

**Important:** run `npm run build` (or the equivalent build step) inside `widget/` to produce
`widget/dist/widget.js` **before** running the Docker build, otherwise the `COPY` will fail.

---

**Option 2 — Host widget.js on a CDN / static host (simpler)**

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

The knowledge base lives in `backend/knowledge_base/*.md`. Each file is plain Markdown.

To add a new topic or update an existing answer:

1. Edit or add a `.md` file in `backend/knowledge_base/`.
2. Re-run the ingest script:
   ```bash
   cd backend
   python -m app.rag.ingest
   ```
   The script chunks, embeds, and upserts documents into the `kb_documents` Supabase table.
3. Restart the backend (or trigger a Render redeploy) so the updated embeddings are live.

### Manual feedback loop for escalated questions

When a user asks something the bot escalates to the team:

1. The team answers the question manually (via email or Pipedrive).
2. If the answer is worth adding to the KB, write it up as a Markdown file in
   `backend/knowledge_base/`.
3. Re-ingest (`python -m app.rag.ingest`) and redeploy.

This is the **approved pathway for growing the KB** — it keeps humans in the loop before any
new content is served by the bot.

### Embedding model migration note

The current embedding model is `text-embedding-3-small` (1536 dimensions). **Changing the
embedding model requires:**

1. A schema migration in Supabase to update the vector column dimension.
2. A full re-ingest of all KB documents with the new model.

Do not change `EMBEDDING_MODEL` without performing both steps — mixed-dimension vectors will
produce nonsense retrieval scores.

---

## Reading LangFuse

Open [cloud.langfuse.com](https://cloud.langfuse.com) and navigate to your project.

- **Traces view:** each `/chat` call produces one trace. Click a trace to see the
  retrieve → generate → respond spans with latency and token usage.
- **Escalation events:** traces tagged `escalation` show questions the bot could not ground
  in the KB — use these to identify KB gaps.
- **Cost tracking:** the `usage` metadata on each generation span shows token counts. Multiply
  by the model's per-token price to estimate cost. LangFuse's cost dashboard automates this if
  you configure model prices.

---

## Managing Cost

- `DAILY_COST_CAP_USD` (default `10.0`) is a hard daily cap. When the cap is hit, the backend
  switches to `OPENROUTER_MODEL_FALLBACK` or returns a cost-cap error. Adjust the cap in the
  Render environment variables.
- To reduce cost per turn, switch `OPENROUTER_MODEL` to a cheaper model (e.g. `openai/gpt-4o-mini`).
  The fallback model is already cheap by default.
- Monitor actual spend in LangFuse and in your OpenRouter billing dashboard.

---

## Where Leads Land

All captured leads (wholesale, refill-station, and question intents) are stored in **two places**:

1. **Supabase `leads` table** — durable store; survives any downstream failures.
2. **`Info@GenerationConscious.co`** via Resend — email notification sent immediately.
3. **Pipedrive** — a Person and a Deal are created via the Pipedrive API.

If either notification fails, the Supabase row persists with `emailed=false` or
`pushed_to_pipedrive=false` so the team can retry manually. Contact the team at
`Info@GenerationConscious.co` or text (516) 619-6174.

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

---

## Project Structure

```
genco-chatbot/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app, CORS, static widget mount
│   │   ├── config.py        # pydantic-settings Settings class
│   │   ├── llm.py           # OpenRouter completions + fallback
│   │   ├── rag/             # ingest, embed, retrieve
│   │   ├── chat/            # router, prompts, memory
│   │   ├── leads/           # capture_lead, notify, Pipedrive
│   │   └── guardrails/      # rate limit, cost cap, injection guard
│   ├── knowledge_base/      # *.md source documents
│   ├── tests/               # pytest suite
│   ├── eval/                # run_eval.py harness
│   ├── schema.sql           # Supabase schema (apply once)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── render.yaml
├── widget/
│   └── dist/
│       └── widget.js        # compiled embed widget
├── .env.example
├── VERIFICATION.md          # deferred live-key checks
├── LAUNCH_CHECKLIST.md      # pre-launch gate checklist
└── README.md                # this file
```
