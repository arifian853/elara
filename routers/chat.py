"""
routers/chat.py — POST /chat endpoint.

Routes messages to either:
  - RAG pipeline (default)
  - Intake state machine (when session is in intake mode)

Supports SSE streaming via `stream: true`.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.responses import StreamingResponse

from models import ChatRequest, ChatResponse, SourceItem
from services.rag import run_rag_pipeline, RAGResult
from services.intake import get_intake_session, process_intake_step

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Main chat endpoint.

    Routes to intake state machine if session is active,
    otherwise runs the RAG pipeline.
    """
    message = request.message.strip()
    session_id = request.session_id or "anonymous"

    # ── Check for active intake session ─────────────────────────────
    intake_session = await get_intake_session(session_id)

    if intake_session is not None:
        # Active intake session → process next step
        result = await process_intake_step(session_id, message, intake_session)
        return ChatResponse(
            mode="intake",
            response=result["response"],
            step=result.get("step"),
            chips=result.get("chips"),
        )

    # ── Check for intake trigger phrases ────────────────────────────
    if _is_intake_trigger(message):
        from services.intake import start_intake_session
        result = await start_intake_session(session_id)
        return ChatResponse(
            mode="intake",
            response=result["response"],
            step=result.get("step"),
            chips=result.get("chips"),
        )

    # ── RAG pipeline ────────────────────────────────────────────────
    if request.stream:
        return await _handle_stream(message, request.history)

    rag_result = await run_rag_pipeline(
        message=message,
        history=request.history,
        stream=False,
    )

    if isinstance(rag_result, RAGResult):
        return ChatResponse(
            mode=rag_result.mode,
            response=rag_result.response,
            sources=[
                SourceItem(title=s["title"], score=s["score"])
                for s in rag_result.sources
            ],
        )

    # Fallback
    return ChatResponse(mode="rag", response=str(rag_result))


async def _handle_stream(message: str, history: list[dict]):
    """Handle SSE streaming response."""
    generator = await run_rag_pipeline(
        message=message,
        history=history,
        stream=True,
    )

    async def sse_generator():
        try:
            async for chunk in generator:
                data = json.dumps({"content": chunk}, ensure_ascii=False)
                yield f"data: {data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            logger.error(f"SSE stream error: {e}")
            error_data = json.dumps({"error": str(e)}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _is_intake_trigger(message: str) -> bool:
    """Check if the message triggers the intake/project-request flow."""
    msg_lower = message.lower()
    triggers = [
        "/project-request",
        "/request",
        "mau bikin",
        "mau buat",
        "mau hire",
        "mau pesan",
        "berapa harga",
        "bisa bantu skripsi",
        "bisa bantu tugas",
        "butuh jasa",
        "order project",
        "request proyek",
        "request project",
        "ajak kerja sama",
    ]
    return any(t in msg_lower for t in triggers)
