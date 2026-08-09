"""
tests/test_ingestion.py — Smoke test for repo parsing & document ingestion.

Run: uv run pytest tests/test_ingestion.py -v -s
"""

from __future__ import annotations

import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from utils.repo_parser import parse_github_url
from utils.chunking import chunk_markdown
from services.ingest import ingest_file_document


def test_parse_github_url():
    """Verify GitHub URL parser extracts owner and repo correctly."""
    owner, repo = parse_github_url("https://github.com/arifian853/portfolio-LTS")
    assert owner == "arifian853"
    assert repo == "portfolio-LTS"

    owner2, repo2 = parse_github_url("https://github.com/fastapi/fastapi.git/")
    assert owner2 == "fastapi"
    assert repo2 == "fastapi"


@pytest.mark.asyncio
async def test_file_ingest_markdown():
    """Test file document ingestion with markdown text."""
    sample_md = """# Test Document
## Section 1
This is a test section for document ingestion.

## Section 2
Another section with information about Arifian's services.
"""
    file_bytes = sample_md.encode("utf-8")
    result = await ingest_file_document(
        filename="__test_ingest_sample__.md",
        file_bytes=file_bytes,
        source_type="manual",
    )

    assert result["status"] == "success"
    assert result["chunks_created"] > 0
    print(f"\n[PASS] Ingested document ID: {result['document_id']}, Chunks: {result['chunks_created']}")

    # Clean up test document from DB
    from config import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM documents WHERE title = '__test_ingest_sample__.md'")
    print("[CLEANUP] Cleaned up test document")
