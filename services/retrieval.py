"""
services/retrieval.py — Hybrid retrieval: pgvector + FTS + RRF fusion.

Pipeline:
  1. Embed query via Gemini Embedding 2
  2. Vector search (pgvector cosine) → top-K
  3. Threshold check on top-1 cosine similarity (≥ 0.6)
  4. If passes → FTS search → RRF fusion → return merged top-K
  5. If fails  → return None (caller handles fallback)
"""

from __future__ import annotations

from dataclasses import dataclass

from google import genai

from config import settings, get_pool


# ── Gemini embedding client (module-level singleton) ────────────────

_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=settings.gemini_api_key)
    return _genai_client


# ── Data classes ────────────────────────────────────────────────────

@dataclass
class RetrievedChunk:
    """A chunk returned from hybrid retrieval."""
    chunk_id: str
    content: str
    section: str | None
    cosine_sim: float        # from vector search (0-1)
    rrf_score: float         # combined RRF score
    document_title: str


import asyncio

# ── Embedding ───────────────────────────────────────────────────────

async def embed_query(query: str, max_retries: int = 3) -> list[float]:
    """
    Generate embedding for a query string via Gemini Embedding 2 with retry.

    Uses task_type="RETRIEVAL_QUERY" for query-optimized embeddings.
    """
    client = _get_genai_client()
    for attempt in range(max_retries):
        try:
            response = client.models.embed_content(
                model=settings.embedding_model,
                contents=query,
                config={
                    "output_dimensionality": settings.embedding_dimensions,
                    "task_type": "RETRIEVAL_QUERY",
                },
            )
            return response.embeddings[0].values
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
            else:
                raise e


async def embed_document(text: str, max_retries: int = 3) -> list[float]:
    """
    Generate embedding for a document chunk via Gemini Embedding 2 with retry.

    Uses task_type="RETRIEVAL_DOCUMENT" for document-optimized embeddings.
    """
    client = _get_genai_client()
    for attempt in range(max_retries):
        try:
            response = client.models.embed_content(
                model=settings.embedding_model,
                contents=text,
                config={
                    "output_dimensionality": settings.embedding_dimensions,
                    "task_type": "RETRIEVAL_DOCUMENT",
                },
            )
            return response.embeddings[0].values
        except Exception as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
            else:
                raise e


# ── Vector Search (pgvector) ───────────────────────────────────────

async def _vector_search(
    query_embedding: list[float],
    top_k: int = 20,
) -> list[dict]:
    """
    Search chunks by cosine similarity using pgvector HNSW index.

    Returns list of dicts with: chunk_id, content, section, cosine_sim, document_title.
    """
    pool = await get_pool()

    # Format embedding as pgvector literal: '[0.1,0.2,...]'
    vec_literal = "[" + ",".join(str(v) for v in query_embedding) + "]"

    sql = """
        SELECT
            c.id AS chunk_id,
            c.content,
            c.section,
            1 - (c.embedding <=> $1::vector) AS cosine_sim,
            d.title AS document_title
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        ORDER BY c.embedding <=> $1::vector
        LIMIT $2
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, vec_literal, top_k)

    return [
        {
            "chunk_id": str(r["chunk_id"]),
            "content": r["content"],
            "section": r["section"],
            "cosine_sim": float(r["cosine_sim"]),
            "document_title": r["document_title"],
        }
        for r in rows
    ]


# ── Full-Text Search (FTS) ─────────────────────────────────────────

async def _fts_search(
    query: str,
    top_k: int = 20,
) -> list[dict]:
    """
    Search chunks using PostgreSQL full-text search (GIN index).

    Uses 'simple' config (no stemming) for multilingual ID/EN support.
    Returns list of dicts with: chunk_id, content, section, fts_rank, document_title.
    """
    pool = await get_pool()

    # Build tsquery: split words and join with '&' (AND)
    words = query.strip().split()
    if not words:
        return []
    tsquery = " & ".join(words)

    sql = """
        SELECT
            c.id AS chunk_id,
            c.content,
            c.section,
            ts_rank(to_tsvector('simple', c.content), to_tsquery('simple', $1)) AS fts_rank,
            d.title AS document_title
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE to_tsvector('simple', c.content) @@ to_tsquery('simple', $1)
        ORDER BY fts_rank DESC
        LIMIT $2
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, tsquery, top_k)

    return [
        {
            "chunk_id": str(r["chunk_id"]),
            "content": r["content"],
            "section": r["section"],
            "fts_rank": float(r["fts_rank"]),
            "document_title": r["document_title"],
        }
        for r in rows
    ]


# ── Reciprocal Rank Fusion (RRF) ───────────────────────────────────

def _rrf_fusion(
    vector_results: list[dict],
    fts_results: list[dict],
    k: int = 60,
) -> list[dict]:
    """
    Merge vector and FTS results using Reciprocal Rank Fusion.

    RRF score = 1/(k + rank_vector) + 1/(k + rank_fts)
    where k=60 is the standard smoothing constant.

    Returns merged results sorted by RRF score descending.
    """
    # Build rank maps (1-indexed)
    vector_ranks: dict[str, int] = {}
    for i, r in enumerate(vector_results, 1):
        vector_ranks[r["chunk_id"]] = i

    fts_ranks: dict[str, int] = {}
    for i, r in enumerate(fts_results, 1):
        fts_ranks[r["chunk_id"]] = i

    # Collect all unique chunk IDs
    all_ids = set(vector_ranks.keys()) | set(fts_ranks.keys())

    # Build a lookup for chunk data (prefer vector result data, fall back to FTS)
    chunk_data: dict[str, dict] = {}
    for r in vector_results:
        chunk_data[r["chunk_id"]] = r
    for r in fts_results:
        if r["chunk_id"] not in chunk_data:
            chunk_data[r["chunk_id"]] = r

    # Calculate RRF scores
    # If a chunk doesn't appear in one list, use a large rank (len+1) as penalty
    max_vector_rank = len(vector_results) + 1
    max_fts_rank = len(fts_results) + 1

    scored: list[dict] = []
    for cid in all_ids:
        rank_v = vector_ranks.get(cid, max_vector_rank)
        rank_f = fts_ranks.get(cid, max_fts_rank)
        rrf_score = 1.0 / (k + rank_v) + 1.0 / (k + rank_f)

        data = chunk_data[cid]
        scored.append({
            "chunk_id": cid,
            "content": data["content"],
            "section": data.get("section"),
            "cosine_sim": data.get("cosine_sim", 0.0),
            "rrf_score": rrf_score,
            "document_title": data.get("document_title", ""),
        })

    # Sort by RRF score descending
    scored.sort(key=lambda x: x["rrf_score"], reverse=True)
    return scored


# ── Main Retrieval Function ─────────────────────────────────────────

async def hybrid_retrieve(
    query: str,
    top_k: int | None = None,
    similarity_threshold: float | None = None,
) -> list[RetrievedChunk] | None:
    """
    Hybrid retrieval pipeline:
      1. Embed query via Gemini Embedding 2
      2. Vector search (pgvector top-20)
      3. Full-Text Search (FTS top-20)
      4. Threshold check: passes if top vector similarity >= threshold OR FTS keyword match found
      5. RRF fusion
      6. Return top-K merged chunks

    Returns:
        List of RetrievedChunk if relevant context is found.
        None if top-1 cosine sim < threshold and no FTS match (caller should use fallback).
    """
    if top_k is None:
        top_k = settings.retrieval_top_k
    if similarity_threshold is None:
        similarity_threshold = settings.similarity_threshold

    # Step 1: Embed query
    query_embedding = await embed_query(query)

    # Step 2: Vector search
    vector_results = await _vector_search(query_embedding, top_k=20)

    # Step 3: FTS search
    fts_results = await _fts_search(query, top_k=20)

    if not vector_results and not fts_results:
        return None  # No chunks in DB at all

    top1_cosine_sim = vector_results[0]["cosine_sim"] if vector_results else 0.0
    has_fts_matches = len(fts_results) > 0

    # Step 4: Relevance check (Passes if vector similarity >= threshold OR FTS keyword match exists)
    if top1_cosine_sim < similarity_threshold and not has_fts_matches:
        return None

    # Step 5: RRF fusion
    merged = _rrf_fusion(vector_results, fts_results)

    # Step 6: Return top-K unique content chunks
    unique_results = []
    seen_contents = set()
    for r in merged:
        normalized_content = r["content"].strip()
        if normalized_content not in seen_contents:
            seen_contents.add(normalized_content)
            unique_results.append(
                RetrievedChunk(
                    chunk_id=r["chunk_id"],
                    content=r["content"],
                    section=r["section"],
                    cosine_sim=r["cosine_sim"],
                    rrf_score=r["rrf_score"],
                    document_title=r["document_title"],
                )
            )
        if len(unique_results) >= top_k:
            break

    return unique_results
