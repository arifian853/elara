"""
scripts/seed_admin.py — Seed initial admin user into Supabase.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from config import settings
from services.auth import hash_password


async def seed_admin():
    if not settings.admin_token:
        print("[ERROR] ADMIN_TOKEN environment variable is not configured.")
        return

    print("Connecting to Supabase...")
    conn = await asyncpg.connect(settings.supabase_db_url)
    pwd_hash = hash_password(settings.admin_token)

    await conn.execute(
        """
        INSERT INTO admin_users (username, password_hash)
        VALUES ('admin', $1)
        ON CONFLICT (username)
        DO UPDATE SET password_hash = $1
        """,
        pwd_hash,
    )
    await conn.close()
    print(f"[SUCCESS] Admin user seeded into DB! Username: admin (Password set from ADMIN_TOKEN)")


if __name__ == "__main__":
    asyncio.run(seed_admin())
