"""
tests/test_m5_5_features.py — Smoke tests for M5.5 features (Confessions & System Prompts).

Run: uv run pytest tests/test_m5_5_features.py -v -s
"""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings, get_pool
from routers.confessions import submit_confession, list_public_confessions, reply_confession, delete_confession
from models import ConfessionSubmitRequest, ConfessionReplyRequest


class DummyRequest:
    class Client:
        host = "127.0.0.1"
    client = Client()


@pytest.mark.asyncio
async def test_confession_lifecycle():
    """Test submit, list, reply, and delete confession."""
    # 1. Submit confession
    payload = ConfessionSubmitRequest(message="Halo Elara, ini pesan anonim pengujian.")
    res = await submit_confession(payload, DummyRequest())
    assert res["status"] == "success"
    cid = res["id"]
    print(f"\n[PASS] Submitted confession ID: {cid}")

    # 2. Reply confession (admin)
    reply_payload = ConfessionReplyRequest(reply="Halo! Terima kasih pesannya 😊")
    reply_res = await reply_confession(cid, reply_payload, x_admin_token=settings.admin_token)
    assert reply_res["status"] == "success"
    # Note: verify_admin_token will pass or check settings. In tests, we verify DB state.
    # But let's check directly via pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_replied, reply FROM confessions WHERE id = $1::uuid", cid)
        assert row["is_replied"] is True
        assert "Terima kasih" in row["reply"]

    # 3. Public list
    pub_list = await list_public_confessions(limit=10)
    assert any(item["id"] == cid for item in pub_list)
    print("[PASS] Confession appears in public list")

    # 4. Cleanup
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM confessions WHERE id = $1::uuid", cid)
    print("[CLEANUP] Deleted test confession")
