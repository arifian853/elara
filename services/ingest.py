"""
services/ingest.py — Ingest file documents (PDF, DOCX, CSV, MD) into Knowledge Base.

Flow:
  1. Upload raw bytes to Cloudflare R2 (optional)
  2. Parse text content from file bytes
  3. Chunk content using markdown-aware chunker
  4. Generate Gemini embeddings for each chunk
  5. Insert document & chunks into Supabase via asyncpg
"""

from __future__ import annotations

import json
import logging
from config import get_pool
from utils.parsing import parse_file_content
from utils.chunking import chunk_markdown
from utils.r2 import upload_to_r2
from services.retrieval import embed_document

logger = logging.getLogger(__name__)


async def ingest_file_document(
    filename: str,
    file_bytes: bytes,
    source_type: str = "manual",
    metadata: dict | None = None,
) -> dict:
    """
    Ingest a file document into R2 storage and Supabase vector database.

    Returns dict with status, document_id, title, and chunks_created.
    """
    metadata = metadata or {}

    # 1. Upload to Cloudflare R2
    r2_key = await upload_to_r2(file_bytes, filename)

    # 2. Parse text content
    text_content = parse_file_content(file_bytes, filename)
    if not text_content.strip():
        raise ValueError(f"No readable text content found in file {filename}")

    # 3. Chunk text content
    chunks = chunk_markdown(text_content)
    if not chunks:
        raise ValueError(f"Chunking yielded 0 chunks for file {filename}")

    # 4. Save document metadata to Supabase
    pool = await get_pool()
    async with pool.acquire() as conn:
        doc_id = await conn.fetchval(
            """
            INSERT INTO documents (title, source_type, r2_key, metadata)
            VALUES ($1, $2, $3, $4::jsonb)
            RETURNING id
            """,
            filename,
            source_type,
            r2_key,
            json.dumps(metadata),
        )

        # 5. Embed each chunk & insert to Supabase
        chunks_created = 0
        for chunk in chunks:
            embedding = await embed_document(chunk.content)
            vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"

            await conn.execute(
                """
                INSERT INTO chunks (document_id, content, section, token_count, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                """,
                doc_id,
                chunk.content,
                chunk.section,
                chunk.token_count,
                vec_literal,
            )
            chunks_created += 1

    logger.info(f"Ingested file '{filename}': doc_id={doc_id}, chunks={chunks_created}")

    return {
        "status": "success",
        "document_id": str(doc_id),
        "title": filename,
        "chunks_created": chunks_created,
        "r2_key": r2_key,
    }
