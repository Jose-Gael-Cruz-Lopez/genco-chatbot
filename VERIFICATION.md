# Verification Checklist — Deferred Live-Key Checks

These checks require real credentials and a running Supabase project. Run them after all env vars
in `backend/.env` are filled in (`cp .env.example backend/.env`, then fill — the backend reads
only `backend/.env`) and the backend is reachable.

---

## 1. Apply the database schema

Open the Supabase SQL editor for your project and run the contents of
`backend/app/rag/schema.sql`. Confirm that the tables `kb_documents`, `leads`, `chat_messages`,
and the `match_documents` function all appear in the Table Editor.

The KB vector index is **HNSW** (`using hnsw (embedding vector_cosine_ops)`), chosen over ivfflat
because the KB is tiny (~20-40 chunks): ivfflat with many lists leaves most lists empty and a
single-probe query returns little, which would show up here as artificially low similarity. HNSW
needs no list tuning and no post-insert training.

---

## 2. Ingest the knowledge base

```bash
cd backend
source ../venv/bin/activate
python -m app.rag.ingest
```

Expected: the script logs each markdown file it processes and prints a count of upserted rows.
Confirm in Supabase → Table Editor → `kb_documents` that rows are present.

---

## 3. Retrieval quality test

```bash
cd backend
# The live-key tests gate on SHELL env vars (os.getenv), and pydantic-settings reads
# backend/.env internally WITHOUT exporting to the process environment — so source it
# into the shell first or the test silently skips:
set -a; source .env; set +a
python -m pytest tests/test_retrieval.py -v
```

Expected: all assertions pass with top cosine-similarity scores >= 0.25 — the live grounding
bar (`LOW_SIMILARITY` in `backend/app/escalation.py`), which the test imports directly.

**A "skipped" result is NOT a pass.** `1 skipped` means `SUPABASE_URL` / `EMBEDDING_API_KEY`
are not visible to the shell — source `backend/.env` as above and re-run until the test actually
executes. A top score below 0.25 on any fixture query indicates the embeddings or the
`match_documents` threshold need tuning.

---

## 4. Live chat round-trip

Start the backend:

```bash
cd backend
uvicorn app.main:app --reload
```

Send a grounded question (omit `session_id` on the first turn — it is optional):

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Do you ship to New York?"}' | python -m json.tool
```

Expected: `reply` references the KB shipping fact (USPS live rates; NY sales tax). The
`retrieval_scores` array should contain at least one score > 0.2.

---

## 5. Lead capture end-to-end (wholesale + refill)

Run a simulated wholesale conversation until the bot emits a `capture_lead` tool call, or POST
directly:

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "verify-wholesale-001",
    "message": "I want to buy wholesale. My name is Test User, email test@example.com, phone 5551234567, org Acme Corp, and I estimate 500 sheets."
  }' | python -m json.tool
```

Verify all three of the following after the call:

- **Supabase:** a row appears in the `leads` table with `intent=wholesale`, `emailed=true`,
  `pushed_to_pipedrive=true`.
- **Email:** `Info@GenerationConscious.co` receives the lead notification (check inbox).
- **Pipedrive:** a new Person and Deal appear in your Pipedrive account.

Repeat for `intent=refill_station` with fields: `name`, `email`, `phone`, `organization`,
`num_laundry_rooms`, `num_students`.

---

## 6. Eval harness

Deploy the backend (or run locally) and execute:

```bash
cd backend
python eval/run_eval.py https://YOUR-BACKEND-HOST
```

Expected: the script prints per-question pass/fail with grounding scores. Review any failures —
low scores indicate KB gaps; re-ingest after editing the markdown files.

---

## 7. Rate-limit and cost-cap fallbacks

**Rate limit:** send more than `RATE_LIMIT_PER_MINUTE` (default 20) requests in one minute from
the same client IP (the limiter keys on `X-Forwarded-For`, so rotating/omitting `session_id` does
NOT bypass it). Expected: requests beyond the limit return HTTP 200 with the friendly throttle
message ("You're sending messages quickly — give me a moment and try again.").

**Cost cap:** temporarily set `DAILY_COST_CAP_USD=0.00001` in `.env` and restart the server,
then send a chat message. Expected: the reply is the static cost-cap message ("I'm momentarily
unavailable…") — the fallback model is NOT invoked on cost-cap, only on primary model failure.
Restore `DAILY_COST_CAP_USD` to the real value afterward.

---

## 8. LangFuse traces

After running any of the above live-chat steps, open [cloud.langfuse.com](https://cloud.langfuse.com)
and navigate to your project. Confirm:

- A trace appears for each `/chat` call.
- Each trace spans retrieve → generate → respond with latency and token-usage metadata.
- Escalation events appear with the `escalation` tag where applicable.

---

## 9. (Optional) DeepEval faithfulness gate

Only if the optional ML extra is installed (`pip install -r backend/requirements-ml.txt`) and a
judge key is set:

```bash
export OPENAI_API_KEY=...                  # judge model
pytest backend/tests/test_faithfulness_eval.py -v
```

Expected: each grounded case scores ≥ 0.7 faithfulness. Without the dep or key, the module skips
(it never blocks the default suite). This is the deeper, judge-based complement to step 6's
keyword/routing eval.

---

All eight core checks passing = system is production-ready. Step 9 is an optional deeper gate.
