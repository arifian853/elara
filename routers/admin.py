"""
routers/admin.py — Admin endpoints for KB ingestion, leads management, and JWT auth.

Protected by X-Admin-Token header or Authorization: Bearer <jwt_token>.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import settings, get_pool
from models import (
    IngestRepoRequest,
    IngestRepoResponse,
    IngestTextRequest,
    AdminLoginRequest,
    AdminLoginResponse,
)
from services.ingest import ingest_file_document
from services.repo_ingest import ingest_github_repo
from services.auth import create_access_token, verify_jwt_token
from utils.chunking import chunk_markdown
from services.retrieval import embed_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


def verify_admin_token(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """
    Verify authentication via X-Admin-Token OR Authorization: Bearer <jwt_token>.
    """
    if not settings.admin_token:
        raise HTTPException(status_code=500, detail="ADMIN_TOKEN not configured on server")

    # 1. Check X-Admin-Token
    if x_admin_token and x_admin_token == settings.admin_token:
        return {"sub": "admin"}

    # 2. Check Authorization Bearer Token
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]

            # If raw token equals admin_token
            if token == settings.admin_token:
                return {"sub": "admin"}

            # Verify JWT
            payload = verify_jwt_token(token)
            if payload and payload.get("sub"):
                return payload

    raise HTTPException(status_code=401, detail="Unauthorized: Invalid Admin Credentials or Token")


from models import (
    IngestRepoRequest,
    IngestRepoResponse,
    IngestTextRequest,
    AdminLoginRequest,
    AdminLoginResponse,
    CreateUserRequest,
    ChangePasswordRequest,
)
from services.auth import create_access_token, verify_jwt_token, hash_password, verify_password
import time
import httpx
from groq import AsyncGroq

# ── Auth & User Management Endpoints ───────────────────────────────

@router.post("/auth/login", response_model=AdminLoginResponse)
async def admin_login(payload: AdminLoginRequest):
    """
    Admin login endpoint.
    Checks DB admin_users table first, falling back to default env ADMIN_TOKEN.
    """
    username = payload.username.strip()
    password = payload.password

    pool = await get_pool()
    async with pool.acquire() as conn:
        user_row = await conn.fetchrow(
            "SELECT id, username, password_hash FROM admin_users WHERE username = $1",
            username,
        )
        if user_row:
            if verify_password(password, user_row["password_hash"]):
                token = create_access_token(data={"sub": username, "role": "admin"})
                return AdminLoginResponse(access_token=token, token_type="bearer")
            else:
                raise HTTPException(status_code=401, detail="Incorrect username or password")

    # Fallback to default admin user using settings.admin_token
    default_user = "admin"
    if settings.admin_token and username == default_user and password == settings.admin_token:
        token = create_access_token(data={"sub": username, "role": "admin"})
        return AdminLoginResponse(access_token=token, token_type="bearer")

    raise HTTPException(status_code=401, detail="Incorrect username or password")


@router.get("/auth/me")
async def get_current_admin(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get current authenticated admin info."""
    user = verify_admin_token(x_admin_token, authorization)
    return {"username": user.get("sub", "admin"), "role": "admin", "status": "active"}


@router.get("/users")
async def list_admin_users(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """List all admin users."""
    verify_admin_token(x_admin_token, authorization)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, username, created_at FROM admin_users ORDER BY created_at ASC")

    users = [{"id": str(r["id"]), "username": r["username"], "createdAt": r["created_at"].isoformat()} for r in rows]
    if not users:
        # Include default admin if no DB users created yet
        users.append({"id": "default-admin", "username": "admin", "createdAt": "Default System User"})

    return users


@router.post("/users")
async def create_admin_user(
    payload: CreateUserRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Create a new admin user."""
    verify_admin_token(x_admin_token, authorization)
    username = payload.username.strip()
    pwd_hash = hash_password(payload.password)

    pool = await get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval("SELECT id FROM admin_users WHERE username = $1", username)
        if existing:
            raise HTTPException(status_code=400, detail=f"Username '{username}' already exists")

        new_id = await conn.fetchval(
            "INSERT INTO admin_users (username, password_hash) VALUES ($1, $2) RETURNING id",
            username,
            pwd_hash,
        )

    return {"status": "success", "id": str(new_id), "username": username}


@router.post("/users/change-password")
async def change_admin_password(
    payload: ChangePasswordRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Change password for current logged-in user."""
    user_info = verify_admin_token(x_admin_token, authorization)
    username = user_info.get("sub", "admin")

    pool = await get_pool()
    async with pool.acquire() as conn:
        user_row = await conn.fetchrow(
            "SELECT id, password_hash FROM admin_users WHERE username = $1",
            username,
        )
        new_hash = hash_password(payload.new_password)

        if user_row:
            if not verify_password(payload.old_password, user_row["password_hash"]):
                raise HTTPException(status_code=400, detail="Old password is incorrect")
            await conn.execute("UPDATE admin_users SET password_hash = $1 WHERE id = $2", new_hash, user_row["id"])
        else:
            # If default admin user changing password, insert into admin_users table
            if not settings.admin_token or payload.old_password != settings.admin_token:
                raise HTTPException(status_code=400, detail="Old password is incorrect")
            await conn.execute(
                "INSERT INTO admin_users (username, password_hash) VALUES ($1, $2)",
                username,
                new_hash,
            )

    return {"status": "success", "message": f"Password for user '{username}' updated successfully"}


# ── LLM Connection Health Check ─────────────────────────────────────

@router.get("/llm-health")
async def check_llm_connections(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """
    Test live connectivity & latency for all LLM / AI Studio services:
      1. Groq API (openai/gpt-oss-120b)
      2. Gemini Embedding 2 (gemini-embedding-2)
      3. Gemma Reranker (gemma-4-26b-a4b-it)
    """
    verify_admin_token(x_admin_token, authorization)

    results = []

    # 1. Test Groq API (openai/gpt-oss-120b)
    t0 = time.perf_counter()
    try:
        client = AsyncGroq(api_key=settings.groq_api_key)
        # Quick 5-token ping
        res = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": "Ping"}],
            max_tokens=5,
        )
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append({
            "service": "Groq Generation & Query Rewrite",
            "model": settings.groq_model,
            "status": "HEALTHY",
            "latencyMs": latency_ms,
            "sample": res.choices[0].message.content.strip() if res.choices else "OK",
        })
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append({
            "service": "Groq Generation & Query Rewrite",
            "model": settings.groq_model,
            "status": "ERROR",
            "latencyMs": latency_ms,
            "error": str(e),
        })

    # 2. Test Gemini Embedding 2
    t0 = time.perf_counter()
    try:
        from services.retrieval import embed_query
        vec = await embed_query("Health test query")
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append({
            "service": "Google AI Studio Embedding",
            "model": settings.embedding_model,
            "dimensions": len(vec) if vec else 0,
            "status": "HEALTHY" if len(vec) == 768 else "WARN",
            "latencyMs": latency_ms,
        })
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append({
            "service": "Google AI Studio Embedding",
            "model": settings.embedding_model,
            "status": "ERROR",
            "latencyMs": latency_ms,
            "error": str(e),
        })

    # 3. Test Gemma Reranker
    t0 = time.perf_counter()
    try:
        from services.rerank import rerank_chunks
        test_chunks = [
            {"id": "1", "section": "Test A", "content": "Arifian adalah AI Developer."},
            {"id": "2", "section": "Test B", "content": "Kucing adalah hewan peliharaan."}
        ]
        reranked = await rerank_chunks("Siapa Arifian?", test_chunks, top_k=2)
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append({
            "service": "Google AI Studio LLM Reranker",
            "model": settings.reranker_model,
            "status": "HEALTHY",
            "latencyMs": latency_ms,
            "topResult": reranked[0]["section"] if reranked else "-",
        })
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        results.append({
            "service": "Google AI Studio LLM Reranker",
            "model": settings.reranker_model,
            "status": "ERROR",
            "latencyMs": latency_ms,
            "error": str(e),
        })

    return {"status": "complete", "timestamp": time.time(), "services": results}


# ── System Health UI Status ─────────────────────────────────────────

@router.get("/system-health")
async def check_system_health(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Detailed health check for Database, Cloudflare R2, and APIs."""
    verify_admin_token(x_admin_token, authorization)

    health_info = {}

    # Database
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            val = await conn.fetchval("SELECT 1")
            health_info["database"] = {"status": "CONNECTED" if val == 1 else "UNHEALTHY", "engine": "PostgreSQL pgvector (Supabase Pooler)"}
    except Exception as e:
        health_info["database"] = {"status": "ERROR", "error": str(e)}

    # R2 Storage
    try:
        from utils.r2 import get_r2_client
        s3 = get_r2_client()
        res = s3.list_buckets()
        health_info["r2Storage"] = {"status": "CONNECTED", "bucket": settings.r2_bucket}
    except Exception as e:
        health_info["r2Storage"] = {"status": "WARN / OPTIONAL", "info": str(e)}

    # Telegram Outbound
    health_info["telegramBridge"] = {
        "status": "CONFIGURED" if settings.telegram_bot_token else "NOT_SET",
        "botTokenMasked": settings.telegram_bot_token[:10] + "..." if settings.telegram_bot_token else "None",
        "chatId": settings.owner_chat_id or "Not configured",
    }

    return {"status": "healthy", "components": health_info}


@router.get("", response_class=FileResponse)
@router.get("/", response_class=FileResponse)
@router.get("/dashboard", response_class=FileResponse)
async def serve_admin_dashboard():
    """Serve the single page Admin Dashboard GUI."""
    admin_html_path = Path(__file__).resolve().parent.parent / "static" / "admin.html"
    return FileResponse(admin_html_path)


# ── Knowledge Base Endpoints ────────────────────────────────────────

@router.post("/ingest-text")
async def ingest_text_string(
    payload: IngestTextRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Ingest raw text string directly into Knowledge Base."""
    verify_admin_token(x_admin_token, authorization)

    title = payload.title.strip()
    content = payload.content.strip()
    if not title or not content:
        raise HTTPException(status_code=400, detail="Title and content cannot be empty")

    chunks = chunk_markdown(content)
    if not chunks:
        raise HTTPException(status_code=400, detail="Chunking yielded 0 chunks")

    pool = await get_pool()
    async with pool.acquire() as conn:
        doc_id = await conn.fetchval(
            """
            INSERT INTO documents (title, source_type, metadata)
            VALUES ($1, $2, $3::jsonb)
            RETURNING id
            """,
            title,
            payload.source_type,
            json.dumps(payload.metadata),
        )

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

    return {
        "status": "success",
        "document_id": str(doc_id),
        "title": title,
        "chunks_created": chunks_created,
    }


@router.get("/documents")
async def list_documents(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """List all Knowledge Base documents with metadata and chunk counts."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.id, d.title, d.source_type, d.r2_key, d.metadata, d.created_at,
                   count(c.id) as chunk_count
            FROM documents d
            LEFT JOIN chunks c ON c.document_id = d.id
            GROUP BY d.id
            ORDER BY d.created_at DESC
            """
        )

    return [
        {
            "id": str(r["id"]),
            "title": r["title"],
            "sourceType": r["source_type"] or "manual",
            "r2Key": r["r2_key"],
            "metadata": r["metadata"],
            "chunkCount": r["chunk_count"],
            "createdAt": r["created_at"].isoformat() if r["created_at"] else "",
        }
        for r in rows
    ]


@router.get("/documents/{doc_id}")
async def get_document_details(
    doc_id: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get document details and all associated vector chunks."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        doc = await conn.fetchrow("SELECT * FROM documents WHERE id = $1::uuid", doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")

        chunks = await conn.fetch(
            "SELECT id, section, content, token_count, created_at FROM chunks WHERE document_id = $1::uuid ORDER BY created_at ASC",
            doc_id,
        )

    return {
        "id": str(doc["id"]),
        "title": doc["title"],
        "sourceType": doc["source_type"],
        "r2Key": doc["r2_key"],
        "metadata": doc["metadata"],
        "createdAt": doc["created_at"].isoformat() if doc["created_at"] else "",
        "chunks": [
            {
                "id": str(c["id"]),
                "section": c["section"],
                "content": c["content"],
                "tokenCount": c["token_count"],
            }
            for c in chunks
        ],
    }


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Delete a document and cascade delete all its vector chunks."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        deleted_id = await conn.fetchval(
            "DELETE FROM documents WHERE id = $1::uuid RETURNING id",
            doc_id,
        )

    if not deleted_id:
        raise HTTPException(status_code=404, detail="Document not found")

    return {"status": "success", "deleted_id": str(deleted_id)}


@router.post("/upload")
async def upload_file_to_kb(
    file: UploadFile = File(...),
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Upload a file (PDF, DOCX, CSV, MD) to ingest into Knowledge Base and R2."""
    verify_admin_token(x_admin_token, authorization)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename missing")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="File is empty")

    try:
        result = await ingest_file_document(
            filename=file.filename,
            file_bytes=file_bytes,
            source_type="manual",
        )
        return result
    except Exception as e:
        logger.error(f"Upload ingest failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest-repo", response_model=IngestRepoResponse)
async def ingest_repo_to_kb(
    request: IngestRepoRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Ingest a GitHub repository into Knowledge Base."""
    verify_admin_token(x_admin_token, authorization)

    try:
        result = await ingest_github_repo(request.url)
        return result
    except Exception as e:
        logger.error(f"Repo ingest failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Project Request Leads Endpoints ─────────────────────────────────

@router.get("/leads")
async def list_leads(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """List all submitted project request leads."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, service, description, budget, deadline, contact, status, created_at FROM leads ORDER BY created_at DESC"
        )

    return [
        {
            "id": str(r["id"]),
            "service": r["service"],
            "description": r["description"],
            "budget": r["budget"],
            "deadline": r["deadline"],
            "contact": r["contact"],
            "status": r["status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else "",
        }
        for r in rows
    ]


@router.get("/leads/{lead_id}")
async def get_lead(
    lead_id: str,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get detail of a specific project lead."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, service, description, budget, deadline, contact, status, raw, created_at FROM leads WHERE id = $1::uuid",
            lead_id,
        )

    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    return {
        "id": str(row["id"]),
        "service": row["service"],
        "description": row["description"],
        "budget": row["budget"],
        "deadline": row["deadline"],
        "contact": row["contact"],
        "status": row["status"],
        "raw": row["raw"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else "",
    }


class UpdateLeadStatusRequest(BaseModel):
    status: str  # 'new' | 'contacted' | 'won' | 'lost'


@router.patch("/leads/{lead_id}")
async def update_lead_status(
    lead_id: str,
    payload: UpdateLeadStatusRequest,
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Update status of a lead."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = await conn.fetchval(
            "UPDATE leads SET status = $1 WHERE id = $2::uuid RETURNING id",
            payload.status,
            lead_id,
        )

    if not updated:
        raise HTTPException(status_code=404, detail="Lead not found")

    return {"status": "success", "id": lead_id, "new_status": payload.status}


@router.get("/stats")
async def get_system_stats(
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Get counts of documents, chunks, and leads for dashboard overview."""
    verify_admin_token(x_admin_token, authorization)

    pool = await get_pool()
    async with pool.acquire() as conn:
        doc_count = await conn.fetchval("SELECT count(*) FROM documents")
        chunk_count = await conn.fetchval("SELECT count(*) FROM chunks")
        lead_count = await conn.fetchval("SELECT count(*) FROM leads")

    return {
        "documents": doc_count,
        "chunks": chunk_count,
        "leads": lead_count,
    }
