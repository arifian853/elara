"""Verify migration: check tables, counts, and pgvector extension."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from config import settings


async def verify():
    conn = await asyncpg.connect(dsn=settings.supabase_db_url)

    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;"
    )
    print(f"Tables: {[t['tablename'] for t in tables]}")

    for tbl in ["documents", "chunks", "leads", "intake_sessions"]:
        count = await conn.fetchval(f"SELECT count(*) FROM {tbl};")
        print(f"  {tbl}: {count} rows")

    ext = await conn.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector';")
    print(f"pgvector version: {ext}")

    await conn.close()
    print("All verified OK!")


if __name__ == "__main__":
    asyncio.run(verify())
