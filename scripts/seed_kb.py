"""
scripts/seed_kb.py — Seed initial Knowledge Base document into pgvector.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ingest import ingest_file_document

INITIAL_PROFILE_TEXT = """
# Profil & Layanan Arifian Saputra

Arifian Saputra adalah seorang Full-Stack Developer dan AI Engineer yang berdomisili di Indonesia.
Arifian berfokus pada pengembangan aplikasi web modern, sistem backend terdistribusi, integrasi AI/RAG, dan agen otomatis.

## Layanan Utama:
1. **Pengembangan Web App / Full-Stack Application:**
   - Stack: Next.js, React, TypeScript, TailwindCSS, FastAPI, Node.js, PostgreSQL/Supabase.
   - Estimasi Budget: IDR 1.000.000 - 3.000.000+ (tergantung skala proyek).
   - Waktu pengerjaan: 1-4 Minggu.

2. **Integrasi AI / RAG / Chatbot / Subagent:**
   - Stack: OpenAI, Groq, Gemini, Supabase pgvector, LangChain, LlamaIndex, Hermes Agent.
   - Pengerjaan sistem chatbot cerdas, pencarian dokumen, dan agen Telegram/WhatsApp.

3. **Konsultasi & Optimization Codebase:**
   - Audit keamanan backend, optimasi query database, integrasi CI/CD, dan setup VPS.

## Kontak & Media Sosial:
- Website: https://arifian.dev
- Telegram: @autumn_elara_nymph_bot / Direct Contact Arifian
- Email: arifian@dev.local
"""


async def main():
    print("Mulai seeding Knowledge Base awal ke pgvector...")
    result = await ingest_file_document(
        filename="profil_arifian.md",
        file_bytes=INITIAL_PROFILE_TEXT.encode("utf-8"),
        source_type="manual",
        metadata={"category": "profile", "seeded": True},
    )
    print(f"[SUCCESS] Knowledge Base awal berhasil di-ingest! Title: {result.get('title')}, Total Chunks: {result.get('chunks_created')}")


if __name__ == "__main__":
    asyncio.run(main())
