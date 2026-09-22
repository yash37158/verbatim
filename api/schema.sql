-- Verbatim schema. Apply with:  psql -d verbatim -f schema.sql
-- Idempotent: safe to re-run.

create extension if not exists vector;
create extension if not exists pgcrypto;   -- gen_random_uuid

create table if not exists users (
  id          uuid primary key default gen_random_uuid(),
  email       text unique not null,
  -- Nullable: accounts created by Google sign-in have no API key. The key remains for
  -- scripts and the eval harness, which cannot carry a browser cookie.
  api_key     text unique,
  google_sub  text unique,
  name        text,
  avatar_url  text,
  created_at  timestamptz not null default now()
);

-- Sessions are rows, not signed tokens, so that signing out actually invalidates one.
-- A stateless JWT stays valid until it expires no matter what the server thinks.
create table if not exists sessions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references users on delete cascade,
  -- The token is stored hashed, like a password: a database leak then yields hashes
  -- rather than working sessions.
  token_hash  char(64) unique not null,
  expires_at  timestamptz not null,
  user_agent  text,
  created_at  timestamptz not null default now()
);
create index if not exists sessions_user_idx on sessions (user_id);
create index if not exists sessions_expiry_idx on sessions (expires_at);

create table if not exists spaces (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references users on delete cascade,
  name        text not null,
  description text,
  created_at  timestamptz not null default now(),
  deleted_at  timestamptz
);
create index if not exists spaces_user_idx on spaces (user_id) where deleted_at is null;

create table if not exists documents (
  id          uuid primary key default gen_random_uuid(),
  space_id    uuid not null references spaces on delete cascade,
  filename    text not null,
  mime_type   text not null,
  size_bytes  bigint not null,
  page_count  int,
  sha256      char(64) not null,
  storage_key text not null,
  -- the documents table doubles as the ingestion queue; see app/ingest.py
  status      text not null default 'queued'
              check (status in ('queued','parsing','embedding','ready','failed')),
  error       text,
  attempts    int not null default 0,
  locked_at   timestamptz,
  -- A daily quota needs hours, not the seconds our in-process backoff covers. Rate-limited
  -- work goes back on the queue with a wait instead of being written off as a bad file.
  retry_after timestamptz,
  -- [[char_offset, page_number], ...] — lets a citation name the page the *quote* is on,
  -- not merely the page its chunk starts on.
  page_offsets jsonb not null default '[]',
  created_at  timestamptz not null default now(),
  unique (space_id, sha256)
);
-- Partial index: the worker's claim query only ever scans queued rows.
create index if not exists documents_queue_idx on documents (retry_after nulls first, created_at)
  where status = 'queued';
create index if not exists documents_space_idx on documents (space_id);

create table if not exists chunks (
  id          uuid primary key default gen_random_uuid(),
  document_id uuid not null references documents on delete cascade,
  space_id    uuid not null references spaces on delete cascade,  -- denormalised: scopes every search
  ordinal     int  not null,
  page_start  int,
  page_end    int,
  char_start  int  not null,
  char_end    int  not null,
  text        text not null,
  embedding   vector(1536) not null,
  tsv         tsvector generated always as (to_tsvector('english', text)) stored
);
create index if not exists chunks_embedding_idx on chunks
  using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);
create index if not exists chunks_tsv_idx on chunks using gin (tsv);
create index if not exists chunks_space_idx on chunks (space_id);
create index if not exists chunks_document_idx on chunks (document_id);

create table if not exists conversations (
  id         uuid primary key default gen_random_uuid(),
  space_id   uuid not null references spaces on delete cascade,
  title      text,
  created_at timestamptz not null default now()
);
create index if not exists conversations_space_idx on conversations (space_id, created_at desc);

create table if not exists messages (
  id              uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations on delete cascade,
  role            text not null check (role in ('user','assistant')),
  content         text not null,
  citations       jsonb not null default '[]',
  searches        jsonb not null default '[]',   -- the queries the agent actually ran
  -- Grounding health, per PRD §9. Computed on every answer; worthless unless kept.
  ungrounded      boolean not null default false,
  dropped_citations int not null default 0,
  model           text,                          -- numbers above are meaningless without it
  latency_ms      int,
  created_at      timestamptz not null default now()
);
create index if not exists messages_conversation_idx on messages (conversation_id, created_at);
-- Supports the grounding-rate query in api/README.md without scanning every message.
create index if not exists messages_grounding_idx on messages (created_at)
  where role = 'assistant';
