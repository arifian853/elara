"""
utils/chunking.py — Markdown-aware semantic chunking.

Splits markdown text by headings (##, ###, etc.) into chunks.
Each chunk ≤ CHUNK_SIZE tokens with CHUNK_OVERLAP overlap.
Token estimation: 1 token ≈ 4 chars (conservative for multilingual).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from config import settings


@dataclass
class Chunk:
    """A single text chunk with metadata."""
    content: str
    section: str       # heading that this chunk belongs to
    token_count: int   # estimated token count


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token (conservative for mixed ID/EN)."""
    return max(1, len(text) // 4)


def _split_by_headings(markdown: str) -> list[tuple[str, str]]:
    """
    Split markdown into (section_title, body) tuples by heading lines.

    A heading is any line starting with 1-6 '#' characters.
    Text before the first heading gets section title "intro".
    """
    # Pattern: one or more # at start of line, followed by space and title text
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

    sections: list[tuple[str, str]] = []
    last_end = 0
    last_title = "intro"

    for match in heading_pattern.finditer(markdown):
        # Collect text between previous heading and this one
        body = markdown[last_end:match.start()].strip()
        if body:
            sections.append((last_title, body))

        last_title = match.group(2).strip()
        last_end = match.end()

    # Remaining text after last heading
    remaining = markdown[last_end:].strip()
    if remaining:
        sections.append((last_title, remaining))

    return sections


def _split_long_text(text: str, max_tokens: int, overlap_ratio: float) -> list[str]:
    """
    Split a long text block into overlapping sub-chunks by paragraphs/sentences.

    Strategy:
    1. Split by double newlines (paragraphs) first.
    2. If a single paragraph is still too long, split by sentences.
    3. Accumulate paragraphs until reaching max_tokens, then start new chunk
       with overlap_ratio worth of previous content.
    """
    overlap_tokens = int(max_tokens * overlap_ratio)
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    # Further split any paragraph that exceeds max_tokens by sentences
    units: list[str] = []
    for para in paragraphs:
        if estimate_tokens(para) > max_tokens:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            units.extend(sentences)
        else:
            units.append(para)

    chunks: list[str] = []
    current_parts: list[str] = []
    current_tokens = 0

    for unit in units:
        unit_tokens = estimate_tokens(unit)

        if current_tokens + unit_tokens > max_tokens and current_parts:
            # Flush current chunk
            chunks.append("\n\n".join(current_parts))

            # Build overlap from tail of current_parts
            overlap_parts: list[str] = []
            overlap_count = 0
            for part in reversed(current_parts):
                part_tokens = estimate_tokens(part)
                if overlap_count + part_tokens > overlap_tokens:
                    break
                overlap_parts.insert(0, part)
                overlap_count += part_tokens

            current_parts = overlap_parts
            current_tokens = overlap_count

        current_parts.append(unit)
        current_tokens += unit_tokens

    # Flush last chunk
    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def chunk_markdown(
    markdown: str,
    max_tokens: int | None = None,
    overlap_ratio: float | None = None,
) -> list[Chunk]:
    """
    Chunk markdown text into sections, respecting heading boundaries.

    Args:
        markdown: Raw markdown text.
        max_tokens: Max tokens per chunk (default from settings.chunk_size).
        overlap_ratio: Overlap ratio between chunks (default from settings.chunk_overlap).

    Returns:
        List of Chunk objects ready for embedding.
    """
    if max_tokens is None:
        max_tokens = settings.chunk_size
    if overlap_ratio is None:
        overlap_ratio = settings.chunk_overlap

    sections = _split_by_headings(markdown)
    chunks: list[Chunk] = []

    for section_title, body in sections:
        body_tokens = estimate_tokens(body)

        if body_tokens <= max_tokens:
            # Section fits in one chunk
            chunks.append(Chunk(
                content=body,
                section=section_title,
                token_count=body_tokens,
            ))
        else:
            # Section too long — split into sub-chunks with overlap
            sub_texts = _split_long_text(body, max_tokens, overlap_ratio)
            for sub in sub_texts:
                chunks.append(Chunk(
                    content=sub,
                    section=section_title,
                    token_count=estimate_tokens(sub),
                ))

    return chunks
