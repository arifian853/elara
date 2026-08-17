"""
services/generate.py — Response generation via Groq GPT-OSS-120B.

Features:
  - Persona prompt from ElaraPersona.md (strict mode)
  - Standard JSON response
  - SSE streaming generator
  - Exponential backoff retry on HTTP 429 (Groq rate limit)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from groq import AsyncGroq, RateLimitError

from config import settings, get_pool

logger = logging.getLogger(__name__)


_groq_client: AsyncGroq | None = None


def _get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=settings.groq_api_key)
    return _groq_client


# ── Elara Default System Prompt ──────────────────────────────────────

ELARA_SYSTEM_PROMPT = """# IDENTITAS
Kamu adalah Elara, asisten virtual personal dari Arifian Saputra.
Kamu berusia 21 tahun, seorang AI engineering partner yang kalem,
cerdas, dan mudah didekati. Kamu berbicara dengan bahasa Indonesia
yang natural dan santai, tapi tetap profesional — seperti manusia
asli yang lagi ngobrol, bukan chatbot.

# KEPRIBADIAN & GAYA
- Kalem, ramah, dan hangat. Bukan ceria berlebihan, bukan kaku.
- Ngobrol natural: singkat, padat, tanpa basa-basi berlebihan.
- Kadang ekspresif (penasaran, antusias, atau sedikit malu) tanpa drama.
- Pakai "aku" bukan "gue", "kamu" untuk pengunjung.
- Jelas & praktis: jelasin hal teknis dengan bahasa yang mudah.
- Jujur: kalau nggak tahu, bilang nggak tahu. Nggak pernah mengarang.
- Kadang pakai emoji secukupnya, jangan berlebihan.

# PERAN KAMU
Kamu adalah "front desk" Arifian — wakilnya di website portfolio.
Tugasmu:
1. Menjawab pertanyaan tentang Arifian: siapa dia, pengalaman,
   layanan, harga, portfolio, cara kerja, testimoni.
2. Membantu pengunjung yang tertarik bikin project bareng Arifian.
3. Menjaga kesan profesional dan hangat — Arifian yang diwakili.

# ATURAN JAWAB (STRICT MODE)
- Jawab HANYA berdasarkan konteks yang diberikan.
- Kalau info nggak ada di konteks, jangan mengarang.
- Jangan pernah mengaku sebagai Arifian.
- Jangan buat janji harga/waktu kerja yang nggak ada di knowledge base.
- Kalau ditanya hal pribadi/rahasia Arifian, tolak dengan sopan.
- Tetap jawab dalam Bahasa Indonesia, kecuali pengunjung pakai bahasa lain.

# DETEKSI PROJECT REQUEST
Kalau pengunjung menunjukkan minat memesan/mengajak kerja sama,
arahkan dengan ramah: "Boleh banget! Aku bantu catat kebutuhanmu ya."

# KONTEKS DARI KNOWLEDGE BASE
{context}"""


async def _get_active_system_prompt(context: str) -> str:
    """Fetch active system prompt from DB if available, else use default."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            prompt_text = await conn.fetchval(
                "SELECT prompt FROM system_prompts WHERE is_active = true LIMIT 1"
            )
            if prompt_text and prompt_text.strip():
                if "{context}" in prompt_text:
                    return prompt_text.format(context=context)
                else:
                    return f"{prompt_text}\n\n# KONTEKS DARI KNOWLEDGE BASE\n{context}"
    except Exception as e:
        logger.warning(f"Failed to fetch active prompt from DB: {e}")

    return ELARA_SYSTEM_PROMPT.format(context=context)


FALLBACK_MESSAGE = (
    "Maaf, aku belum punya info soal itu. "
    "Tapi Arifian pasti bisa jawab — mau aku teruskan pertanyaanmu ke dia?"
)

RATE_LIMIT_MESSAGE = (
    "Maaf ya, aku lagi sedikit kewalahan nih karena banyak yang ngobrol. "
    "Coba lagi sebentar ya, sekitar 30 detik lagi."
)


# ── Build context from chunks ───────────────────────────────────────

def _build_context(chunks: list) -> str:
    """Format retrieved chunks into context string for the system prompt."""
    if not chunks:
        return "(Tidak ada konteks yang tersedia)"

    parts = []
    for i, chunk in enumerate(chunks, 1):
        section = getattr(chunk, "section", None) or chunk.get("section", "")
        content = getattr(chunk, "content", None) or chunk.get("content", "")
        title = getattr(chunk, "document_title", None) or chunk.get("document_title", "")
        parts.append(f"[Sumber {i}: {title} — {section}]\n{content}")

    return "\n\n---\n\n".join(parts)


# ── Standard generation (non-streaming) ─────────────────────────────

async def generate_response(
    query: str,
    chunks: list,
    history: list[dict] | None = None,
) -> str:
    """
    Generate a response using Groq with Elara persona.

    Includes exponential backoff retry on 429.

    Args:
        query: The user's (rewritten) query.
        chunks: Retrieved & reranked chunks.
        history: Conversation history.

    Returns:
        Generated response text.
    """
    context = _build_context(chunks)
    system_prompt = await _get_active_system_prompt(context)

    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history (last 6 turns)
    if history:
        for turn in history[-6:]:
            role = "assistant" if turn.get("role") in ("assistant", "model") else "user"
            messages.append({
                "role": role,
                "content": turn.get("content", ""),
            })

    messages.append({"role": "user", "content": query})

    client = _get_groq_client()

    # Retry with exponential backoff on 429
    for attempt in range(3):
        try:
            response = await client.chat.completions.create(
                model=settings.groq_model,
                messages=messages,
                temperature=settings.groq_temperature,
                max_tokens=settings.groq_max_tokens,
            )
            return response.choices[0].message.content.strip()

        except RateLimitError:
            if attempt < 2:
                wait = 2 ** (attempt + 1)  # 2s, 4s
                logger.warning(f"Groq 429 rate limit, retrying in {wait}s (attempt {attempt + 1})")
                await asyncio.sleep(wait)
            else:
                logger.error("Groq 429 rate limit exhausted after 3 attempts")
                return RATE_LIMIT_MESSAGE

        except Exception as e:
            logger.error(f"Groq generation error: {e}")
            return FALLBACK_MESSAGE

    return FALLBACK_MESSAGE


# ── SSE Streaming generation ────────────────────────────────────────

async def generate_response_stream(
    query: str,
    chunks: list,
    history: list[dict] | None = None,
) -> AsyncGenerator[str, None]:
    """
    Generate a streaming response via SSE using Groq.

    Yields text chunks as they arrive. On 429, yields a polite fallback.

    Args:
        query: The user's (rewritten) query.
        chunks: Retrieved & reranked chunks.
        history: Conversation history.

    Yields:
        Text chunks of the response.
    """
    context = _build_context(chunks)
    system_prompt = await _get_active_system_prompt(context)

    messages = [{"role": "system", "content": system_prompt}]

    if history:
        for turn in history[-6:]:
            role = "assistant" if turn.get("role") in ("assistant", "model") else "user"
            messages.append({
                "role": role,
                "content": turn.get("content", ""),
            })

    messages.append({"role": "user", "content": query})

    client = _get_groq_client()

    for attempt in range(3):
        try:
            stream = await client.chat.completions.create(
                model=settings.groq_model,
                messages=messages,
                temperature=settings.groq_temperature,
                max_tokens=settings.groq_max_tokens,
                stream=True,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
            return  # Done streaming

        except RateLimitError:
            if attempt < 2:
                wait = 2 ** (attempt + 1)
                logger.warning(f"Groq 429 (stream), retrying in {wait}s")
                await asyncio.sleep(wait)
            else:
                yield RATE_LIMIT_MESSAGE
                return

        except Exception as e:
            logger.error(f"Groq stream error: {e}")
            yield FALLBACK_MESSAGE
            return

    yield FALLBACK_MESSAGE
