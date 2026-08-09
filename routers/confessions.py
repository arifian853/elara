"""
routers/confessions.py — Public and Admin endpoints for Anonymous Messages / Confessions (M5.5).

Public:
  POST /confessions/public/submit — Kirim pesan anonim (+ notifikasi Telegram)
  GET /confessions/public/list   — List pesan anonim publik & balasan

Admin (X-Admin-Token):
  GET /admin/confessions               — List semua pesan
  POST /admin/confessions/{id}/reply   — Balas pesan anonim
  DELETE /admin/confessions/{id}       — Hapus pesan
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Header, HTTPException, Request

from config import settings, get_pool
from models import ConfessionSubmitRequest, ConfessionReplyRequest
from services.bridge import send_telegram_message
from services.auth import verify_admin_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["confessions"])


# ── Public Endpoints ────────────────────────────────────────────────

@router.post("/confessions/public/submit")
async def submit_confession(payload: ConfessionSubmitRequest, request: Request):
    """Submit an anonymous message / confession from portfolio website."""
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    client_ip = request.client.host if request.client else "unknown"

    pool = await get_pool()
    async with pool.acquire() as conn:
        confession_id = await conn.fetchval(
            """
            INSERT INTO confessions (message, ip_address)
            VALUES ($1, $2)
            RETURNING id
            """,
            msg,
            client_ip,
        )

    # Send outbound Telegram notification to Arifian
    try:
        tele_text = (
            f"<b>Pesan Anonim / Confession Baru!</b>\n\n"
            f"<i>\"{msg}\"</i>\n\n"
            f"IP: <code>{client_ip}</code>\n"
            f"ID: <code>{confession_id}</code>"
        )
        await send_telegram_message(tele_text)
    except Exception as e:
        logger.error(f"Failed to send Telegram notification for confession: {e}")

    return {
        "status": "success",
        "message": "Confession submitted successfully",
        "id": str(confession_id),
    }


@router.get("/confessions/public/list")
async def list_public_confessions(limit: int = 50):
    """List public confessions that have been replied to by Arifian (for /message page)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, message, reply, reply_created_at, created_at, is_replied
            FROM confessions
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )

    return [
        {
            "id": str(r["id"]),
            "message": r["message"],
            "reply": r["reply"],
            "replyCreatedAt": r["reply_created_at"].isoformat() if r["reply_created_at"] else None,
            "isReplied": r["is_replied"],
            "createdAt": r["created_at"].isoformat() if r["created_at"] else "",
        }
        for r in rows
    ]


# ── Admin Endpoints ─────────────────────────────────────────────────

@router.get("/admin/confessions")
async def get_admin_confessions(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
    limit: int = 100,
):
    """List all confessions (replied and unreplied) for admin management."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, message, ip_address, reply, reply_created_at, is_replied, created_at
            FROM confessions
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )

    return [
        {
            "id": str(r["id"]),
            "message": r["message"],
            "ipAddress": r["ip_address"] or "unknown",
            "reply": r["reply"],
            "replyCreatedAt": r["reply_created_at"].isoformat() if r["reply_created_at"] else None,
            "isReplied": r["is_replied"],
            "createdAt": r["created_at"].isoformat() if r["created_at"] else "",
        }
        for r in rows
    ]


@router.post("/admin/confessions/{confession_id}/reply")
async def reply_to_confession(
    confession_id: str,
    payload: ConfessionReplyRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Submit a public reply for a confession."""
    verify_admin_token(x_admin_token, authorization)

    reply_text = payload.reply.strip()
    if not reply_text:
        raise HTTPException(status_code=400, detail="Reply cannot be empty")

    pool = await get_pool()
    async with pool.acquire() as conn:
        updated_id = await conn.fetchval(
            """
            UPDATE confessions
            SET reply = $1, reply_created_at = now(), is_replied = true
            WHERE id = $2::uuid
            RETURNING id
            """,
            reply_text,
            confession_id,
        )

    if not updated_id:
        raise HTTPException(status_code=404, detail="Confession not found")

    return {
        "status": "success",
        "id": str(updated_id),
        "reply": reply_text,
    }


reply_confession = reply_to_confession


@router.delete("/admin/confessions/{confession_id}")
async def delete_confession(
    confession_id: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Delete a confession."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted_id = await conn.fetchval(
            "DELETE FROM confessions WHERE id = $1::uuid RETURNING id",
            confession_id,
        )

    if not deleted_id:
        raise HTTPException(status_code=404, detail="Confession not found")

    return {"status": "success", "deleted_id": str(deleted_id)}
