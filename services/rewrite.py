"""
services/rewrite.py — Query rewrite using conversation history.

Resolves pronouns and references ("dia", "itu", "yang tadi")
into a self-contained query using Groq (1 fast LLM call).
"""

from __future__ import annotations

from groq import AsyncGroq

from config import settings


_groq_client: AsyncGroq | None = None


def _get_groq_client() -> AsyncGroq:
    global _groq_client
    if _groq_client is None:
        _groq_client = AsyncGroq(api_key=settings.groq_api_key)
    return _groq_client


REWRITE_SYSTEM = (
    "Kamu adalah query rewriter. Tugasmu menerjemahkan pesan user "
    "menjadi query pencarian mandiri (self-contained) berdasarkan "
    "riwayat percakapan. Hilangkan kata ganti rujukan seperti "
    "'dia', 'itu', 'yang tadi', 'ini' — ganti dengan subjek aslinya. "
    "Jika pesan sudah jelas tanpa konteks, kembalikan apa adanya. "
    "Balas HANYA dengan query yang sudah ditulis ulang, tanpa penjelasan."
)


async def rewrite_query(message: str, history: list[dict]) -> str:
    """
    Rewrite the user's message using conversation history for context.

    If history is empty or message is already self-contained,
    returns the original message unchanged.

    Args:
        message: Current user message.
        history: List of {"role": "user"|"assistant", "content": "..."}.

    Returns:
        Rewritten query string.
    """
    # Skip rewrite if no history — nothing to resolve
    if not history:
        return message

    client = _get_groq_client()

    # Build messages for the rewrite call
    messages = [{"role": "system", "content": REWRITE_SYSTEM}]

    # Include last 4 turns of history for context
    recent = history[-4:]
    for turn in recent:
        role = "assistant" if turn.get("role") in ("assistant", "model") else "user"
        messages.append({
            "role": role,
            "content": turn.get("content", ""),
        })

    messages.append({
        "role": "user",
        "content": f"Tulis ulang query ini: {message}",
    })

    try:
        response = await client.chat.completions.create(
            model=settings.groq_model,
            messages=messages,
            temperature=0.1,
            max_tokens=200,
        )
        rewritten = response.choices[0].message.content.strip()
        return rewritten if rewritten else message
    except Exception:
        # On any error, fall back to original message
        return message
