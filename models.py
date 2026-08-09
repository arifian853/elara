"""
models.py — Pydantic request / response schemas.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


# ── Chat ────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str = Field(..., max_length=2000)
    session_id: str = Field(default="")
    stream: bool = Field(default=False)
    history: list[dict] = Field(default_factory=list)


class SourceItem(BaseModel):
    title: str
    score: float


class ChatResponse(BaseModel):
    mode: str  # "rag" | "intake"
    response: str
    sources: list[SourceItem] = Field(default_factory=list)
    # intake-specific (optional)
    step: int | None = None
    chips: list[str] | None = None


# ── Health ──────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str  # "healthy" | "unhealthy"
    database: str  # "connected" | "disconnected"


# ── Admin: Repo Ingest ──────────────────────────────────────────────

class IngestRepoRequest(BaseModel):
    url: str = Field(..., description="GitHub repository URL")


class IngestRepoResponse(BaseModel):
    status: str
    document_id: str
    title: str
    chunks_created: int
    metadata: dict = Field(default_factory=dict)


# ── Confessions / Anonymous Messages (M5.5) ─────────────────────────

class ConfessionSubmitRequest(BaseModel):
    message: str = Field(..., max_length=500, description="Pesan anonim dari pengunjung")


class ConfessionReplyRequest(BaseModel):
    reply: str = Field(..., description="Balasan publik dari admin")


# ── System Prompts (M5.5) ───────────────────────────────────────────

class SystemPromptRequest(BaseModel):
    name: str = Field(..., description="Nama system prompt")
    prompt: str = Field(..., description="Isi prompt sistem")
    description: str = Field(default="", description="Deskripsi singkat")
    is_active: bool = Field(default=False, description="Apakah prompt ini aktif")


class SystemPromptUpdateRequest(BaseModel):
    name: str | None = None
    prompt: str | None = None
    description: str | None = None
    is_active: bool | None = None


# ── Knowledge Base / Ingest Text (M5) ────────────────────────────────

class IngestTextRequest(BaseModel):
    title: str = Field(..., description="Judul dokumen / knowledge item")
    content: str = Field(..., description="Teks mentah untuk di-ingest & embed")
    source_type: str = Field(default="manual", description="pdf | docx | md | csv | github | manual")
    metadata: dict = Field(default_factory=dict)


# ── Admin Auth (JWT Login) ──────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    username: str = Field(..., description="Username admin")
    password: str = Field(..., description="Password admin")


class AdminLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=3, description="Username admin baru")
    password: str = Field(..., min_length=6, description="Password admin baru")


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., description="Password lama")
    new_password: str = Field(..., min_length=6, description="Password baru")



