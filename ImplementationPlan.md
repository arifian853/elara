# Elara v1 (Public) — Implementation Plan

**Goal:** Membangun backend chatbot publik "Elara Public" — RAG assistant yang menjawab info Arifian dari knowledge base (dokumen teks/PDF + GitHub Repo Ingestion), plus mode intake project-request yang mengirim brief ke Telegram pribadi Arifian.

**Architecture:** FastAPI di VPS sebagai API gateway async (`app.py`). Pipeline RAG: query rewrite → Gemini Embedding 2 → hybrid search (pgvector + FTS + RRF) → threshold 0.6 pada similarity top-1 pgvector → rerank (gemma-4-26b-a4b-it) → generate (Groq GPT-OSS-120B, mendukung SSE Streaming). Mode intake = state machine 6 langkah (5 pertanyaan + konfirmasi) → simpan ke tabel `leads` → bridge Telegram Bot API (outbound) ke DM Arifian. Fitur Ingestion = File Parsing (PDF/DOCX/CSV/MD) + Repo Ingestion GitHub (README, manifest deps, file tree, & metadata).

**Tech Stack & Tooling:** Python 3.11, **`uv` (Astral Python Package Manager)**, FastAPI (async), asyncpg (Pooler Postgres), Supabase (Postgres 17 + pgvector + FTS), Google AI Studio (`google-genai` SDK: Gemini Embedding 2 + Gemma 4 26B A4B IT), Groq (`groq` SDK: GPT-OSS-120B), Cloudflare R2 (`boto3` S3-compatible, Private Bucket), GitHub API (`httpx`), Uvicorn + systemd. *(Pengembangan fokus menggunakan `uv`, tanpa `pip` manual; tanpa LangChain / LlamaIndex)*.

---

## 1. Struktur Repo

```
arifian-ai-v2/
├── app.py                    # FastAPI entry (jalankan: uv run uvicorn app:app)
├── config.py                 # env, asyncpg pool, clients (groq, google, r2)
├── models.py                 # Pydantic models (max_length=2000)
├── routers/
│   ├── chat.py               # POST /chat (RAG + intake + SSE)
│   ├── admin.py              # upload KB, ingest-repo, leads, stats (API/curl v1)
│   └── health.py             # GET /health (cek Supabase SELECT 1 → 200/503)
├── services/
│   ├── rag.py                # orchestrator: rewrite → retrieve → threshold → rerank → generate
│   ├── retrieval.py          # hybrid pgvector+FTS+RRF (asyncpg)
│   ├── rerank.py             # gemma-4-26b-a4b-it LLM-as-reranker
│   ├── generate.py           # Groq + streaming + 429 backoff
│   ├── rewrite.py            # query rewrite
│   ├── intake.py             # state machine 6 steps
│   ├── bridge.py             # Telegram outbound notification
│   ├── ingest.py             # parse → chunk → embed → upsert (file)
│   └── repo_ingest.py        # ingest repo GitHub → fetch metadata/README/deps → chunk → embed
├── utils/
│   ├── chunking.py           # semantic-ish chunking (markdown-aware)
│   ├── parsing.py            # pypdf, python-docx, csv
│   ├── repo_parser.py        # parser GitHub API: fetch README, package.json/pyproject.toml, tree
│   └── r2.py                 # boto3 S3-compatible client (presigned URL)
├── supabase/
│   └── migrations.sql        # schema lengkap + setup role 'elara' & permissions
├── scripts/
│   ├── run_migration.py      # runner migration via asyncpg
│   ├── verify_migration.py   # verifikasi tabel & pgvector
│   ├── seed_kb.py            # seed 4-5 dokumen awal (profil, layanan, faq, portfolio)
│   └── ping_supabase.py      # anti-pause cron (Hermes / cron)
├── tests/
│   ├── test_retrieval.py     # smoke test hybrid retrieval (3/3 PASSED)
│   ├── test_state_machine_intake.py # smoke test state machine intake (2/2 PASSED)
│   └── test_ingestion.py     # smoke test repo & file ingestion (2/2 PASSED)
├── .env.example
├── pyproject.toml            # uv project & dependency manifest
├── uv.lock                   # uv lockfile untuk reproducible environment
└── README.md                 # Dokumentasi API + catatan Admin UI phase 2
```

---

## 2. Supabase Schema & Security (migrations.sql)

> **Keamanan & Role:** Backend TIDAK menggunakan `SUPABASE_SERVICE_KEY` maupun REST API URL. Backend terhubung langsung ke database via `asyncpg` menggunakan role custom `elara`. RLS **TIDAK** diaktifkan (Auto-RLS off).

```sql
-- 1. Setup Role 'elara' & Hak Akses Database
create role elara with login password 'YOUR_STRONG_PASSWORD_HERE';

grant connect on database postgres to elara;
grant usage on schema public to elara;

alter default privileges in schema public
  grant select, insert, update, delete on tables to elara;
alter default privileges in schema public
  grant usage, select, update on sequences to elara;

-- 2. Extensions
create extension if not exists vector;

-- 3. Tabel Documents (file sumber asli di R2 atau GitHub repo)
create table if not exists documents (
  id          uuid primary key default gen_random_uuid(),
  title       text not null,
  source_type text,                      -- pdf | docx | md | csv | github | manual
  r2_key      text,                      -- object key di R2 (nullable jika github/manual)
  metadata    jsonb default '{}',        -- utk github: {github_url, repo, description, tech_stack, stars, project_order, updated_at}
  created_at  timestamptz default now()
);

-- 4. Tabel Chunks + Vector Indexing (768 dimensions utk Gemini Embedding 2)
create table if not exists chunks (
  id          uuid primary key default gen_random_uuid(),
  document_id uuid references documents(id) on delete cascade,
  content     text not null,
  section     text,
  token_count int,
  embedding   vector(768),
  created_at  timestamptz default now()
);

create index if not exists idx_chunks_hnsw
  on chunks using hnsw (embedding vector_cosine_ops);

create index if not exists idx_chunks_fts
  on chunks using gin (to_tsvector('simple', content));

-- 5. Tabel Leads (hasil Intake Project Request)
create table if not exists leads (
  id          uuid primary key default gen_random_uuid(),
  service     text,
  description text,
  budget      text,
  deadline    text,
  contact     text,
  status      text default 'new',        -- new | contacted | won | lost
  raw         jsonb default '{}',
  created_at  timestamptz default now()
);

-- 6. Tabel Intake Sessions (Persist state machine, anti-kehilangan jika restart)
create table if not exists intake_sessions (
  chat_id    text primary key,
  step       int default 1,
  data       jsonb default '{}',
  updated_at timestamptz default now()
);

-- Grant akses pada tabel yang baru dibuat ke role 'elara'
grant select, insert, update, delete on all tables in schema public to elara;
grant usage, select, update on all sequences in schema public to elara;
```

---

## 3. Environment (.env.example)

```env
# Database Connection (Async connection via asyncpg / Connection Pooler)
# Menggunakan role custom 'elara' — TANPA SUPABASE_SERVICE_KEY & SUPABASE_URL
SUPABASE_DB_URL=postgresql://postgres.your-project:YOUR_PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:5432/postgres

# Groq (Generation + Query Rewrite)
GROQ_API_KEY=gsk_...
GROQ_MODEL=openai/gpt-oss-120b
GROQ_TEMPERATURE=0.3
GROQ_MAX_TOKENS=1000

# Google AI Studio (Embedding + Reranker)
GEMINI_API_KEY=AIzaSy...
EMBEDDING_MODEL=gemini-embedding-2
EMBEDDING_DIMENSIONS=768
RERANKER_MODEL=gemma-4-26b-a4b-it

# Cloudflare R2 (Private Bucket)
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_ENDPOINT=https://...r2.cloudflarestorage.com
R2_BUCKET=elara-chatbot
R2_REGION=auto

# GitHub API (Opsional: Menaikkan Rate Limit API dari 60 menjadi 5000 req/jam)
GITHUB_TOKEN=ghp_...

# Telegram Bridge (Outbound Only)
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here
OWNER_CHAT_ID=your_telegram_chat_id_here

# Security & CORS Settings
ADMIN_TOKEN=openssl_rand_hex_24_generated_token_here
CORS_ORIGINS=https://arifian.dev,https://www.arifian.dev,http://localhost:3000,http://localhost:5173

# RAG Tuning
RETRIEVAL_TOP_K=10
RERANK_TOP_K=3
SIMILARITY_THRESHOLD=0.6
CHUNK_SIZE=800
CHUNK_OVERLAP=0.1
```

---

## 4. Endpoint API Spec

| Method | Path | Fungsi | Auth / Header |
|--------|------|--------|---------------|
| POST | `/chat` | Pesan utama: route RAG / intake (Support SSE Stream) | — |
| POST | `/admin/upload` | Upload file (PDF/DOCX/MD/CSV) → Ingest ke KB & R2 | `X-Admin-Token` |
| POST | `/admin/ingest-repo` | Ingest GitHub repository (README, deps, tree, metadata) | `X-Admin-Token` |
| GET | `/admin/leads` | List data leads | `X-Admin-Token` |
| GET | `/admin/leads/{id}` | Detail lead spesifik | `X-Admin-Token` |
| PATCH | `/admin/leads/{id}` | Update status lead (`new`, `contacted`, `won`, `lost`) | `X-Admin-Token` |
| GET | `/admin/stats` | Ringkasan statistik (`leads`, `chunks`, `documents`) | `X-Admin-Token` |
| GET | `/health` | Liveness + DB Check (`SELECT 1`) → 200/503 | — |

---

## 5. RAG Pipeline Detail & Persona Behavior (services/rag.py)

```
User Message + History
  │
  ├── 1. Query Rewrite (rewrite.py)
  │      -> Menggunakan Groq (1 fast call) untuk menyelesaikan kata rujukan ("dia", "itu").
  │
  ├── 2. Vector Search & Similarity Thresholding (retrieval.py)
  │      -> Generate query embedding via Gemini Embedding 2 (768-dim).
  │      -> Query pgvector via asyncpg:
  │           SELECT content, 1 - (embedding <=> $1) as cosine_sim FROM chunks ORDER BY embedding <=> $1 LIMIT 20
  │      -> [KRITIS] THRESHOLD CHECK:
  │           Ambil cosine_sim dari Chunk Top-1 pgvector.
  │           JIKA top1_cosine_sim < 0.6:
  │             BYPASS RRF & Reranker! Langsung return Fallback Response:
  │             "Maaf, aku belum punya info soal itu. Tapi Arifian pasti bisa jawab — mau aku teruskan pertanyaanmu ke dia? 😊"
  │
  ├── 3. Hybrid Search & RRF Fusion (retrieval.py)
  │      -> JIKA top1_cosine_sim >= 0.6:
  │           Jalankan FTS (Full Text Search) query via asyncpg (top-20).
  │           Lakukan RRF (Reciprocal Rank Fusion) menggabungkan Ranks (bukan skor 0-1).
  │           RRF Score = 1/(60 + rank_vector) + 1/(60 + rank_fts) → Ambil Top-10 chunks.
  │
  ├── 4. LLM Reranking (rerank.py)
  │      -> Panggil gemma-4-26b-a4b-it via Google AI Studio.
  │      -> Gemma bertindak MURNI SEBAGAI PENYORTIR URUTAN (re-orderer), bukan penentu pass/fail.
  │      -> Minta Gemma mengurutkan 3 chunk paling relevan dari 10 candidate chunks.
  │
  └── 5. Generation & Persona Response Rules (generate.py)
         -> Groq GPT-OSS-120B (temperature=0.3, max_tokens=1000).
         -> Persona prompt Elara (Strict mode, ramah, berpijak pada konteks).
         -> ATURAN JAWABAN PROYEK:
            Ketika ditanya seputar "proyek/project Arifian apa saja":
            - Elara menceritakan 3 proyek terbaru dari Knowledge Base (hasil Repo Ingestion / portfolio.md).
            - Mengarahkan pengunjung secara ramah untuk melihat proyek lengkap di halaman `/projects` dan profil GitHub Arifian (`https://github.com/arifian853`).
         -> Support SSE Streaming jika `stream=True`.
         -> Exponential Backoff Retry jika terkena Rate Limit Groq 429.
```

---

## 6. Intake State Machine (services/intake.py) & Telegram Outbound Bridge

### State Machine Workflow (6 Steps)

| Step | Pertanyaan / Aksi | Tipe Input / Pilihan Chips |
|------|-------------------|----------------------------|
| **1** | Mau jasa apa dari Arifian? | `[Web App]`, `[Skripsi/TA]`, `[Coaching]`, `[Desain]`, `[Lainnya]` |
| **2** | Ceritain proyeknya dong (tujuan & fitur utama)... | Free text (pencatatan deskripsi) |
| **3** | Estimasi budget kamu sekitar berapa? | `[<1jt]`, `[1-3jt]`, `[3-5jt]`, `[5jt+]`, `[Belum Tahu]` |
| **4** | Targetnya kapan kelar proyek ini? | `[Buru-buru (<2 mgg)]`, `[1 Bulan]`, `[2-3 Bulan]`, `[Santai]` |
| **5** | Kontak yang bisa dihubungi (WA / Email)? | Free text (validasi format kontak) |
| **6** | Summary Konfirmasi → Simpan ke tabel `leads` → Kirim Telegram Bridge → Pesan Terima Kasih | User klik `[Ya, Kirim]` atau ketik konfirmasi |

---

## 7. Milestones & Rencana Pelaksanaan (`uv` Workflow)

### M0 — Scaffold & Async Database Pool (`uv`) [x]
- [x] Inisialisasi proyek dengan `uv` (Python 3.11).
- [x] `config.py`: Konfigurasi env & inisialisasi `asyncpg.create_pool(dsn=settings.SUPABASE_DB_URL)`.
- [x] `app.py`: Setup CORS (`CORS_ORIGINS`), lifespan asyncpg pool, `/health` endpoint.
- [x] `routers/health.py`: Implementasi `GET /health` dengan query `SELECT 1` ke Supabase (return 503 jika DB gagal).
- [x] Test: `uv run uvicorn app:app --port 8001` → `GET /health` = 200 OK (`{"status": "healthy", "database": "connected"}`).

### M1 — Schema Database & Initial Seed KB [x]
- [x] Eksekusi `supabase/migrations.sql` via `scripts/run_migration.py` (Setup role `elara`, HNSW index, FTS index, tabel `documents`, `chunks`, `leads`, `intake_sessions`).
- [x] Verifikasi tabel via `scripts/verify_migration.py` (4 tabel terbuat + pgvector 0.8.2).
- [ ] Buat 4 dokumen seed awal di `scripts/seed_kb/` (`profil.md`, `layanan.md`, `faq.md`, `portfolio.md`) & jalankan `scripts/seed_kb.py`.

### M2 — Hybrid Retrieval (pgvector + FTS + RRF) & Threshold [x]
- [x] `utils/chunking.py`: Chunking markdown-aware per section heading (max 800 token, overlap 10%).
- [x] `services/retrieval.py`: Vector search (`pgvector`), Cosine Similarity Threshold Check (0.6) pada top-1, FTS, dan RRF Fusion.
- [x] `tests/test_retrieval.py`: Smoke test query "Siapa Arifian?" → 3/3 PASSED (cosine sim = 0.7147 > 0.6).

### M3 — Rerank, Groq Generator, & RAG Orchestrator [x]
- [x] `services/rewrite.py`: Query rewrite (Groq).
- [x] `services/rerank.py`: Integrasi `gemma-4-26b-a4b-it` via `google-genai` SDK sebagai sorter top-3 chunk.
- [x] `services/generate.py`: Groq GPT-OSS-120B (Strict persona Elara, 429 backoff retry, SSE streaming).
- [x] `services/rag.py`: Orchestrator lengkap (Rewrite → Retrieve → Threshold Check → Rerank → Generate).
- [x] `routers/chat.py`: Endpoint `POST /chat` dengan pydantic request model & SSE stream.

### M4 — Intake State Machine & Telegram Outbound Bridge [x]
- [x] `services/intake.py`: State machine 6-step project request dengan timeout 15 menit & cancel handler.
- [x] `services/bridge.py`: Telegram Bot API `sendMessage` outbound function ke DM Arifian.
- [x] `tests/test_state_machine_intake.py`: Smoke test alur 6-step intake & cancel (2/2 PASSED).

### M5 — Ingest KB & GitHub Repo Ingestion (Perluasan Admin) [x]
- [x] `utils/r2.py`: Client `boto3` untuk Cloudflare R2 Private Bucket.
- [x] `utils/parsing.py`: Parser PDF (`pypdf`), DOCX (`python-docx`), CSV, & MD.
- [x] `utils/repo_parser.py`: Parser GitHub API via `httpx` (fetch metadata repo, `README.md`, `package.json`/`pyproject.toml` dependencies, file tree ringkas).
- [x] `services/ingest.py`: Orchestrator file ingest → parse → chunk → embed Gemini 2 → R2 & Supabase.
- [x] `services/repo_ingest.py`: Orchestrator repo ingestion → parse GitHub → markdown chunking → Gemini Embedding 2 → Supabase.
- [x] `routers/admin.py`: Endpoint `POST /admin/upload`, `POST /admin/ingest-repo`, `GET /admin/leads`, `GET /admin/leads/{id}`, `PATCH /admin/leads/{id}`, `GET /admin/stats`.
- [x] `tests/test_ingestion.py`: Smoke test ingestion (2/2 PASSED). Total test suite **7/7 PASSED**.

### M5.5 — Replikasi Fitur Tambahan dari backend-ai (Iterasi Mendatang)
> **CATATAN:** *Rencana fitur ini dipersiapkan untuk iterasi berikutnya (TIDAK DIEKSEKUSI PADA ITERASI SEKARANG).*

#### 1. Fitur Pesan Anonim / Confess & Feedback (`confessions`)
- **Skema Database (`confessions`):**
  ```sql
  create table if not exists confessions (
    id               uuid primary key default gen_random_uuid(),
    message          text not null,
    ip_address       text,
    reply            text,
    reply_created_at timestamptz,
    is_replied       boolean default false,
    created_at       timestamptz default now()
  );
  grant select, insert, update, delete on confessions to elara;
  ```
- **Endpoint Publik (halaman `/message` portofolio):**
  - `POST /confessions/public/submit`: Mengirim pesan anonim dari pengunjung (max 500 karakter). Mengirim notifikasi Telegram outbound ke DM Arifian via `services/bridge.py`.
  - `GET /confessions/public/list`: Mengambil daftar pesan anonim beserta balasan dari Arifian untuk ditampilkan di halaman `/message`.
- **Endpoint Admin Management:**
  - `GET /admin/confessions`: Menampilkan daftar seluruh pesan anonim beserta status balasan.
  - `POST /admin/confessions/{id}/reply`: Memberikan atau memperbarui jawaban/balasan publik dari Arifian.
  - `DELETE /admin/confessions/{id}`: Menghapus pesan anonim.

#### 2. Dynamic System Prompt Manager
- **Skema Database (`system_prompts`):**
  ```sql
  create table if not exists system_prompts (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    prompt      text not null,
    description text,
    is_active   boolean default false,
    created_at  timestamptz default now()
  );
  ```
- **Endpoints Admin:**
  - `GET /admin/system-prompts`: List seluruh system prompt.
  - `GET /admin/system-prompts/active`: Mengambil system prompt yang sedang aktif.
  - `POST /admin/system-prompts`: Membuat system prompt baru.
  - `PUT /admin/system-prompts/{id}`: Update prompt / setel sebagai aktif.
  - `DELETE /admin/system-prompts/{id}`: Hapus system prompt.

#### 3. JWT Authentication & Multi-User Admin (Upgrade dari X-Admin-Token)
- **Endpoints Auth:**
  - `POST /admin/auth/login`: Authentication admin berbasis JWT bearer token (`access_token`, `token_type`, expire 60 menit).
  - `GET /admin/auth/me`: Mengambil info profile user admin yang sedang login.
  - `POST /admin/auth/users`: Manajemen user/akun admin tambahan.

### M5.6 — Frontend Admin UI (Elara Admin Dashboard — Minimalist Brutalism)
> **CATATAN:** *Rencana antarmuka GUI Admin didesain mengikuti standar estetika persis [DESIGN.md](file:///d:/Projects/portfolio-LTS/frontend/DESIGN.md).*

- **Prinsip Desain & Tema (Mengikuti DESIGN.md):**
  - **Minimalist Brutalism (`rounded-none`):** Seluruh card, button, badge, modal dialog, dan input menggunakan sudut tajam `0px` (`rounded-none`).
  - **Color Palette (Claude.ai Warm Aesthetics):**
    - Background: Parchment `#f5f4ed` (Light) / Near Black `#141413` (Dark)
    - Card Surface: Ivory `#faf9f5` (Light) / Dark Surface `#30302e` (Dark)
    - Brand CTA & Accents: Terracotta Brand `#c96442`, Terracotta Accent `#b55333`, Coral Accent `#d97757`
    - Borders: Border Cream `#f0eee6`
  - **Typography:** `Lexend Deca` (Heading) & `Inclusive Sans` (Body & UI text).
  - **Layout:** Bento Grid modular dengan hover border highlights & animasi Framer Motion.

- **Halaman & Komponen Dashboard Admin (Frontend Admin / React):**
  1. **Overview & Analytics Bento Grid:** Counter Cards (Total Chunks, Repos, Leads, Confessions, System Health).
  2. **Knowledge Base & Repo Ingestion Manager:** Drag-and-drop file upload, form ingest GitHub repo, & tabel dokumen.
  3. **Leads Kanban & Data Grid:** Filter status (`new`, `contacted`, `won`, `lost`) & lead detail modal.
  4. **Confessions Manager:** Balasan publik & notifikasi status.
  5. **System Prompt Manager:** Textarea editor system prompt.

### M5.7 — Bidirectional Telegram Bot Integration ("Hermes Skill Bridge")
> **CATATAN:** *TIDAK MENGGUNAKAN WEBHOOK (`POST /telegram/webhook`). Telegram Bot API hanya mengizinkan 1 metode per token (`getUpdates` XOR `setWebhook`). Memasang webhook di backend akan merusak Hermes gateway (bot Elara privat di Telegram).*

- **1. Outbound Notifications (Sudah Aktif di M4):**
  - `services/bridge.py` mengirim notifikasi lead baru ke DM pribadi Arifian via Telegram Bot API `sendMessage`. Ini *outbound HTTP request* biasa yang **100% aman** dan tidak memicu konflik dengan polling Hermes. Inline action buttons di-skip pada v1.

- **2. Inbound Commands & Natural Language Intent via Hermes:**
  - Ditangani sepenuhnya oleh **HERMES** (Elara di Telegram), BUKAN oleh backend.
  - Backend menyediakan Admin API lengkap (semua diproteksi `X-Admin-Token`):
    - `GET /admin/leads` (list leads)
    - `GET /admin/leads/{id}` (detail lead)
    - `PATCH /admin/leads/{id}` (update status)
    - `GET /admin/stats` (ringkasan statistik `leads`, `chunks`, `documents`)
  - **Hermes Skill `elara-admin`:** Hermes diberikan skill baru yang memanggil Admin API di `http://localhost:8000` (internal dalam 1 VPS, tanpa expose publik).
  - Hermes memahami perintah maupun bahasa alami: *"ada leads tuh?"*, *"yang baru kontak siapa?"*, `/status <id> contacted`, `/stats`.
  - `ADMIN_TOKEN` disalin ke `~/.hermes/.env` (tidak pernah keluar dari VPS).

### M6 — Deployment, SSE Polish, & Anti-Pause Cron
- [ ] Integrasi SSE Streaming di `POST /chat` (`stream: true`).
- [ ] Deploy ke VPS dengan `systemd` (`arifian-elara.service`) & `uvicorn` (1 worker: `uv run uvicorn app:app --port 8000`).
- [ ] Setup `slowapi` rate limiting di `/chat` (30 req/menit per IP).
- [ ] Chron job anti-pause Supabase (Hermes cron ping tiap 6 jam).

---

## 8. Catatan Batasan & Ruang Lingkup (Scope Constraints)

1. **Tooling & Environment (`uv`):** Seluruh manajemen paket, virtual environment, dan eksekusi skrip/server menggunakan **`uv`** (misal: `uv add <package>`, `uv run uvicorn app:app`, `uv run pytest`). Tidak menggunakan `pip` manual untuk menjamin performa cepat dan lingkungan yang konsisten (*reproducible via `uv.lock`*).
2. **GitHub Ingestion Scope (v1):** Yang diserap hanya `README.md`, manifest dependensi (`package.json`/`pyproject.toml`), ringkasan struktur folder (file tree), dan metadata GitHub (stars, language, updated_at). *Raw code `.ts`/`.py` lengkap tidak diserap di v1* untuk menjaga efisiensi token & fokus pada pemahaman tingkat tinggi (*high-level architecture*).
3. **Admin UI Scope:** Pada versi v1 ini, manajemen admin (ingest KB, ingest repo, & view leads) diselesaikan murni berbasis **API Endpoint & cURL** yang diproteksi header `X-Admin-Token`. Antarmuka React Admin UI dialokasikan pada M5.6.
4. **Groq Quota & Rate Limit:** Kuota Groq gratis dipantau secara berkala. Jika terjadi HTTP 429, backend melakukan *retry backoff* 2x lalu menampilkan pesan fallback ramah.
5. **Pemberhentian Sementara Supabase (Anti-Pause):** Supabase free tier memasuki mode pause jika 7 hari tanpa query SQL. Solusinya ditangani oleh skrip ping otomatis di Hermes cron.
6. **Inbound Telegram Interaction:** Interaksi inbound Telegram (baca leads/ngobrol seputar leads di Telegram) bergantung pada Hermes yang berjalan di VPS. Jika Hermes mati, notifikasi outbound dan Admin Web/cURL API tetap berjalan 100% normal.