"""
services/intake.py — Project request intake state machine (6 steps).

Flow:
  Step 1: Jenis layanan (chips)
  Step 2: Deskripsi proyek (free text)
  Step 3: Budget (chips)
  Step 4: Deadline (chips)
  Step 5: Kontak WA/Email (free text)
  Step 6: Konfirmasi → simpan ke leads → kirim Telegram

Sessions persisted to intake_sessions table (anti-kehilangan jika restart).
Timeout: 15 menit tanpa aktivitas → session expired.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from config import get_pool

logger = logging.getLogger(__name__)


# ── Step definitions ────────────────────────────────────────────────

STEPS = {
    1: {
        "question": "Mau jasa apa dari Arifian? Pilih salah satu ya:",
        "chips": ["Web App", "Skripsi/TA", "Coaching", "Desain", "Lainnya"],
        "field": "service",
    },
    2: {
        "question": "Ceritain proyeknya dong, tujuan dan fitur utamanya apa?",
        "chips": None,
        "field": "description",
    },
    3: {
        "question": "Estimasi budget kamu sekitar berapa?",
        "chips": ["<1jt", "1-3jt", "3-5jt", "5jt+", "Belum Tahu"],
        "field": "budget",
    },
    4: {
        "question": "Targetnya kapan kelar proyek ini?",
        "chips": ["Buru-buru (<2 mgg)", "1 Bulan", "2-3 Bulan", "Santai"],
        "field": "deadline",
    },
    5: {
        "question": "Kontak yang bisa dihubungi (nomor WA atau email)?",
        "chips": None,
        "field": "contact",
    },
}

TIMEOUT_MINUTES = 15


# ── Session management ──────────────────────────────────────────────

async def get_intake_session(chat_id: str) -> dict | None:
    """
    Get active intake session from DB.

    Returns None if no session or session expired (>15 min idle).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT step, data, updated_at FROM intake_sessions WHERE chat_id = $1",
            chat_id,
        )

    if row is None:
        return None

    # Check timeout
    updated_at = row["updated_at"]
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    elapsed = (now - updated_at).total_seconds()

    if elapsed > TIMEOUT_MINUTES * 60:
        # Session expired — clean up
        await _delete_session(chat_id)
        return None

    data = row["data"] if isinstance(row["data"], dict) else json.loads(row["data"])
    return {"step": row["step"], "data": data}


async def start_intake_session(chat_id: str) -> dict:
    """Start a new intake session at step 1."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Upsert: reset if exists
        await conn.execute(
            """
            INSERT INTO intake_sessions (chat_id, step, data, updated_at)
            VALUES ($1, 1, '{}', now())
            ON CONFLICT (chat_id) DO UPDATE
            SET step = 1, data = '{}', updated_at = now()
            """,
            chat_id,
        )

    step_info = STEPS[1]
    return {
        "response": f"Boleh banget! Aku bantu catat kebutuhanmu ya.\n\n{step_info['question']}",
        "step": 1,
        "chips": step_info["chips"],
    }


async def process_intake_step(
    chat_id: str,
    message: str,
    session: dict,
) -> dict:
    """
    Process the user's answer for the current intake step.

    Returns response dict with 'response', 'step', and optional 'chips'.
    """
    current_step = session["step"]
    data = session["data"]

    # ── Handle cancel ───────────────────────────────────────────────
    if message.lower() in ("/cancel", "batal", "cancel"):
        await _delete_session(chat_id)
        return {
            "response": "Oke, request dibatalin ya. Kalau berubah pikiran, tinggal bilang aja!",
            "step": None,
            "chips": None,
        }

    # ── Step 6: Confirmation ────────────────────────────────────────
    if current_step == 6:
        if message.lower() in ("ya", "ya, kirim", "yes", "ok", "oke", "kirim"):
            # Save to leads table
            lead_id = await _save_lead(data)
            # Send Telegram notification
            await _notify_telegram(data, lead_id)
            # Clean up session
            await _delete_session(chat_id)
            return {
                "response": (
                    "Terima kasih! Request kamu sudah aku kirim ke Arifian.\n"
                    "Dia akan segera menghubungi kamu lewat kontak yang kamu kasih.\n"
                    "Ada yang lain yang bisa aku bantu?"
                ),
                "step": None,
                "chips": None,
            }
        else:
            # Not confirmed — ask again or cancel
            return {
                "response": "Klik 'Ya, Kirim' untuk konfirmasi, atau ketik 'batal' untuk membatalkan.",
                "step": 6,
                "chips": ["Ya, Kirim"],
            }

    # ── Steps 1-5: Collect answer ───────────────────────────────────
    if current_step not in STEPS:
        await _delete_session(chat_id)
        return {
            "response": "Session error, silakan mulai lagi dengan mengetik request kamu.",
            "step": None,
            "chips": None,
        }

    step_info = STEPS[current_step]
    data[step_info["field"]] = message.strip()

    next_step = current_step + 1

    # ── If all 5 questions answered → show confirmation (step 6) ────
    if next_step > 5:
        # Save progress and show summary
        await _update_session(chat_id, 6, data)

        summary = (
            f"Oke, ini rangkuman request kamu:\n\n"
            f"  Layanan: {data.get('service', '-')}\n"
            f"  Deskripsi: {data.get('description', '-')}\n"
            f"  Budget: {data.get('budget', '-')}\n"
            f"  Deadline: {data.get('deadline', '-')}\n"
            f"  Kontak: {data.get('contact', '-')}\n\n"
            f"Sudah benar? Kalau iya, aku kirim ke Arifian ya!"
        )
        return {
            "response": summary,
            "step": 6,
            "chips": ["Ya, Kirim"],
        }

    # ── Move to next question ───────────────────────────────────────
    await _update_session(chat_id, next_step, data)
    next_info = STEPS[next_step]
    return {
        "response": next_info["question"],
        "step": next_step,
        "chips": next_info["chips"],
    }


# ── Internal helpers ────────────────────────────────────────────────

async def _update_session(chat_id: str, step: int, data: dict):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE intake_sessions
            SET step = $1, data = $2::jsonb, updated_at = now()
            WHERE chat_id = $3
            """,
            step,
            json.dumps(data),
            chat_id,
        )


async def _delete_session(chat_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM intake_sessions WHERE chat_id = $1",
            chat_id,
        )


async def _save_lead(data: dict) -> str:
    """Insert lead into leads table. Returns lead ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        lead_id = await conn.fetchval(
            """
            INSERT INTO leads (service, description, budget, deadline, contact, raw)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id
            """,
            data.get("service", ""),
            data.get("description", ""),
            data.get("budget", ""),
            data.get("deadline", ""),
            data.get("contact", ""),
            json.dumps(data),
        )
    logger.info(f"Lead saved: {lead_id}")
    return str(lead_id)


async def _notify_telegram(data: dict, lead_id: str):
    """Send lead notification to Arifian via Telegram bridge."""
    try:
        from services.bridge import send_telegram_message

        text = (
            f"New Project Request!\n\n"
            f"Lead ID: {lead_id}\n"
            f"Layanan: {data.get('service', '-')}\n"
            f"Deskripsi: {data.get('description', '-')}\n"
            f"Budget: {data.get('budget', '-')}\n"
            f"Deadline: {data.get('deadline', '-')}\n"
            f"Kontak: {data.get('contact', '-')}"
        )
        await send_telegram_message(text)
    except Exception as e:
        logger.error(f"Telegram notification failed: {e}")
        # Don't fail the intake — lead is already saved
