# Verification Checklist — Deferred Live-Key Checks

These checks require real credentials and a running Supabase project. Run them after all env vars
in `.env` are filled in and the backend is reachable.

---

## 1. Apply the database schema

Open the Supabase SQL editor for your project and run the contents of `backend/schema.sql`.
Confirm that the tables `kb_documents`, `leads`, `chat_messages`, and the `match_documents`
function all appear in the Table Editor.

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
python -m pytest tests/test_retrieval.py -v
```

Expected: all assertions pass with cosine-similarity scores > 0.2. A score below 0.2 on any
fixture query indicates the embeddings or the `match_documents` threshold need tuning.

---

## 4. Live chat round-trip

Start the backend:

```bash
cd backend
uvicorn app.main:app --reload
```

Send a grounded question:

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"verify-001","message":"Do you ship to New York?"}' | python -m json.tool
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
the same IP. Expected: requests beyond the limit return HTTP 429.

**Cost cap:** temporarily set `DAILY_COST_CAP_USD=0.00001` in `.env` and restart the server,
then send a chat message. Expected: the reply uses the fallback model or returns the cost-cap
error message. Restore `DAILY_COST_CAP_USD` to the real value afterward.

---

## 8. LangFuse traces

After running any of the above live-chat steps, open [cloud.langfuse.com](https://cloud.langfuse.com)
and navigate to your project. Confirm:

- A trace appears for each `/chat` call.
- Each trace spans retrieve → generate → respond with latency and token-usage metadata.
- Escalation events appear with the `escalation` tag where applicable.

---

All eight checks passing = system is production-ready.
