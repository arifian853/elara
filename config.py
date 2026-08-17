"""
config.py — Environment settings & asyncpg connection pool.

Loads .env via pydantic-settings. Exposes get_pool() / close_pool()
for FastAPI lifespan management.
"""

from __future__ import annotations

import asyncpg
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All env vars consumed by Elara Public backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database (asyncpg via Supabase Pooler) ──────────────────────
    supabase_db_url: str

    # ── Groq (Generation + Query Rewrite) ───────────────────────────
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_temperature: float = 0.3
    groq_max_tokens: int = 1000

    # ── Google AI Studio (Embedding + Reranker) ─────────────────────
    gemini_api_key: str = ""
    embedding_model: str = "gemini-embedding-2"
    embedding_dimensions: int = 768
    reranker_model: str = "gemma-4-26b-a4b-it"

    # ── Cloudflare R2 ───────────────────────────────────────────────
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_endpoint: str = ""
    r2_bucket: str = "elara-chatbot"
    r2_region: str = "auto"

    # ── GitHub API (optional — raises rate limit to 5000/hr) ────────
    github_token: str = ""

    # ── Telegram Bridge (outbound only) ─────────────────────────────
    telegram_bot_token: str = ""
    owner_chat_id: str = ""

    # ── Security & CORS ─────────────────────────────────────────────
    admin_token: str = ""
    cors_origins: str = "https://arifian.dev,http://localhost:3000"
    environment: str = "development"  # 'development' | 'production'
    enable_docs: bool = True

    # ── RAG tuning ──────────────────────────────────────────────────
    retrieval_top_k: int = 10
    rerank_top_k: int = 3
    similarity_threshold: float = 0.35
    chunk_size: int = 800
    chunk_overlap: float = 0.1


settings = Settings()

# ── asyncpg pool singleton ──────────────────────────────────────────
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the existing pool or create one (idempotent)."""
    global _pool
    if _pool is not None:
        # If pool or underlying loop is closed (e.g. between pytest modules), reset
        if getattr(_pool, "_closed", False) or getattr(getattr(_pool, "_loop", None), "is_closed", lambda: False)():
            _pool = None

    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=settings.supabase_db_url,
            min_size=2,
            max_size=10,
            command_timeout=15,
        )
    return _pool


async def close_pool() -> None:
    """Gracefully close the pool (called on shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
