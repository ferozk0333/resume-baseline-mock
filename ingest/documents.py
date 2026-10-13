"""PDF / DOCX → plain text. No OCR: image-only documents are reported as unreadable."""
from __future__ import annotations

import hashlib
import io
from pathlib import Path

import docx
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from core import config


class DocumentError(Exception):
    """User-facing problem with an uploaded document."""


UNREADABLE_MESSAGE = (
    "We could not read text from this file (it may be a scanned image). "
    "Upload a text-based PDF or DOCX, or add your roles and projects manually in the review step."
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except (PdfReadError, ValueError, KeyError) as exc:
        raise DocumentError(f"This PDF could not be opened ({exc.__class__.__name__}).") from exc


def _docx_text(data: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types for bad files
        raise DocumentError("This DOCX file could not be opened.") from exc
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def extract_text(filename: str, data: bytes) -> str:
    """Return the document's text, or raise DocumentError with a user-facing message."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        text = _pdf_text(data)
    elif suffix == ".docx":
        text = _docx_text(data)
    else:
        raise DocumentError("Upload a PDF or DOCX file.")
    if len("".join(text.split())) < config.MIN_DOCUMENT_CHARS:
        raise DocumentError(UNREADABLE_MESSAGE)
    return text


def save_upload(candidate_id: str, filename: str, data: bytes) -> Path:
    """Keep the original upload so extraction can be re-run later."""
    folder = config.UPLOAD_DIR / candidate_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256(data)[:16]}{Path(filename).suffix.lower()}"
    path.write_bytes(data)
    return path
