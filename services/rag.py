"""
services/rag.py — RAG pipeline orchestrator.

Full pipeline:
  1. Query Rewrite (resolve pronouns via history)
  2. Hybrid Retrieve (pgvector + FTS + RRF + threshold)
  3. Rerank (Gemma LLM-as-reranker, top-3)
  4. Generate (Groq GPT-OSS-120B with Elara persona)

Returns a structured result with mode, response, and sources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from collections.abc import AsyncGenerator

from services.rewrite import rewrite_query
from services.retrieval import hybrid_retrieve
from services.rerank import rerank_chunks
from services.generate import (
    generate_response,
    generate_response_stream,
    FALLBACK_MESSAGE,
)

logger = logging.getLogger(__name__)


@dataclass
class RAGResult:
    """Structured result from the RAG pipeline."""
    mode: str = "rag"
    response: str = ""
    sources: list[dict] = field(default_factory=list)
    rewritten_query: str = ""


async def run_rag_pipeline(
    message: str,
    history: list[dict] | None = None,
    stream: bool = False,
) -> RAGResult | AsyncGenerator[str, None]:
    """
    Execute the full RAG pipeline.

    Args:
        message: User's raw message.
        history: Conversation history.
        stream: If True, returns an async generator for SSE streaming.

    Returns:
        RAGResult for non-streaming, or AsyncGenerator[str] for streaming.
    """
    history = history or []

    # ── Step 1: Query Rewrite ───────────────────────────────────────
    rewritten = await rewrite_query(message, history)
    logger.info(f"Rewritten query: '{message}' -> '{rewritten}'")

    # ── Step 2: Hybrid Retrieve + Threshold Check ───────────────────
    chunks = await hybrid_retrieve(rewritten)

    if chunks is None:
        # Check if message is a greeting or general conversational query
        msg_clean = message.strip().lower()
        greeting_keywords = {
            "hai", "hi", "halo", "hello", "hey", "pagi", "selamat pagi",
            "siang", "selamat siang", "sore", "selamat sore", "malam",
            "selamat malam", "siapa kamu", "siapa elara", "kamu siapa",
            "elara", "apa kabar", "terima kasih", "makasih", "thanks",
            "thank you", "permisi", "tes", "test", "ping"
        }
        is_greeting = any(k in msg_clean for k in greeting_keywords) or len(msg_clean.split()) <= 2

        if is_greeting:
            logger.info("Conversational greeting detected, generating warm persona response without KB chunks")
            if stream:
                return generate_response_stream(rewritten, [], history)
            resp = await generate_response(rewritten, [], history)
            return RAGResult(
                response=resp,
                rewritten_query=rewritten,
                sources=[],
            )

        # Explicit non-matching technical query → return fallback
        logger.info("Below similarity threshold, returning fallback")
        if stream:
            return _fallback_stream()
        return RAGResult(
            response=FALLBACK_MESSAGE,
            rewritten_query=rewritten,
        )

    # ── Step 3: Rerank (Gemma top-3) ────────────────────────────────
    chunk_dicts = [
        {
            "chunk_id": c.chunk_id,
            "content": c.content,
            "section": c.section,
            "cosine_sim": c.cosine_sim,
            "rrf_score": c.rrf_score,
            "document_title": c.document_title,
        }
        for c in chunks
    ]

    reranked = await rerank_chunks(rewritten, chunk_dicts)
    logger.info(f"Reranked: {len(chunk_dicts)} -> {len(reranked)} chunks")

    # ── Step 4: Generate ────────────────────────────────────────────
    sources = [
        {"title": c.get("document_title", ""), "score": round(c.get("cosine_sim", 0), 4)}
        for c in reranked
    ]

    if stream:
        return generate_response_stream(rewritten, reranked, history)

    response_text = await generate_response(rewritten, reranked, history)

    return RAGResult(
        response=response_text,
        sources=sources,
        rewritten_query=rewritten,
    )


async def _fallback_stream() -> AsyncGenerator[str, None]:
    """Stream the fallback message character by character (instant)."""
    yield FALLBACK_MESSAGE
