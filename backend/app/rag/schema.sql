create extension if not exists vector;

create table if not exists kb_documents (
  id uuid primary key default gen_random_uuid(),
  content text not null,
  content_hash text unique,
  embedding vector(1536),
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now()
);

create index if not exists kb_documents_embedding_idx
  on kb_documents using ivfflat (embedding vector_cosine_ops) with (lists = 100);

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
