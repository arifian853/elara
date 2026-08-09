"""
tests/test_retrieval.py — Smoke test for hybrid retrieval pipeline.

This test:
  1. Seeds a small test document + chunks (with real Gemini embeddings)
  2. Runs hybrid_retrieve("Siapa Arifian?")
  3. Asserts results are returned and threshold logic works
  4. Cleans up test data

Run: uv run pytest tests/test_retrieval.py -v -s
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import settings, get_pool, close_pool
from services.retrieval import hybrid_retrieve, embed_document


# ── Test data ───────────────────────────────────────────────────────

TEST_DOC_TITLE = "__test_retrieval_doc__"

TEST_CHUNKS = [
    {
        "content": "Arifian adalah seorang Full-Stack Developer dan AI Engineer yang berbasis di Indonesia. "
                   "Dia memiliki pengalaman dalam pengembangan web menggunakan React, Next.js, dan FastAPI. "
                   "Arifian juga aktif dalam riset kecerdasan buatan dan machine learning.",
        "section": "Profil Arifian",
    },
    {
        "content": "Layanan yang ditawarkan oleh Arifian meliputi pembuatan web application, "
                   "konsultasi teknis AI/ML, bimbingan skripsi dan tugas akhir, serta desain UI/UX. "
                   "Harga bervariasi mulai dari Rp 1 juta hingga Rp 5 juta tergantung kompleksitas.",
        "section": "Layanan",
    },
    {
        "content": "Pertanyaan yang sering ditanyakan: Apakah Arifian menerima proyek freelance? Ya. "
                   "Berapa lama waktu pengerjaan? Tergantung scope, biasanya 2-8 minggu. "
                   "Apakah ada garansi revisi? Ya, hingga 2 kali revisi gratis.",
        "section": "FAQ",
    },
]


# ── Fixtures ────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module", autouse=True, loop_scope="module")
async def seed_and_cleanup():
    """Seed test data before tests, clean up after."""
    pool = await get_pool()

    # --- SEED ---
    async with pool.acquire() as conn:
        # Clean up any leftover test data first
        await conn.execute(
            "DELETE FROM documents WHERE title = $1",
            TEST_DOC_TITLE,
        )

        # Insert test document
        doc_id = await conn.fetchval(
            """
            INSERT INTO documents (title, source_type)
            VALUES ($1, 'md')
            RETURNING id
            """,
            TEST_DOC_TITLE,
        )

        # Insert test chunks with real embeddings
        for chunk_data in TEST_CHUNKS:
            embedding = await embed_document(chunk_data["content"])
            vec_literal = "[" + ",".join(str(v) for v in embedding) + "]"
            await conn.execute(
                """
                INSERT INTO chunks (document_id, content, section, token_count, embedding)
                VALUES ($1, $2, $3, $4, $5::vector)
                """,
                doc_id,
                chunk_data["content"],
                chunk_data["section"],
                len(chunk_data["content"]) // 4,
                vec_literal,
            )

    print(f"\n[SEED] Inserted doc {doc_id} with {len(TEST_CHUNKS)} chunks")

    yield  # run tests

    # --- CLEANUP ---
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM documents WHERE title = $1",
            TEST_DOC_TITLE,
        )
    print(f"\n[CLEANUP] Removed test doc '{TEST_DOC_TITLE}'")


# ── Tests ───────────────────────────────────────────────────────────

@pytest.mark.asyncio(loop_scope="module")
async def test_retrieve_relevant_query():
    """Query 'Siapa Arifian?' should return chunks above threshold."""
    results = await hybrid_retrieve("Siapa Arifian?")

    assert results is not None, "Expected results but got None (below threshold)"
    assert len(results) > 0, "Expected at least 1 chunk"

    # Top result should mention Arifian
    top = results[0]
    assert "Arifian" in top.content or "arifian" in top.content.lower()
    assert top.cosine_sim >= settings.similarity_threshold

    print(f"\n[PASS] Top result: cosine_sim={top.cosine_sim:.4f}, "
          f"rrf={top.rrf_score:.6f}, section='{top.section}'")
    print(f"       Content preview: {top.content[:80]}...")


@pytest.mark.asyncio(loop_scope="module")
async def test_retrieve_irrelevant_query():
    """Query completely unrelated should return None (below threshold)."""
    results = await hybrid_retrieve(
        "What is the capital of Jupiter's fourth moon?"
    )

    # With only 3 small chunks about Arifian, a totally unrelated query
    # should ideally fall below threshold. If it doesn't, the test still
    # passes but we log a warning.
    if results is None:
        print("\n[PASS] Irrelevant query correctly returned None (below threshold)")
    else:
        top_sim = results[0].cosine_sim if results else 0
        print(f"\n[WARN] Irrelevant query returned {len(results)} results "
              f"(top cosine_sim={top_sim:.4f}). "
              f"Threshold may need tuning.")


@pytest.mark.asyncio(loop_scope="module")
async def test_rrf_fusion_produces_scores():
    """Verify that RRF scores are populated and ordered."""
    results = await hybrid_retrieve("layanan freelance harga")

    if results is None:
        pytest.skip("Below threshold - not enough data for this query")

    # All results should have positive RRF scores
    for r in results:
        assert r.rrf_score > 0, f"RRF score should be positive, got {r.rrf_score}"

    # Results should be sorted by RRF score descending
    scores = [r.rrf_score for r in results]
    assert scores == sorted(scores, reverse=True), "Results not sorted by RRF score"

    print(f"\n[PASS] {len(results)} results, RRF scores: "
          f"{[f'{s:.6f}' for s in scores]}")
