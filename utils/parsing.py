"""
utils/parsing.py — Parser for PDF, DOCX, CSV, and Markdown/text files.
"""

from __future__ import annotations

import csv
import io
import logging
from pypdf import PdfReader
from docx import Document

logger = logging.getLogger(__name__)


def parse_file_content(file_bytes: bytes, filename: str) -> str:
    """
    Parse text content from raw file bytes based on file extension.

    Supports: .pdf, .docx, .csv, .md, .txt
    """
    ext = filename.lower().split(".")[-1] if "." in filename else ""

    if ext == "pdf":
        return _parse_pdf(file_bytes)
    elif ext == "docx":
        return _parse_docx(file_bytes)
    elif ext == "csv":
        return _parse_csv(file_bytes)
    elif ext in ("md", "markdown", "txt"):
        return file_bytes.decode("utf-8", errors="replace")
    else:
        # Fallback decode text
        return file_bytes.decode("utf-8", errors="replace")


def _parse_pdf(file_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(file_bytes))
    text_parts = []
    for i, page in enumerate(reader.pages):
        page_text = page.extract_text() or ""
        if page_text.strip():
            text_parts.append(f"## Page {i + 1}\n{page_text.strip()}")
    return "\n\n".join(text_parts)


def _parse_docx(file_bytes: bytes) -> str:
    doc = Document(io.BytesIO(file_bytes))
    text_parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            text_parts.append(para.text.strip())
    return "\n\n".join(text_parts)


def _parse_csv(file_bytes: bytes) -> str:
    stream = io.StringIO(file_bytes.decode("utf-8", errors="replace"))
    reader = csv.reader(stream)
    rows = list(reader)
    if not rows:
        return ""

    header = rows[0]
    lines = [f"Header: {', '.join(header)}"]
    for row in rows[1:]:
        if any(row):
            lines.append(", ".join(row))
    return "\n".join(lines)
