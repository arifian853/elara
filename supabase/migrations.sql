-- =============================================================
-- Elara v1 (Public) — Supabase Schema Migration
-- =============================================================
-- Jalankan SQL ini di Supabase SQL Editor (Dashboard).
-- Catatan: Role custom 'elara' dibuat agar backend tidak
-- bergantung pada SUPABASE_SERVICE_KEY. RLS TIDAK diaktifkan
-- (Auto-RLS off) karena akses hanya melalui backend.
-- =============================================================

-- 1. Setup Role 'elara' & Hak Akses Database
-- (Jika role sudah ada, abaikan error ini)
do $$
begin
  if not exists (select from pg_roles where rolname = 'elara') then
    create role elara with login password 'YOUR_STRONG_PASSWORD_HERE';
  end if;
end
$$;

grant connect on database postgres to elara;
grant usage on schema public to elara;

alter default privileges in schema public
  grant select, insert, update, delete on tables to elara;
alter default privileges in schema public
  grant usage, select, update on sequences to elara;

-- 2. Extensions
create extension if not exists vector;

-- 3. Tabel Documents (file sumber asli di R2 atau GitHub repo)
create table if not exists documents (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  source_type text,                      -- pdf | docx | md | csv | github | manual
  r2_key      text,                      -- object key di R2 (nullable jika github/manual)
  metadata    jsonb default '{}',        -- utk github: {github_url, repo, description, tech_stack, stars, project_order, updated_at}
  created_at  timestamptz default now()
);

-- 4. Tabel Chunks + Vector Indexing (768 dimensions utk Gemini Embedding 2)
create table if not exists chunks (
  id          uuid primary key default gen_random_uuid(),
  document_id uuid references documents(id) on delete cascade,
  content     text not null,
  section     text,
  token_count int,
  embedding   vector(768),
  created_at  timestamptz default now()
);

create index if not exists idx_chunks_hnsw
  on chunks using hnsw (embedding vector_cosine_ops);

create index if not exists idx_chunks_fts
  on chunks using gin (to_tsvector('simple', content));

-- 5. Tabel Leads (hasil Intake Project Request)
create table if not exists leads (
  id          uuid primary key default gen_random_uuid(),
  service     text,
  description text,
  budget      text,
  deadline    text,
  contact     text,
  status      text default 'new',        -- new | contacted | won | lost
  raw         jsonb default '{}',
  created_at  timestamptz default now()
);

-- 6. Tabel Intake Sessions (Persist state machine, anti-kehilangan jika restart)
create table if not exists intake_sessions (
  chat_id    text primary key,
  step       int default 1,
  data       jsonb default '{}',
  updated_at timestamptz default now()
);

-- 8. Tabel Confessions (Pesan Anonim & Balasan)
create table if not exists confessions (
  id               uuid primary key default gen_random_uuid(),
  message          text not null,
  ip_address       text,
  reply            text,
  reply_created_at timestamptz,
  is_replied       boolean default false,
  created_at       timestamptz default now()
);

-- 9. Tabel System Prompts (Dynamic System Prompt Manager)
create table if not exists system_prompts (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  prompt      text not null,
  description text,
  is_active   boolean default false,
  created_at  timestamptz default now()
);

-- 10. Grant akses pada semua tabel ke role 'elara'
grant select, insert, update, delete on all tables in schema public to elara;
grant usage, select, update on all sequences in schema public to elara;
