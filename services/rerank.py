"""
services/rerank.py — LLM-as-reranker using Gemma 4 26B via Google AI Studio.

Gemma acts PURELY as an order-sorter (re-orderer), NOT as a pass/fail gate.
It picks the top-N most relevant chunks from candidates and returns them
in ranked order.
"""

from __future__ import annotations

from google import genai

from config import settings


_genai_client: genai.Client | None = None


def _get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        _genai_client = genai.Client(api_key=settings.gemini_api_key)
    return _genai_client


RERANK_PROMPT_TEMPLATE = """Kamu adalah reranker. Diberikan sebuah QUERY dan {n} CHUNK teks.
Tugasmu: urutkan chunk berdasarkan relevansi terhadap query.

Balas HANYA dengan daftar nomor chunk yang paling relevan, diurutkan dari paling relevan.
Pilih maksimal {top_k} chunk terbaik.
Format jawaban: hanya angka dipisah koma, contoh: 2,5,1

QUERY: {query}

{chunks_text}"""


async def rerank_chunks(
    query: str,
    chunks: list[dict],
    top_k: int | None = None,
) -> list[dict]:
    """
    Rerank candidate chunks using Gemma as LLM-as-reranker.

    Args:
        query: The user's (rewritten) query.
        chunks: List of dicts with at least 'content' and other metadata.
        top_k: Number of top chunks to return (default from settings).

    Returns:
        Reranked list of chunk dicts, ordered by relevance (top_k items).
        On error, returns the first top_k chunks unchanged (graceful fallback).
    """
    if top_k is None:
        top_k = settings.rerank_top_k

    if len(chunks) <= top_k:
        return chunks  # No point reranking if fewer than top_k

    # Build numbered chunk text for the prompt
    chunks_lines = []
    for i, c in enumerate(chunks, 1):
        preview = c["content"][:300]  # Limit chunk preview to save tokens
        chunks_lines.append(f"CHUNK {i}:\n{preview}")
    chunks_text = "\n\n".join(chunks_lines)

    prompt = RERANK_PROMPT_TEMPLATE.format(
        n=len(chunks),
        top_k=top_k,
        query=query,
        chunks_text=chunks_text,
    )

    try:
        client = _get_genai_client()
        response = client.models.generate_content(
            model=settings.reranker_model,
            contents=prompt,
            config={
                "temperature": 0.0,
                "max_output_tokens": 50,
            },
        )

        # Parse response: expect comma-separated numbers like "2,5,1"
        raw = response.text.strip()
        indices = _parse_rerank_response(raw, len(chunks))

        if not indices:
            # Parsing failed — return top_k chunks as-is
            return chunks[:top_k]

        # Build reranked list
        reranked = []
        for idx in indices[:top_k]:
            reranked.append(chunks[idx])
        return reranked

    except Exception:
        # On any error, gracefully fall back to original order
        return chunks[:top_k]


def _parse_rerank_response(raw: str, max_idx: int) -> list[int]:
    """
    Parse Gemma's response into 0-indexed chunk indices.

    Handles formats like: "2,5,1" or "2, 5, 1" or "CHUNK 2, CHUNK 5, CHUNK 1"
    """
    import re

    # Extract all numbers from the response
    numbers = re.findall(r"\d+", raw)

    indices = []
    seen = set()
    for n in numbers:
        idx = int(n) - 1  # Convert 1-indexed to 0-indexed
        if 0 <= idx < max_idx and idx not in seen:
            indices.append(idx)
            seen.add(idx)

    return indices
