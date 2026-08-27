# Launch Checklist

Work through these items in order before going live. Check each box once verified.

The bot ships in **FAQ-match mode** (`BOT_MODE=faq`): it calls no AI service and replies with the
GC team's own FAQ answers, word for word. Everything under
[Generative mode only](#generative-mode-only) is **not** on the critical path — skip that section
entirely unless you are deliberately launching with `BOT_MODE=generative`.

---

## Mode & Database

- [ ] `BOT_MODE=faq` is set in the production environment (Render → Environment). It is declared
  `sync: false` in `render.yaml`, so it is **not** inherited — an unset value falls back to the
  code default (`faq`), but set it explicitly so the mode is visible to whoever reads the dashboard.
- [ ] No AI keys are required for launch. `OPENROUTER_API_KEY`, `EMBEDDING_API_KEY`, and the
  `LANGFUSE_*` values may stay blank; confirm the service still boots and `/health` is green with
  them empty.
- [ ] `backend/app/rag/schema.sql` has been **re-applied** in the Supabase SQL editor. The whole
  file is re-runnable and upgrades an existing project in place, adding the FAQ objects:
  `kb_documents.content_tsv` + its GIN index, `match_documents_fts`, `chat_sessions.flow_state`,
  and the `faq_misses` table (and making `kb_documents.embedding` nullable).
- [ ] That same re-apply added the **agent-portal ownership columns**: `kb_documents.managed_by`
  (`text not null default 'file'`) and `faq_misses.resolved` (`boolean not null default false`).
  Confirm both exist in the Table Editor **before** anyone publishes from the portal — without
  `managed_by`, the next re-ingest deletes every published answer.
- [ ] KB re-ingested **after** the schema change so every chunk is searchable:
  ```bash
  cd backend && python -m app.rag.ingest
  ```
  Then confirm in Supabase that `select count(*), count(content_tsv) from kb_documents;` returns
  two equal numbers (FAQ mode stores rows with `embedding = NULL` — that is expected).

---

## Embed

- [x] Widget branding confirmed with Greg (approved 2026-08-10): the `CONFIG` block at the top of `widget/dist/widget.js`
  carries `PRIMARY` `#FF0719` and the site-wordmark `LOGO` URL derived from the live site (2026-08)
  — swap in team-approved values if they differ.
- [ ] Widget script tag is live on the Generation Conscious WordPress site.
  Paste the following into a header/footer plugin or Elementor custom-code block:
  ```html
  <script src="https://YOUR-BACKEND-HOST/widget/widget.js"
          data-backend-url="https://YOUR-BACKEND-HOST"></script>
  ```
  Replace `YOUR-BACKEND-HOST` with the actual Render (or Railway/Fly) service URL.
- [ ] Widget JS is reachable in production: `curl -I <embed src URL>` returns HTTP 200.
  The default Dockerfile now bundles `widget/dist/` (build context = repo root), so this should
  pass out of the box; this check guards against a regression or a misconfigured CDN.
- [ ] Quick-reply buttons render in production: the greeting shows Buy Sheets / Buy Refill
  Stations / Question for the team, and an answered question shows the 👍 / ✉️ feedback pair.

---

## Security & CORS

- [ ] `ALLOWED_ORIGINS` is set to exactly `https://generationconscious.co` in the production
  environment — remove every dev origin (`http://localhost:8000`, `http://localhost:5500`,
  `http://127.0.0.1:5500`) before go-live.
- [ ] Rate limiting is keyed on client IP (`X-Forwarded-For`). Confirm Render forwards the real
  client IP (it sets `X-Forwarded-For` by default) so `RATE_LIMIT_PER_MINUTE` is enforced per IP,
  not per browser-supplied session. In FAQ mode this is the **only** request gate — there is no
  model to protect from prompt injection and no spend to cap, so the injection guard and the daily
  cost cap (the generative-mode backstops) do not run.

---

## Knowledge Base

- [ ] Every KB chunk reads as a complete, standalone answer. **In FAQ mode the chunk is shown to
  the visitor verbatim** — no summarizing, no rewriting — so anything that reads as a fragment,
  an internal note, or a "see above" ships to customers as-is. Skim
  `backend/knowledge_base/*.md` heading by heading (each heading starts a chunk).
- [ ] The home-delivery URL hardcoded in the KB (`backend/knowledge_base/products_and_purchasing.md`)
  and in the system prompt (`backend/app/chat/prompts.py`) is confirmed as the product page:
  `https://generationconscious.co/product/laundry-detergent-sheets/`
  (already set — verify no accidental edits; there is no `STORE_URL` variable, the URL is plain
  prose in those two files).
- [x] GC team has approved the Mission copy (approved 2026-08-10) in `backend/knowledge_base/learn_more.md` (drawn from
  `https://generationconscious.co/about/`, fetched 2026-08-10). If the wording changes, edit the
  file and re-run `python -m app.rag.ingest` so the live KB picks it up.
- [ ] Learn More links verified by a **human click-through in a logged-out browser** — both are
  hardcoded in `backend/knowledge_base/learn_more.md`:
  - the Lifecycle Assessment Google Drive link renders the PDF (sharing must be
    "anyone with the link"),
  - the Instagram refill-station post displays the refill station.
  Do not trust a `curl` HTTP 200 for either: Drive returns 200 on its access-denied page and
  Instagram can return a 200 shell for deleted posts.
- [ ] Match quality spot-checked against production (VERIFICATION check 10): the seeded questions
  return the right entry, and a question the KB cannot answer ("do you sell dog food") returns the
  *"I couldn't find that in our FAQ"* offer rather than a confident wrong answer.

---

## Lead Routing

- [ ] FAQ feedback round-trip verified end-to-end (VERIFICATION check 11): ask a question, tap
  **"✉️ No — ask the team"**, complete the guided flow (name → email), and confirm the lead lands
  in the Supabase `leads` table **and** the `Info@GenerationConscious.co` inbox **and** Pipedrive.
  This is the business goal of FAQ mode — do not wave it through.
- [ ] Escalation email verified end-to-end: send a test wholesale inquiry and confirm
  `Info@GenerationConscious.co` receives the notification.
- [ ] Lead capture verified for the **wholesale** flow (Name, Email, Phone, Organization,
  Estimated Sheets → Supabase row + email + Pipedrive). In FAQ mode this is reached by asking a
  wholesale question and tapping "Start wholesale inquiry".
- [ ] Lead capture verified for the **refill-station** flow (Name, Email, Phone, Organization,
  Laundry Rooms, Students/Tenants → Supabase row + email + Pipedrive). In FAQ mode this is the
  "Buy Refill Stations" greeting button.
- [ ] Pipedrive person and deal created correctly for both flows.
- [ ] `faq_misses` is filling up: after the checks above, the table holds one row per 👍/✉️ tap and
  per no-match. This is the FAQ backlog the team reviews to grow the KB — confirm someone owns
  that review. The portal at `/agent` is where that review happens (see
  [Agent portal](#agent-portal)); the Supabase table editor is the fallback.

---

## Agent portal

`/agent` is the team's side of the bot: every question the FAQ could not answer, each with a
**Publish to FAQ** box that turns it into a permanent entry. It fails closed — with either secret
blank, every `/agent/*` API route returns 503, the login page says so, and the visitor chat is
untouched.

- [ ] `AGENT_PASSWORD` and `AGENT_SESSION_SECRET` are both set in the production environment
  (Render → Environment; both are `sync: false` in `render.yaml`). Generate the secret with
  `python -c "import secrets;print(secrets.token_urlsafe(48))"` and keep it stable — rotating it
  signs everyone out. **Launching without the portal is a valid choice:** leave both blank, confirm
  `curl -s -o /dev/null -w '%{http_code}' https://YOUR-BACKEND-HOST/agent/faq-gaps` returns **503**,
  and skip the rest of this section.
- [ ] `/agent` is reachable **over HTTPS** in production and the login screen loads. The session
  cookie is `Secure` whenever the request scheme is `https`, so a plain-HTTP host cannot sign in.
- [ ] A wrong password is rejected — the page shows "Wrong password." and no session starts. Six
  attempts in a minute return "Too many attempts. Wait a minute." (login is limited to 5/min per
  IP, separately from the chat limiter).
- [ ] Publishing works end-to-end (VERIFICATION check 13): a gap publishes, leaves the list, and
  the bot answers that question with the new entry on the very next turn — no re-ingest, no
  redeploy.
- [ ] **A published entry survives a re-ingest.** Run `cd backend && python -m app.rag.ingest`,
  then confirm the `managed_by = 'portal'` row is still in `kb_documents`. If it is gone, ingest is
  deleting the team's work — do not launch (VERIFICATION check 13, step 5).
- [ ] Whoever edits the knowledge base knows the split: **portal entries are edited in the portal,
  `backend/knowledge_base/*.md` entries in the repo** (README → Two sources of knowledge).

---

## QA

- [ ] Mobile QA: widget opens, sends a message, displays a reply and its quick-reply buttons on
  iOS and Android.
- [ ] Desktop QA: widget opens, sends a message, displays a reply and its quick-reply buttons in
  Chrome and Safari/Firefox.
- [ ] Guided-flow QA (VERIFICATION check 12): an invalid email re-prompts without advancing, a
  non-numeric count re-prompts, and typing "cancel" exits to the greeting buttons.

---

## Generative mode only

**Skip this whole section while `BOT_MODE=faq`.** These items only apply if the service is
deliberately launched with `BOT_MODE=generative`, which calls OpenRouter and OpenAI on every turn.

- [ ] OpenRouter and OpenAI accounts exist with billing attached (the scope puts LLM costs on the
  company), and `OPENROUTER_API_KEY` + `EMBEDDING_API_KEY` are set in the production environment.
- [ ] `DAILY_COST_CAP_USD` is set to a value appropriate for expected traffic (e.g., `10.0`).
  Confirm the cost-cap path returns the static unavailable message (it does NOT call the fallback
  model). The fallback model (`OPENROUTER_MODEL_FALLBACK`) triggers only on primary model failure.
  Note the tracker is in-memory and per-process — pin the deploy to a single worker or the cap
  multiplies (see README → Managing Cost).
- [ ] LangFuse is receiving traces: open cloud.langfuse.com, send a test chat message, and
  confirm a trace appears with retrieve/generate/respond spans.

---

All boxes outside the generative-mode section checked = cleared for launch.
