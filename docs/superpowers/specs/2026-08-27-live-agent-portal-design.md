# Genco Chatbot — Live Agent Portal & FAQ Publishing

**Date:** 2026-08-27
**Status:** Approved (design), pending implementation
**Builds on:** `2026-08-12-faq-match-mode-design.md` (FAQ-match mode, shipped 2026-08-27).
Everything in that design carries forward unchanged. This adds a live human layer *above* the
email escalation loop; the email loop remains the floor under every path.

## Motivation

Greg's message of 2026-08-11 described one system. FAQ-match mode shipped the first half of it.
This spec covers the rest, traced to his words:

| What Greg asked for | Covered by |
|---|---|
| "read the question and select a best fit answer from the FAQs" | Shipped — `rag/fts.py` |
| "ask the customer… did it answer your question" | Shipped — feedback buttons |
| "if no, it sends the question to info@generationconscious.co" | Shipped — lead pipeline |
| "I will personally respond" | Shipped — email reply loop |
| "please wait for a live customer agent" | §3 Connecting state |
| "that live customer agent will be me" | §2 Single-agent auth |
| "sign in to a portal" | §2, §6 |
| "chatting back and forth with website visitors" | §3, §4, §6 |
| "if the FAQ cant answer their question" | §4 — the only trigger for live chat |
| **"help us add to the FAQ so it's even more robust"** | **§7 FAQ publishing** |
| **"require me to answer less questions"** | **§7 — the point of the whole feature** |

The last two are the ones that make this pay for itself. A portal that only adds a chat window
adds work to Greg's day. A portal that turns each answered question into a permanent FAQ entry
reduces it, permanently. **Live chat volume should fall over time. If it doesn't, this feature
failed.**

## Decisions

| Decision | Choice | Why |
|---|---|---|
| When live chat is offered | Only while Greg's heartbeat is fresh | Never promise a human who isn't there |
| Transport | HTTP polling, state in Postgres | A Render restart is invisible; a dropped socket is not |
| Contact capture | Email **before** connecting | A visitor who closes the tab mid-chat is still reachable |
| Auth | Single password + HMAC-signed cookie, stdlib only | One agent; a user table is premature |
| Concurrency | Greg may hold several chats at once | Simpler than a queue with position messaging, at this volume |
| Portal | One self-contained HTML file at `/agent` | Matches how `widget.js` already ships |
| AI | **Still none, anywhere** | The anti-AI sales claim must survive this feature |

## Architecture

```
Visitor widget ──POST /chat───────────► FastAPI
               ──GET  /live/messages──►   ├─ BOT_MODE=faq
                                          │   ├─ rag/fts        unchanged
                                          │   ├─ chat/flows     + live states
                                          │   └─ leads          unchanged
Greg's browser ──POST /agent/*─────────►   ├─ live/             presence, queue, relay
  (portal at /agent)                       ├─ agent_auth        password → signed cookie
                                           └─ kb_publish        portal → kb_documents
```

Both sides poll. Neither holds a connection. Every piece of conversation state lives in Postgres,
so a process restart mid-conversation loses nothing.

---

## 1. Presence and availability

New table `agent_presence`, exactly one row.

```sql
create table if not exists agent_presence (
  id           text primary key default 'greg',
  available    boolean default false,
  last_seen_at timestamptz default now()
);
```

The portal sends `POST /agent/heartbeat {"available": true|false}` every **15 seconds** while
open. Greg is considered reachable when `available = true` **and** `last_seen_at` is within
**45 seconds** (three missed heartbeats). Closing the tab stops the heartbeat, so availability
expires on its own — Greg cannot accidentally leave himself "on" overnight.

`is_agent_available() -> bool` is the single function that answers this, used by both the chat
flow and the portal.

## 2. Authentication

Stdlib only — no new dependency.

- `AGENT_PASSWORD` (env). `POST /agent/login` compares with `hmac.compare_digest` (constant time).
- On success, sets cookie `gc_agent` = `base64(payload).base64(hmac_sha256(payload, AGENT_SESSION_SECRET))`
  where payload is `{"exp": <unix ts>}`, 12-hour expiry.
- Cookie flags: `HttpOnly`, `Secure`, `SameSite=Strict`, `Path=/agent`.
  `SameSite=Strict` is what stops the WordPress origin from ever reaching these routes with
  credentials, despite the permissive CORS policy the widget needs.
  **`Secure` means the cookie is not set over plain HTTP**, so local development on
  `http://localhost` would be unable to log in. The flag is therefore set from
  `request.url.scheme == "https"`, keeping production strict without an env switch someone can
  forget to flip back.
- Every `/agent/*` route except `/agent` (the login page) and `/agent/login` requires a valid
  unexpired signature, else **401**.
- `POST /agent/login` is rate-limited by IP using the existing `guardrails.RateLimiter`, at
  **5 attempts per minute** — a separate instance from the chat limiter.
- `AGENT_SESSION_SECRET` missing or empty → every `/agent/*` route returns 503 and logs once at
  startup. The portal fails closed, never open.

## 3. Live chat lifecycle

New table:

```sql
create table if not exists live_chats (
  id            uuid primary key default gen_random_uuid(),
  session_id    uuid not null,
  lead_id       uuid,
  status        text not null default 'waiting',  -- waiting | active | ended
  question      text,
  started_at    timestamptz default now(),
  accepted_at   timestamptz,
  ended_at      timestamptz,
  ended_reason  text   -- agent_ended | agent_dropped | visitor_left | not_accepted
);
create index if not exists live_chats_status_idx on live_chats(status, started_at);
```

Agent turns are stored in the existing `chat_messages` table with `role = 'agent'` — no schema
change; the column is free text.

**States and transitions**

- **waiting** — visitor has given their email and is waiting. The portal shows it with an audible
  alert and a tab-title flash.
- **active** — Greg opened it. Set on his first fetch of the transcript.
- **ended** — one of four reasons:
  - `agent_ended` — Greg clicked End chat.
  - `agent_dropped` — his heartbeat went stale (>45s) mid-chat.
  - `visitor_left` — no visitor poll for >2 minutes.
  - `not_accepted` — nobody opened it within 60 seconds of `waiting`.

**Every ended chat falls into the email loop.** On end, the lead created at connect time is
notified: Resend email to `Info@GenerationConscious.co` carrying the full transcript and the
end reason, then Pipedrive. This reuses `escalation.capture_lead`'s store-first ordering
unchanged. `not_accepted` and `agent_dropped` additionally tell the visitor so in chat:

> "Looks like we got disconnected — Greg has your email and will follow up personally."

This is the mechanism that makes live chat safe to offer. There is no exit from a live chat that
does not leave a lead behind.

## 4. Flow-state integration

`chat/flows.py` gains three states. FAQ matching, feedback, and the existing lead flows are
untouched.

- **`awaiting_live_consent`** — reached from the "✉️ No — ask the team" / "Send my question to
  the team" taps **only when `is_agent_available()` is true**. Reply:

  > "Greg from our team is here right now — want to talk to him directly, or should I take your
  > details so he can email you?"

  Quick replies: `["💬 Chat with Greg now", "✉️ Just email me"]`. "Just email me" falls through
  to today's question lead flow, unchanged.

- **`live_collect_email`** — one field, reusing the existing email validator:

  > "Great — what's your email, so Greg can follow up if we get cut off?"

  On a valid address: create the lead row (store-first, **not yet notified**), create the
  `live_chats` row as `waiting`, transition to `live`. Reply is the connecting state Greg
  described:

  > "Thanks — connecting you to Greg now…"

- **`live`** — the visitor's messages are stored and relayed, **not matched against the FAQ**.
  `handle_turn` returns an empty reply with `live: true` so the widget suppresses the bot bubble
  and shows a typing indicator instead. Exits on any of the four end reasons above.

If `is_agent_available()` is false — the normal case — none of these states are entered and the
conversation behaves exactly as it does today. **The zero-AI guarantee is unaffected: no state
here calls a model.**

## 5. Endpoints

**Visitor-facing** (CORS-open, no auth):

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat` | Unchanged, plus a `live` boolean in the response |
| GET | `/live/messages?session_id=&after=` | Agent messages since `after` (an ISO timestamp); also reports whether the chat ended, and why |

The widget polls `/live/messages` every **2 seconds** while `live` is true, and not at all
otherwise. Polling stops the moment the response reports the chat ended.

**Agent-facing** (cookie auth, `SameSite=Strict`):

| Method | Path | Purpose |
|---|---|---|
| GET | `/agent` | The portal (login screen when unauthenticated) |
| POST | `/agent/login` · `/agent/logout` | Session |
| POST | `/agent/heartbeat` | Presence, every 15s |
| GET | `/agent/queue` | Waiting + active chats |
| GET | `/agent/chat/{session_id}` | Transcript; marks `waiting` → `active` |
| POST | `/agent/chat/{session_id}/message` | Send a reply |
| POST | `/agent/chat/{session_id}/end` | End the chat |
| GET | `/agent/faq-gaps` | Unanswered questions, grouped and counted |
| POST | `/agent/faq-entry` | Publish an answer to the KB |

## 6. The portal

One self-contained HTML file, `portal/dist/portal.html`, served by FastAPI at `/agent` — the same
shipping model as `widget/dist/widget.js`, and bundled the same way by the Dockerfile. Vanilla JS,
no build step, no framework. XSS-safe rendering via `textContent` throughout, as the widget
already does: visitor-typed text is untrusted and must never reach `innerHTML`.

**Two panes.**

**Live** — availability toggle (drives the heartbeat), the list of waiting and active chats, and
the open conversation with a send box and an End chat button. A new waiting chat plays a sound and
flashes the tab title. Each chat shows the visitor's original question and email.

**FAQ gaps** — the reason this feature pays for itself. Every question the FAQ could not answer,
grouped by normalised text and ordered by how often it has been asked, each with:

- the question, and how many times it has been asked
- a **"Write the answer"** textarea and a title field
- **Publish to FAQ** → live in the knowledge base within seconds

Grouping is done in Python using the existing `flows._norm`, not in SQL — it reuses one
normalisation rule across the codebase and avoids a generated column that would split groups on
inconsistent spacing.

## 7. FAQ publishing and the ingest ownership fix

**This is the bug that would have eaten Greg's work.** `ingest.py` currently ends with:

```python
sb.table("kb_documents").delete().not_.in_("content_hash", list(rows_by_hash)).execute()
```

Any row not present in the markdown files is deleted. The first re-ingest after Greg published an
FAQ entry from the portal would silently destroy every one of them.

**Fix:** tag rows with their owner and prune only file-owned rows.

```sql
alter table kb_documents add column if not exists managed_by text not null default 'file';
```

- `'file'` — authored in `backend/knowledge_base/*.md`, owned by ingest.
- `'portal'` — written by Greg, owned by the database. **Ingest must never touch these.**

The prune becomes:

```python
sb.table("kb_documents").delete() \
    .eq("managed_by", "file") \
    .not_.in_("content_hash", list(rows_by_hash)) \
    .execute()
```

and the upsert sets `"managed_by": "file"` explicitly on every row it writes.

**Publishing** (`POST /agent/faq-entry`) inserts into `kb_documents` with `managed_by='portal'`,
`embedding=NULL`, `metadata={"source": "portal", "title": <title>}`, and the same
`sha256(content)` `content_hash` ingest uses. The `content_tsv` column is generated, so the entry
is searchable the moment it commits — no re-ingest, no redeploy.

Publishing also marks the `faq_misses` rows in that group resolved, so the gap leaves the list.

```sql
alter table faq_misses add column if not exists resolved boolean not null default false;
```

A published entry that turns out to be wrong is edited or deleted the same way it was created —
from the portal. Entries authored in the repo are still edited in the repo.

## Error handling

Every rule from the FAQ-match design applies unchanged. Additions:

- Any `/live/*` or `/agent/*` database failure → log, return a well-formed empty result, HTTP 200
  for visitor routes. A polling loop must never see a 500.
- Presence lookup failure → treated as **unavailable**. Failing closed sends the visitor down the
  email path, which always works.
- The widget's poll failing repeatedly (5 consecutive) → stop polling, tell the visitor Greg will
  email them, reset to idle. Their lead already exists.
- Publishing an FAQ entry whose `content_hash` already exists → upsert, not an error. Greg
  re-publishing a corrected answer replaces it.
- `capture_lead` failing at connect time → do **not** enter live chat; fall back to the existing
  question lead flow. Never start a conversation you cannot follow up on.

## Testing

TDD per CLAUDE.md. Mocked Supabase throughout; no live Postgres in the suite.

- **Presence:** fresh/stale heartbeat boundaries at 45s, `available=false` while fresh, DB failure → unavailable.
- **Auth:** valid login sets a signed cookie; tampered signature, expired payload, and missing cookie all 401; login rate limit trips at 6 attempts; missing `AGENT_SESSION_SECRET` → 503.
- **Flow states:** consent offered only when available; "Just email me" falls through to today's flow byte-for-byte; invalid email re-prompts; `live` state bypasses FAQ matching entirely.
- **Lifecycle:** each of the four end reasons ends the chat, notifies the lead exactly once, and resets flow state to idle.
- **Relay:** `/live/messages` returns only messages after the cursor, in order; agent messages never leak into another session.
- **Ingest ownership (regression):** seed one `'file'` row and one `'portal'` row, run `ingest_all()`, assert the portal row survives. **This test is the point of §7 — it must fail before the fix.**
- **Publishing:** inserts with `managed_by='portal'` and a correct hash; re-publishing upserts; the gap's `faq_misses` rows are marked resolved.
- **Contract:** `/chat` returns five keys on every path, always HTTP 200. Existing four-key assertions are updated, not deleted.
- **Zero-AI guard:** extend the existing source scan to the new modules — no model imports anywhere in the live path.

## Schema changes

Appended to `backend/app/rag/schema.sql`, which stays idempotent and re-runnable:

1. `create table agent_presence`
2. `create table live_chats` + status index
3. `alter table kb_documents add column managed_by text not null default 'file'`
4. `alter table faq_misses add column resolved boolean not null default false`

Existing rows default to `managed_by='file'`, which is correct — everything currently in the table
came from the markdown files.

## New settings

| Variable | Default | Notes |
|---|---|---|
| `AGENT_PASSWORD` | `""` | Empty disables the portal entirely |
| `AGENT_SESSION_SECRET` | `""` | Empty → `/agent/*` returns 503 |
| `AGENT_HEARTBEAT_TTL_SECONDS` | `45` | Availability window |
| `LIVE_ACCEPT_TIMEOUT_SECONDS` | `60` | Before `not_accepted` |
| `LIVE_VISITOR_IDLE_SECONDS` | `120` | Before `visitor_left` |

With `AGENT_PASSWORD` unset the entire feature is dormant and the bot behaves exactly as it does
today — which is also the safe rollout position.

## Docs impact

- **README:** portal setup, the two new secrets, and how FAQ publishing interacts with the
  markdown knowledge base (the `managed_by` split).
- **VERIFICATION:** presence expiry, each of the four end reasons, and the ingest-ownership
  regression check.
- **LAUNCH_CHECKLIST:** portal reachable over HTTPS; `AGENT_PASSWORD` and `AGENT_SESSION_SECRET`
  set; a real end-to-end live chat; a published FAQ entry surviving a re-ingest.

## Out of scope (v1)

- More than one agent. The design is single-agent throughout; multiple agents means a real user
  table and assignment rules, and should wait until a second person actually needs access.
- Push/SMS alerts when Greg is signed out. Presence gating means a signed-out Greg is never
  offered to a visitor, so there is nothing to alert him about.
- Canned replies, file transfer, typing indicators from Greg's side, chat ratings.
- Editing repo-authored KB files from the portal. Portal entries are portal-owned; file entries
  stay file-owned. Mixing the two is how you lose work.
- Any WordPress change. The embed is unchanged — the portal is a separate page on the backend.
