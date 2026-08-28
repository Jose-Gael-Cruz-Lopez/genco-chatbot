create extension if not exists vector;

create table if not exists kb_documents (
  id uuid primary key default gen_random_uuid(),
  content text not null,
  content_hash text unique,
  embedding vector(1536),
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

-- HNSW: high recall with no list-count tuning and no post-insert training step.
-- ivfflat with many lists over a tiny KB (~20-40 chunks) leaves most lists empty and a
-- single-probe query finds little; HNSW is the right tool at this scale and stays fast as it grows.
-- (At this size exact <=> search is already sub-millisecond even without an index.)
create index if not exists kb_documents_embedding_idx
  on kb_documents using hnsw (embedding vector_cosine_ops);

create or replace function match_documents(
  query_embedding vector(1536),
  match_count int default 5,
  similarity_threshold float default 0.0
)
returns table (id uuid, content text, metadata jsonb, similarity float)
language sql stable as $$
  select id, content, metadata,
         1 - (embedding <=> query_embedding) as similarity
  from kb_documents
  where 1 - (embedding <=> query_embedding) >= similarity_threshold
  order by embedding <=> query_embedding
  limit match_count;
$$;

create table if not exists chat_sessions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz default now(),
  metadata jsonb default '{}'::jsonb
);

create table if not exists chat_messages (
  id uuid primary key default gen_random_uuid(),
  session_id uuid references chat_sessions(id),
  role text not null,
  content text not null,
  created_at timestamptz default now()
);
create index if not exists chat_messages_session_idx on chat_messages(session_id, created_at);

create table if not exists leads (
  id uuid primary key default gen_random_uuid(),
  session_id uuid,
  intent text not null,
  name text, email text, phone text, organization text,
  extra jsonb default '{}'::jsonb,
  message text,
  created_at timestamptz default now(),
  emailed boolean default false,
  pushed_to_pipedrive boolean default false
);

-- ── FAQ-match mode (BOT_MODE=faq) ─────────────────────────────────────────
-- In FAQ mode chunks are stored with no embedding, so the column must be nullable.
alter table kb_documents alter column embedding drop not null;

-- Generated tsvector + GIN index: the zero-AI matching path.
alter table kb_documents add column if not exists content_tsv tsvector
  generated always as (to_tsvector('english', content)) stored;
create index if not exists kb_documents_content_tsv_idx
  on kb_documents using gin (content_tsv);

-- Mirrors match_documents' return shape (score column named `similarity`) so the
-- Python retrieval helpers and the frozen retrieval_scores contract stay identical.
create or replace function match_documents_fts(
  query_text text,
  match_count int default 5
)
returns table (id uuid, content text, metadata jsonb, similarity float)
language sql stable as $$
  select id, content, metadata,
         ts_rank(content_tsv, websearch_to_tsquery('english', query_text))::float
           as similarity
  from kb_documents
  where content_tsv @@ websearch_to_tsquery('english', query_text)
  order by ts_rank(content_tsv, websearch_to_tsquery('english', query_text)) desc
  limit match_count;
$$;

-- Per-session position in the deterministic FAQ state machine (null = idle).
alter table chat_sessions add column if not exists flow_state jsonb;

-- The FAQ backlog: one row per feedback event. No PII — question text and rank only.
create table if not exists faq_misses (
  id uuid primary key default gen_random_uuid(),
  question text not null,
  top_rank float,
  answered boolean default false,
  created_at timestamptz default now()
);
create index if not exists faq_misses_created_idx on faq_misses(created_at desc);

-- ── Portal-authored knowledge (agent portal) ──────────────────────────────
-- Who owns a row: 'file' = authored in backend/knowledge_base/*.md and owned by
-- ingest; 'portal' = written by the team in the agent portal and owned by the
-- database. Ingest prunes ONLY file-managed rows — without this, the first
-- re-ingest after publishing deletes every portal-written answer.
alter table kb_documents add column if not exists managed_by text not null default 'file';
create index if not exists kb_documents_managed_by_idx on kb_documents(managed_by);

-- A gap leaves the portal's list once someone publishes an answer for it.
alter table faq_misses add column if not exists resolved boolean not null default false;
create index if not exists faq_misses_unresolved_idx on faq_misses(resolved, created_at desc);

-- ── Live agent chat ───────────────────────────────────────────────────────
-- Exactly one row. Availability is available=true AND a heartbeat inside
-- AGENT_HEARTBEAT_TTL_SECONDS, so it expires on its own when the tab closes.
create table if not exists agent_presence (
  id           text primary key default 'greg',
  available    boolean not null default false,
  last_seen_at timestamptz not null default now()
);

create table if not exists live_chats (
  id                   uuid primary key default gen_random_uuid(),
  session_id           uuid not null,
  lead_id              uuid,
  status               text not null default 'waiting',  -- waiting|active|ended
  question             text,
  started_at           timestamptz not null default now(),
  accepted_at          timestamptz,
  -- Updated on every visitor poll; a stale value is how visitor_left is detected
  -- without a background worker.
  visitor_last_seen_at timestamptz not null default now(),
  ended_at             timestamptz,
  ended_reason         text  -- agent_ended|agent_dropped|visitor_left|not_accepted
);
create index if not exists live_chats_status_idx on live_chats(status, started_at);
create index if not exists live_chats_session_idx on live_chats(session_id, started_at desc);
