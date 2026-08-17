"""
scripts/ingest_projects_documents.py — Ingest agregat.md and projects.md into Supabase pgvector.
"""
import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ingest import ingest_file_document

async def main():
    knowledge_dir = Path(__file__).resolve().parent.parent / "knowledge"

    files_to_ingest = [
        ("agregat.md", {"category": "projects_aggregate", "type": "summary_table", "updated": "2026"}),
        ("projects.md", {"category": "projects_detail", "type": "full_portfolio", "updated": "2026"}),
    ]

    for filename, meta in files_to_ingest:
        file_path = knowledge_dir / filename
        if not file_path.exists():
            print(f"[ERROR] File {file_path} not found!")
            continue

        print(f"\n--- Ingesting {filename} ---")
        content_bytes = file_path.read_bytes()
        result = await ingest_file_document(
            filename=filename,
            file_bytes=content_bytes,
            source_type="manual",
            metadata=meta,
        )
        print(f"[SUCCESS] {filename} ingested! Doc ID: {result.get('document_id')}, Chunks: {result.get('chunks_created')}")

    print("\n[COMPLETE] Seluruh dokumen proyek berhasil di-ingest ke Supabase pgvector!")

if __name__ == "__main__":
    asyncio.run(main())
