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
