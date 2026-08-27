# Verification Checklist — Deferred Live-Key Checks

These checks require real credentials and a running Supabase project. Run them after the env vars
in `backend/.env` are filled in (`cp .env.example backend/.env`, then fill — the backend reads
only `backend/.env`) and the backend is reachable.

**Not every check applies to every mode.** In the default `BOT_MODE=faq` the bot calls no AI
service, so the checks that exercise embeddings, the LLM, the cost cap, and tracing are skipped.
Each check below is labelled with the mode(s) it applies to.

| Mode | Run these, in this order | Skip |
|---|---|---|
| `faq` (default) | **1 → 2 → 10 → 11 → 12 → 4 → 6 → 7** | 3, 5, 8, 9 (and the cost-cap half of 7) |
| `generative` | **1 → 2 → 3 → 4 → 5 → 6 → 7 → 8** (9 optional) | 10, 11, 12 |

> **On the numbering:** checks 1–9 keep the numbers they have always had — the README and a couple
> of code comments (`chat/router.py`) reference them by number — so the FAQ-mode checks added with
> this mode are numbered 10–12 at the end rather than renumbering the file. Read them in the run
> order above, not top to bottom.

---

## 1. Apply the database schema

**Applies to: both modes.**

Open the Supabase SQL editor for your project and run the contents of
`backend/app/rag/schema.sql`. The whole file is idempotent — re-running it on an existing project
upgrades it in place, which is exactly what an existing deployment needs after the FAQ-mode change.

Confirm in the Table Editor that these all exist:

- tables `kb_documents`, `chat_sessions`, `chat_messages`, `leads`, and **`faq_misses`**,
- column **`kb_documents.content_tsv`** (generated `tsvector`) with the GIN index
  `kb_documents_content_tsv_idx`,
- column **`chat_sessions.flow_state`** (`jsonb`, nullable),
- functions `match_documents` (generative) and **`match_documents_fts`** (FAQ).

`kb_documents.embedding` is now **nullable** — FAQ mode stores chunks with no vector at all.

The KB vector index is **HNSW** (`using hnsw (embedding vector_cosine_ops)`), chosen over ivfflat
because the KB is tiny (~20-40 chunks): ivfflat with many lists leaves most lists empty and a
single-probe query returns little, which would show up here as artificially low similarity. HNSW
needs no list tuning and no post-insert training. (This matters in generative mode only; FAQ mode
never reads the vector column.)

---

## 2. Ingest the knowledge base

**Applies to: both modes.**

```bash
cd backend
source ../venv/bin/activate
python -m app.rag.ingest
```

Expected: the script logs each markdown file it processes and prints a count of upserted rows.
Confirm in Supabase → Table Editor → `kb_documents` that rows are present.

**In FAQ mode the ingest makes no embedding calls** — it runs fine with `EMBEDDING_API_KEY` blank
and stores every row with `embedding = NULL`. Confirm the matching column is populated instead:

```sql
select count(*) as chunks,
       count(content_tsv) as searchable,
       count(embedding)   as embedded
from kb_documents;
```

Expected in FAQ mode: `chunks = searchable`, `embedded = 0`. In generative mode all three match.

Re-run this check after **every** KB edit — in FAQ mode the KB text is the bot's script, shown to
visitors word for word.

---

## 3. Retrieval quality test

**Applies to: generative mode only** (it measures cosine similarity from live embeddings). FAQ
mode's equivalent is check 10.

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

**Applies to: both modes** (the expectations differ; see below).

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

**Every response, in both modes, carries all four contract keys and HTTP 200:**
`session_id`, `reply`, `retrieval_scores`, `quick_replies`.

- **FAQ mode:** `reply` is a KB chunk **word for word** (diff it against
  `backend/knowledge_base/shipping_and_tax.md` if unsure — it should match exactly, no summarizing
  or prefixing). `quick_replies` is `["👍 Yes, that answered it", "✉️ No — ask the team"]`.
  `retrieval_scores` holds `ts_rank` values (a different scale from cosine — see check 10).
- **Generative mode:** `reply` references the KB shipping fact (USPS live rates; NY sales tax) in
  the model's own words, `quick_replies` is `[]`, and `retrieval_scores` should contain at least
  one score >= 0.25 (the `LOW_SIMILARITY` escalation bar — below it the bot routes to the team
  instead of answering).

The response's `session_id` is a **server-minted UUID** (`chat_sessions.id` is a Postgres uuid
column). To continue the same conversation, pass that exact UUID back in the next request.
A hand-typed non-UUID value (e.g. `"verify-001"`) is ignored: the server mints a fresh session
and returns its UUID instead, so history saved under a made-up id will never be found. This bites
harder in FAQ mode — `flow_state` is stored on the session row, so a rotating session id loses the
conversation's place in the flow on every turn.

---

## 5. Lead capture end-to-end (wholesale + refill)

**Applies to: generative mode only** — this one-shot POST relies on the model parsing every field
out of a single sentence. FAQ mode collects the same fields one at a time through the guided flow;
verify it with checks 11 and 12, which land in exactly the same three places.

Run a simulated wholesale conversation until the bot emits a `capture_lead` tool call, or POST
directly:

```bash
curl -s -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "I want to buy wholesale. My name is Test User, email test@example.com, phone 5551234567, org Acme Corp, and I estimate 500 sheets."
  }' | python -m json.tool
```

(As in check 4, `session_id` is omitted — the server mints a UUID session and returns it. For a
multi-turn simulation, reuse the returned `session_id` on each subsequent request.)

Verify all three of the following after the call:

- **Supabase:** a row appears in the `leads` table with `intent=wholesale`, `emailed=true`,
  `pushed_to_pipedrive=true`.
- **Email:** `Info@GenerationConscious.co` receives the lead notification (check inbox).
- **Pipedrive:** a new Person and Deal appear in your Pipedrive account.

Repeat for `intent=refill_station` with fields: `name`, `email`, `phone`, `organization`,
`num_laundry_rooms`, `num_students`.

---

## 6. Eval harness

**Applies to: both modes.**

Offline, with no keys and no backend running:

```bash
# from the REPO ROOT — eval/ lives at the repo root, not under backend/
python eval/run_eval.py --mock
```

Expected: every routing case passes, then a `FAQ mode (6 cases)` section where every case passes.
The FAQ fixtures are answered out of the real `backend/knowledge_base/` markdown, so a failure
there means the **KB itself** no longer carries the words the bot is expected to say (e.g. someone
edited `shipping_and_tax.md` and dropped "USPS") — fix the KB, don't loosen the fixture.

Against a running backend:

```bash
python eval/run_eval.py https://YOUR-BACKEND-HOST
```

Expected: per-question pass/fail with grounding scores. Review any failures — low scores indicate
KB gaps; re-ingest after editing the markdown files. Note that the `eval/test_set.jsonl` routing
cases were written against generative-mode wording, so against a live **FAQ** backend the `FAQ
mode` section is the meaningful half; a few routing cases classify differently because the reply
is now verbatim KB text.

---

## 7. Rate-limit and cost-cap fallbacks

**Rate limit — applies to both modes.** Send more than `RATE_LIMIT_PER_MINUTE` (default 20)
requests in one minute from the same client IP (the limiter keys on `X-Forwarded-For`, so
rotating/omitting `session_id` does NOT bypass it). Expected: requests beyond the limit return HTTP
200 with the friendly throttle message ("You're sending messages quickly — give me a moment and try
again.") and `quick_replies: []`. Throttled turns — and, in generative mode, cost-capped and
injection-declined turns — are **not persisted or traced by design**: they will not appear in
`GET /history` or LangFuse, and they echo the client-sent `session_id` (or `""`) rather than
minting one.

**Cost cap — generative mode only.** Temporarily set `DAILY_COST_CAP_USD=0.00001` in
`backend/.env` and restart the server, then send a chat message. Expected: the reply is the static
cost-cap message ("I'm momentarily unavailable…") — the fallback model is NOT invoked on cost-cap,
only on primary model failure. Restore `DAILY_COST_CAP_USD` to the real value afterward. In FAQ
mode there is no spend to cap and the gate is skipped entirely.

---

## 8. LangFuse traces

**Applies to: generative mode only.** FAQ mode calls no model and emits no traces (its LangFuse
keys stay blank and `trace_turn` no-ops without them). Its operational visibility is the
`faq_misses` table (check 11) plus the `emailed` / `pushed_to_pipedrive` flags on `leads`.

After running any of the above live-chat steps, open [cloud.langfuse.com](https://cloud.langfuse.com)
and navigate to your project. Confirm:

- A trace appears for each `/chat` call.
- Each trace spans retrieve → generate → respond with latency and token-usage metadata.
- Escalation events appear with the `escalation` tag where applicable.

---

## 9. (Optional) DeepEval faithfulness gate

**Applies to: generative mode only** — it judges model-written text, and FAQ mode writes none.

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

## 10. FAQ match quality spot-check

**Applies to: FAQ mode.** This is FAQ mode's counterpart to check 3 — the same question ("is
retrieval finding the right entry?") measured with `ts_rank` instead of cosine similarity.

With the backend running and the KB ingested, ask a handful of real questions and read both the
reply and the ranks:

```bash
for q in "how much is shipping" \
         "do you charge sales tax" \
         "how do I buy sheets" \
         "do you do bulk orders" \
         "how do refill stations work" \
         "do you sell dog food"; do
  echo "--- $q"
  curl -s -X POST http://localhost:8000/chat \
    -H "Content-Type: application/json" \
    -d "{\"message\": \"$q\"}" | python -m json.tool
done
```

Expected:

- The first five return the **matching KB chunk verbatim** with
  `quick_replies: ["👍 Yes, that answered it", "✉️ No — ask the team"]`, and
  `retrieval_scores[0]` at or above `FTS_MIN_RANK` (`0.05`, in `backend/app/rag/fts.py`).
- "do you do bulk orders" additionally offers `"Start wholesale inquiry"` — the answer comes from
  `wholesale.md`, which is mapped to that trigger in `chat/flows.py`.
- "do you sell dog food" returns *"I couldn't find that in our FAQ — but our team can answer it
  personally."* with `quick_replies: ["✉️ Send my question to the team"]`. **A wrong-but-confident
  answer here is the failure mode that matters** — it means `FTS_MIN_RANK` is too low.

Tuning: raise `FTS_MIN_RANK` if weak matches slip through as answers; lower it if good questions
fall to escalation. Then confirm in Supabase that `faq_misses` gained one row per no-match, with
`answered = false`.

---

## 11. FAQ feedback round-trip → lead lands

**Applies to: FAQ mode.** This is the business goal of the whole pivot: a question the FAQ cannot
answer reaches a human. Run it in the widget, not with curl, so the buttons are exercised too
(`cd widget && python -m http.server 5500`, then `http://localhost:5500/test.html`).

1. Ask a question the KB **can** answer and tap **"👍 Yes, that answered it"**.
   Expected: a short thanks, no buttons, and a `faq_misses` row with `answered = true`.
2. Ask another answerable question and tap **"✉️ No — ask the team"**.
   Expected: a `faq_misses` row with `answered = false`, and the bot starts the guided question
   flow with the original question already captured — it asks for your **name** next, not the
   question again.
3. Finish the flow (name, then email). Expected, all three:
   - **Supabase:** a `leads` row with `intent=question`, `emailed=true`,
     `pushed_to_pipedrive=true`, and the original question in the payload.
   - **Email:** `Info@GenerationConscious.co` receives it.
   - **Pipedrive:** a Person and a Deal appear.

Then ask something unanswerable ("do you sell dog food") and tap **"✉️ Send my question to the
team"**. Expected: the same lead path, and **no duplicate** `faq_misses` row — the miss was
already recorded when it happened.

---

## 12. FAQ guided lead flow end-to-end

**Applies to: FAQ mode.** Check 11 covers the `question` intent; this covers the two revenue
intents and the flow's error handling. Run each in the widget from the greeting buttons.

**Refill stations** — tap "Buy Refill Stations". Expected: one prompt at a time, in order —
name, email, phone, organization, number of laundry rooms, number of students. Then a
confirmation, and the lead in Supabase + inbox + Pipedrive with `intent=refill_station`.

**Wholesale** — ask "do you do bulk orders", then tap "Start wholesale inquiry". Expected: name,
email, phone, organization, estimated sheets → confirmation → lead with `intent=wholesale` and
`estimated_sheets` stored as a **number**.

While in a flow, confirm the guard rails:

- Typing `not-an-email` at the email step re-prompts and **stays on that field** (nothing is
  stored).
- Typing `lots of them` at a count step re-prompts asking for digits.
- Typing `cancel` at any step exits to the greeting buttons.
- Reloading the page mid-flow and answering the next field continues where it left off — the
  widget keeps the session id in `localStorage` and `flow_state` lives on that session row.

**Buy Sheets** — tap it and confirm the reply carries
`https://generationconscious.co/product/laundry-detergent-sheets/` (the home-delivery product page,
**not** `/checkout/` and not `/shop/`) and that no lead flow starts.

---

Checks 1, 2, 4, 6, 7 (rate limit), 10, 11, and 12 passing = **FAQ mode is production-ready**.
Checks 1–8 passing = generative mode is production-ready; check 9 is an optional deeper gate there.
