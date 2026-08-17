"""
scripts/ingest_mongo_adapted_kb.py — Ingest adapted knowledge base (from old MongoDB) into Supabase pgvector.
"""
import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.ingest import ingest_file_document

ADAPTED_ELARA_KNOWLEDGE = """# Basis Pengetahuan Pribadi Arifian Saputra (Elara Knowledge Base)

## Tentang Elara (Identitas Asisten AI)
Elara adalah asisten AI pribadi dari Arifian Saputra. Elara bertugas membantu menjawab berbagai pertanyaan seputar profil, latar belakang, keahlian teknis, tech stack, portofolio proyek Arifian, serta menerima project brief dan konsultasi jasa dari pengunjung untuk diteruskan langsung ke chat Telegram pribadi Arifian.

## Profil, Umur, dan Latar Belakang Arifian
Arifian Saputra saat ini berusia 23 tahun (kelahiran tahun 2002). Ia adalah seorang Software Engineer, Full-Stack Developer, dan AI Engineer yang berdedikasi membangun aplikasi web modern, sistem backend handal, dan integrasi Artificial Intelligence.

## Asal Daerah dan Domisili
Arifian berasal dari Tanjunguban, Kabupaten Bintan, Kepulauan Riau. Saat ini Arifian tinggal, berdomisili, dan beraktivitas kerja di Kota Batam.

## Pendidikan dan Perguruan Tinggi
Arifian menyelesaikan pendidikan sarjananya di Universitas Maritim Raja Ali Haji (UMRAH), yang berlokasi di Kota Tanjungpinang, Kepulauan Riau.

## Pekerjaan dan Karir Saat Ini
Saat ini Arifian bekerja sebagai AI Technical Mentor di Infinite Learning, sebuah perusahaan teknologi dan pusat edukasi talenta digital yang berlokasi di Kota Batam. Arifian membimbing dan mengajar seputar Artificial Intelligence, machine learning, dan software development.

## Akun Media Sosial dan Kontak Resmi
Pengunjung dapat terhubung dengan Arifian melalui kanal resmi berikut:
- Website Utama: https://arifian.dev
- Instagram: @arifiansaputra_ (https://www.instagram.com/arifiansaputra_/)
- LinkedIn: https://www.linkedin.com/in/arifian-saputra/
- GitHub: https://github.com/arifian853

## Portofolio dan Proyek Unggulan
Arifian aktif membangun berbagai proyek web, backend API, dan sistem AI. Seluruh portofolio proyek lengkap dapat dilihat pada menu Projects di website https://arifian.dev/projects atau langsung melalui repositori GitHub di https://github.com/arifian853.

## Hobi, Minat, dan Rutinitas Keseharian
Di waktu luang, Arifian memiliki hobi bermain game, bereksperimen dengan inovasi AI terbaru, ngoding proyek sampingan, nongkrong bersama teman-teman, dan beristirahat. Pada hari kerja, ia fokus beraktivitas di Infinite Learning Batam, dan di akhir pekan meluangkan waktu untuk eksplorasi teknologi dan proyek kreatif.

## Konsep Dasar Artificial Intelligence (AI)
Artificial Intelligence (Kecerdasan Buatan) adalah cabang ilmu komputer yang berfokus pada perancangan sistem dan algoritma cerdas yang mampu belajar dari data, memahami bahasa alami manusia, mengambil keputusan logis, serta memprediksi informasi secara adaptif.
"""


async def main():
    print("Mulai meng-ingest Basis Pengetahuan Elara (adaptasi MongoDB) ke Supabase pgvector...")
    result = await ingest_file_document(
        filename="elara_knowledge_base.md",
        file_bytes=ADAPTED_ELARA_KNOWLEDGE.encode("utf-8"),
        source_type="manual",
        metadata={"source": "mongodb_migration", "persona": "elara_v1", "adapted": True},
    )
    print(f"[SUCCESS] Ingest berhasil! Doc ID: {result.get('document_id')}, Title: {result.get('title')}, Total Chunks: {result.get('chunks_created')}")


if __name__ == "__main__":
    asyncio.run(main())
