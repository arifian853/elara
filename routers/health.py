"""
routers/health.py — Liveness + database connectivity check.

GET /health → 200 if DB reachable, 503 otherwise.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from config import get_pool
from models import HealthResponse

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": HealthResponse}},
)
async def health_check():
    """Ping Supabase with `SELECT 1` to verify connectivity."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return HealthResponse(status="healthy", database="connected")
    except Exception:
        return JSONResponse(
            status_code=503,
            content=HealthResponse(
                status="unhealthy",
                database="disconnected",
            ).model_dump(),
        )
