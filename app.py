"""
app.py — Elara Public FastAPI entry point.

Run with: uv run uvicorn app:app --reload
"""

from __future__ import annotations

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import settings, get_pool, close_pool
from routers import health, chat, admin, confessions, system_prompts


# ── Lifespan: asyncpg pool lifecycle ────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create asyncpg pool. Shutdown: close it."""
    await get_pool()
    yield
    await close_pool()


# ── FastAPI App ─────────────────────────────────────────────────────

show_docs = settings.enable_docs and settings.environment.lower() != "production"

app = FastAPI(
    title="Elara Public API",
    description="RAG chatbot + intake assistant for Arifian",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if show_docs else None,
    redoc_url="/redoc" if show_docs else None,
    openapi_url="/openapi.json" if show_docs else None,
)


# ── CORS ────────────────────────────────────────────────────────────

origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Static Files & Routers ──────────────────────────────────────────

static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

from fastapi.responses import FileResponse

app.include_router(health.router)
app.include_router(chat.router)
app.include_router(admin.router)
app.include_router(confessions.router)
app.include_router(system_prompts.router)


@app.get("/", response_class=FileResponse)
async def root_admin_dashboard():
    """Serve Admin Dashboard GUI directly at root GET /."""
    admin_html_path = Path(__file__).resolve().parent / "static" / "admin.html"
    return FileResponse(admin_html_path)




