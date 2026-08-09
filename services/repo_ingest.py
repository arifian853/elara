"""
services/repo_ingest.py — Ingest GitHub repositories into Knowledge Base.

Flow:
  1. Parse GitHub URL (owner/repo)
  2. Fetch metadata, README, manifest dependencies, and file tree via GitHub API
  3. Chunk compiled markdown text
  4. Generate Gemini embeddings for each chunk
  5. Insert document (source_type='github') and chunks into Supabase via asyncpg
"""

from __future__ import annotations

import json
import logging
from config import get_pool
from utils.repo_parser import parse_github_url, fetch_github_repo_data
from utils.chunking import chunk_markdown
from services.retrieval import embed_document

logger = logging.getLogger(__name__)


async def ingest_github_repo(repo_url: str) -> dict:
    """
    Ingest a GitHub repository into Supabase vector database.

    Returns dict with status, document_id, title, chunks_created, and metadata.
    """
    owner, repo = parse_github_url(repo_url)

    # 1. Fetch repo data
    repo_data = await fetch_github_repo_data(owner, repo)
    metadata = repo_data["metadata"]
    full_text = repo_data["full_text"]

    if not full_text.strip():
        raise ValueError(f"Repo {owner}/{repo} yielded empty content")

    # 2. Chunk compiled text
    chunks = chunk_markdown(full_text)
    if not chunks:
        raise ValueError(f"Chunking yielded 0 chunks for repo {owner}/{repo}")

    title = f"{owner}/{repo}"

    # 3. Save document & chunks to Supabase
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Delete previous version of this repo document if re-ingesting
        existing_doc_id = await conn.fetchval(
            "SELECT id FROM documents WHERE title = $1 AND source_type = 'github'",
            title,
        )
        if existing_doc_id:
            await conn.execute("DELETE FROM documents WHERE id = $1", existing_doc_id)
            logger.info(f"Deleted existing document {existing_doc_id} for re-ingestion of {title}")

        # Insert new document record
        doc_id = await conn.fetchval(
            """
            INSERT INTO documents (title, source_type, metadata)
            VALUES ($1, 'github', $2::jsonb)
            RETURNING id
            """,
            title,
            json.dumps(metadata),
        )

        # Insert chunks with embeddings
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

    logger.info(f"Ingested GitHub repo '{title}': doc_id={doc_id}, chunks={chunks_created}")

    return {
        "status": "success",
        "document_id": str(doc_id),
        "title": title,
        "chunks_created": chunks_created,
        "metadata": metadata,
    }
