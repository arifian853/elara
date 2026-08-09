"""
routers/system_prompts.py — Admin endpoints for Dynamic System Prompt Manager (M5.5).

Endpoints (X-Admin-Token):
  GET    /admin/system-prompts         — List all system prompts
  GET    /admin/system-prompts/active  — Get currently active prompt
  POST   /admin/system-prompts         — Create new prompt
  PUT    /admin/system-prompts/{id}    — Update prompt / activate
  DELETE /admin/system-prompts/{id}    — Delete prompt
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, Header, HTTPException

from config import settings, get_pool
from models import SystemPromptRequest, SystemPromptUpdateRequest
from services.auth import verify_admin_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/system-prompts", tags=["system-prompts"])


@router.get("")
async def list_system_prompts(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """List all system prompts."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, prompt, description, is_active, created_at FROM system_prompts ORDER BY created_at DESC"
        )

    return [
        {
            "id": str(r["id"]),
            "name": r["name"],
            "prompt": r["prompt"],
            "description": r["description"] or "",
            "isActive": r["is_active"],
            "createdAt": r["created_at"].isoformat() if r["created_at"] else "",
        }
        for r in rows
    ]


@router.get("/active")
async def get_active_system_prompt(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get the currently active system prompt."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, name, prompt, description, is_active, created_at FROM system_prompts WHERE is_active = true LIMIT 1"
        )

    if not row:
        raise HTTPException(status_code=404, detail="No active system prompt found")

    return {
        "id": str(row["id"]),
        "name": row["name"],
        "prompt": row["prompt"],
        "description": row["description"] or "",
        "isActive": row["is_active"],
        "createdAt": row["created_at"].isoformat() if row["created_at"] else "",
    }


@router.post("")
async def create_system_prompt(
    payload: SystemPromptRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Create a new system prompt."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        if payload.is_active:
            # Deactivate all other prompts first
            await conn.execute("UPDATE system_prompts SET is_active = false WHERE is_active = true")

        prompt_id = await conn.fetchval(
            """
            INSERT INTO system_prompts (name, prompt, description, is_active)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            payload.name.strip(),
            payload.prompt.strip(),
            payload.description.strip(),
            payload.is_active,
        )

    return {
        "status": "success",
        "id": str(prompt_id),
        "name": payload.name,
        "isActive": payload.is_active,
    }


@router.put("/{prompt_id}")
async def update_system_prompt(
    prompt_id: str,
    payload: SystemPromptUpdateRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Update an existing system prompt."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        # Check prompt existence
        row = await conn.fetchrow("SELECT id, is_active FROM system_prompts WHERE id = $1::uuid", prompt_id)
        if not row:
            raise HTTPException(status_code=404, detail="System prompt not found")

        if payload.is_active is True:
            # Deactivate other prompts
            await conn.execute(
                "UPDATE system_prompts SET is_active = false WHERE id != $1::uuid AND is_active = true",
                prompt_id,
            )

        # Build dynamic update
        fields = []
        values = []
        idx = 1

        if payload.name is not None:
            fields.append(f"name = ${idx}")
            values.append(payload.name)
            idx += 1
        if payload.prompt is not None:
            fields.append(f"prompt = ${idx}")
            values.append(payload.prompt)
            idx += 1
        if payload.description is not None:
            fields.append(f"description = ${idx}")
            values.append(payload.description)
            idx += 1
        if payload.is_active is not None:
            fields.append(f"is_active = ${idx}")
            values.append(payload.is_active)
            idx += 1

        if not fields:
            raise HTTPException(status_code=400, detail="No fields provided for update")

        values.append(prompt_id)
        query = f"UPDATE system_prompts SET {', '.join(fields)} WHERE id = ${idx}::uuid RETURNING id"
        await conn.fetchval(query, *values)

    return {"status": "success", "id": prompt_id}


@router.post("/{prompt_id}/activate")
async def activate_system_prompt(
    prompt_id: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Set a system prompt as active (and deactivate all others)."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id FROM system_prompts WHERE id = $1::uuid", prompt_id)
        if not row:
            raise HTTPException(status_code=404, detail="System prompt not found")

        # Deactivate all others
        await conn.execute("UPDATE system_prompts SET is_active = false WHERE id != $1::uuid", prompt_id)
        # Activate this one
        await conn.execute("UPDATE system_prompts SET is_active = true WHERE id = $1::uuid", prompt_id)

    return {"status": "success", "active_id": prompt_id}


@router.post("/deactivate-all")
async def deactivate_all_system_prompts(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Deactivate all system prompts (falling back to default Elara prompt)."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE system_prompts SET is_active = false")

    return {"status": "success", "message": "All custom system prompts deactivated. Default Elara prompt is active."}


@router.delete("/{prompt_id}")
async def delete_system_prompt(
    prompt_id: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Delete a system prompt (if not active)."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT is_active FROM system_prompts WHERE id = $1::uuid", prompt_id)
        if not row:
            raise HTTPException(status_code=404, detail="System prompt not found")
        if row["is_active"]:
            raise HTTPException(status_code=400, detail="Cannot delete active system prompt. Deactivate or set another active first.")

        deleted_id = await conn.fetchval(
            "DELETE FROM system_prompts WHERE id = $1::uuid RETURNING id",
            prompt_id,
        )

    return {"status": "success", "deleted_id": str(deleted_id)}
