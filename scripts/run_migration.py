"""
run_migration.py — Execute migrations.sql via asyncpg (for when MCP/SQL Editor is down).
Run with: uv run python run_migration.py
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from config import settings


MIGRATION_STATEMENTS = [
    # 1. Role setup (wrapped in DO block for idempotency)
    """
    do $$
    begin
      if not exists (select from pg_roles where rolname = 'elara') then
        create role elara with login password 'YOUR_STRONG_PASSWORD_HERE';
      end if;
    end
    $$;
    """,
    "grant connect on database postgres to elara;",
    "grant usage on schema public to elara;",
    """
    alter default privileges in schema public
      grant select, insert, update, delete on tables to elara;
    """,
    """
    alter default privileges in schema public
      grant usage, select, update on sequences to elara;
    """,

    # 2. Extensions
    "create extension if not exists vector;",

    # 3. Tabel Documents
    """
    create table if not exists documents (
      id          uuid primary key default gen_random_uuid(),
      title       text not null,
      source_type text,
      r2_key      text,
      metadata    jsonb default '{}',
      created_at  timestamptz default now()
    );
    """,

    # 4. Tabel Chunks
    """
    create table if not exists chunks (
      id          uuid primary key default gen_random_uuid(),
      document_id uuid references documents(id) on delete cascade,
      content     text not null,
      section     text,
      token_count int,
      embedding   vector(768),
      created_at  timestamptz default now()
    );
    """,
    """
    create index if not exists idx_chunks_hnsw
      on chunks using hnsw (embedding vector_cosine_ops);
    """,
    """
    create index if not exists idx_chunks_fts
      on chunks using gin (to_tsvector('simple', content));
    """,

    # 5. Tabel Leads
    """
    create table if not exists leads (
      id          uuid primary key default gen_random_uuid(),
      service     text,
      description text,
      budget      text,
      deadline    text,
      contact     text,
      status      text default 'new',
      raw         jsonb default '{}',
      created_at  timestamptz default now()
    );
    """,

    # 6. Tabel Intake Sessions
    """
    create table if not exists intake_sessions (
      chat_id    text primary key,
      step       int default 1,
      data       jsonb default '{}',
      updated_at timestamptz default now()
    );
    """,

    # 7. Tabel Confessions
    """
    create table if not exists confessions (
      id               uuid primary key default gen_random_uuid(),
      message          text not null,
      ip_address       text,
      reply            text,
      reply_created_at timestamptz,
      is_replied       boolean default false,
      created_at       timestamptz default now()
    );
    """,

    # 8. Tabel System Prompts
    """
    create table if not exists system_prompts (
      id          uuid primary key default gen_random_uuid(),
      name        text not null,
      prompt      text not null,
      description text,
      is_active   boolean default false,
      created_at  timestamptz default now()
    );
    """,

    # 9. Tabel Admin Users
    """
    create table if not exists admin_users (
      id            uuid primary key default gen_random_uuid(),
      username      text unique not null,
      password_hash text not null,
      created_at    timestamptz default now()
    );
    """,

    # 10. Grant tables to role elara
    "grant select, insert, update, delete on all tables in schema public to elara;",
    "grant usage, select, update on all sequences in schema public to elara;",
]


async def run_migration():
    print("Connecting to Supabase...")
    conn = await asyncpg.connect(dsn=settings.supabase_db_url)

    for i, stmt in enumerate(MIGRATION_STATEMENTS, 1):
        try:
            await conn.execute(stmt.strip())
            print(f"  [{i}/{len(MIGRATION_STATEMENTS)}] OK")
        except Exception as e:
            print(f"  [{i}/{len(MIGRATION_STATEMENTS)}] FAIL: {e}")

    # Verify
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;"
    )
    print(f"\nPublic tables: {[t['tablename'] for t in tables]}")

    await conn.close()
    print("\nMigration complete!")


if __name__ == "__main__":
    asyncio.run(run_migration())
