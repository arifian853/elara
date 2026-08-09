"""
tests/test_state_machine_intake.py — Smoke test for intake state machine.

Tests the full 6-step flow:
  Start → Service → Description → Budget → Deadline → Contact → Confirm

Run: uv run pytest tests/test_state_machine_intake.py -v -s
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_pool, close_pool
from services.intake import (
    start_intake_session,
    get_intake_session,
    process_intake_step,
)


TEST_CHAT_ID = "__test_intake_session__"


@pytest.fixture(autouse=True)
async def cleanup():
    """Clean up test session before and after each test."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM intake_sessions WHERE chat_id = $1", TEST_CHAT_ID
        )
        await conn.execute(
            "DELETE FROM leads WHERE contact = $1", "__test_contact__"
        )
    yield
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM intake_sessions WHERE chat_id = $1", TEST_CHAT_ID
        )
        await conn.execute(
            "DELETE FROM leads WHERE contact = $1", "__test_contact__"
        )


async def test_full_intake_flow():
    """Walk through all 6 steps and verify lead is saved."""
    # Step 1: Start session
    result = await start_intake_session(TEST_CHAT_ID)
    assert result["step"] == 1
    assert result["chips"] is not None
    assert "jasa" in result["response"].lower() or "layanan" in result["response"].lower()
    print(f"\n[Step 1] {result['response'][:60]}...")

    # Get session to verify persistence
    session = await get_intake_session(TEST_CHAT_ID)
    assert session is not None
    assert session["step"] == 1

    # Step 2: Answer service
    session = await get_intake_session(TEST_CHAT_ID)
    result = await process_intake_step(TEST_CHAT_ID, "Web App", session)
    assert result["step"] == 2
    print(f"[Step 2] {result['response'][:60]}...")

    # Step 3: Answer description
    session = await get_intake_session(TEST_CHAT_ID)
    result = await process_intake_step(
        TEST_CHAT_ID, "Bikin web portfolio modern", session
    )
    assert result["step"] == 3
    assert result["chips"] is not None
    print(f"[Step 3] {result['response'][:60]}...")

    # Step 4: Answer budget
    session = await get_intake_session(TEST_CHAT_ID)
    result = await process_intake_step(TEST_CHAT_ID, "1-3jt", session)
    assert result["step"] == 4
    print(f"[Step 4] {result['response'][:60]}...")

    # Step 5: Answer deadline
    session = await get_intake_session(TEST_CHAT_ID)
    result = await process_intake_step(TEST_CHAT_ID, "1 Bulan", session)
    assert result["step"] == 5
    print(f"[Step 5] {result['response'][:60]}...")

    # Step 6: Answer contact → should show confirmation
    session = await get_intake_session(TEST_CHAT_ID)
    result = await process_intake_step(TEST_CHAT_ID, "__test_contact__", session)
    assert result["step"] == 6
    assert "rangkuman" in result["response"].lower() or "benar" in result["response"].lower()
    assert "Ya, Kirim" in (result["chips"] or [])
    print(f"[Step 6] Confirmation shown")

    # Step 6 confirm: Send
    session = await get_intake_session(TEST_CHAT_ID)
    result = await process_intake_step(TEST_CHAT_ID, "Ya, Kirim", session)
    assert result["step"] is None  # Session ended
    assert "terima kasih" in result["response"].lower()
    print(f"[Done] {result['response'][:60]}...")

    # Verify lead was saved
    pool = await get_pool()
    async with pool.acquire() as conn:
        lead = await conn.fetchrow(
            "SELECT * FROM leads WHERE contact = $1", "__test_contact__"
        )
    assert lead is not None
    assert lead["service"] == "Web App"
    assert lead["budget"] == "1-3jt"
    print(f"[Verify] Lead saved: id={lead['id']}, service={lead['service']}")

    # Verify session was cleaned up
    session = await get_intake_session(TEST_CHAT_ID)
    assert session is None
    print("[Verify] Session cleaned up")


async def test_intake_cancel():
    """Test cancelling an intake session."""
    await start_intake_session(TEST_CHAT_ID)
    session = await get_intake_session(TEST_CHAT_ID)
    assert session is not None

    result = await process_intake_step(TEST_CHAT_ID, "batal", session)
    assert "batal" in result["response"].lower() or "cancel" in result["response"].lower()

    session = await get_intake_session(TEST_CHAT_ID)
    assert session is None
    print("\n[PASS] Cancel works correctly")
