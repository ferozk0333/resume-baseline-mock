"""PDF / DOCX → normalized Markdown text. No OCR: image-only documents are reported as unreadable.

Every upload goes through one normalization step before any model call (iteration 2):
1. Layout-aware parse to Markdown: pymupdf4llm for PDF (reading order, columns, headings,
   bullets); python-docx for DOCX (document order, heading and list styles, tables).
   pymupdf4llm runs in its classic mode: its ONNX "layout" mode turned employer and title
   lines of a tab-aligned resume into bullets (compared in phase 9).
2. Clean-up: inline emphasis and HTML entities removed (they break quote matching), links
   flattened to "text (url)", runs of tabs/spaces collapsed, lines trimmed, a bullet's
   wrapped lines joined, line breaks between bullets kept.
The result is stored per file hash and is exactly the text used for extraction, the quote
check and the review step's source view.

PyMuPDF / pymupdf4llm are AGPL-3.0 (see README).
"""
from __future__ import annotations

import hashlib
import html
import io
import re
from pathlib import Path

import docx
import pymupdf
import pymupdf4llm

from core import config

# Stored with the normalized text; bump when parsing or clean-up changes so old cache rows are not reused.
NORMALIZER = f"pymupdf4llm-{pymupdf4llm.__version__}-classic+docx-md+clean-1"
pymupdf4llm.use_layout(False)


class DocumentError(Exception):
    """User-facing problem with an uploaded document."""


UNREADABLE_MESSAGE = (
    "We could not read text from this file (it may be a scanned image). "
    "Upload a text-based PDF or DOCX, or add your roles and projects manually in the review step."
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pdf_markdown(data: bytes) -> str:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        if doc.needs_pass and not doc.authenticate(""):
            raise DocumentError("This PDF is password-protected.")
        return pymupdf4llm.to_markdown(doc, show_progress=False)
    except DocumentError:
        raise
    except Exception as exc:  # PyMuPDF raises its own and generic errors for damaged files
        raise DocumentError(f"This PDF could not be opened ({exc.__class__.__name__}).") from exc


def _docx_paragraph(p) -> str:
    text = p.text.strip()
    if not text:
        return ""
    style = (p.style.name if p.style is not None else "") or ""
    if style == "Title":
        return f"# {text}"
    m = re.match(r"Heading (\d)", style)
    if m:
        return f"{'#' * min(int(m.group(1)) + 1, 6)} {text}"
    numbered = p._p.pPr is not None and p._p.pPr.numPr is not None
    if style.startswith("List") or numbered:
        return f"- {text}"
    return text


def _docx_markdown(data: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:  # python-docx raises several unrelated types for bad files
        raise DocumentError("This DOCX file could not be opened.") from exc
    parts = []
    for block in document.iter_inner_content():          # paragraphs and tables in document order
        if isinstance(block, docx.table.Table):
            for row in block.rows:
                cells = [c.text.strip() for c in row.cells]
                parts.append(" | ".join(dict.fromkeys(c for c in cells if c)))   # merged cells repeat
        else:
            parts.append(_docx_paragraph(block))
    return "\n\n".join(p for p in parts if p)


_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]+)\)")


def _flatten_link(m: re.Match) -> str:
    text, url = m.group(1).strip(), m.group(2)
    bare = re.sub(r"^(https?://)?(www\.)?", "", url).rstrip("/")
    if url.startswith("mailto:") or not text or bare.split("?")[0] in text or text in url:
        return text or bare
    return f"{text} ({url})"


_EMPHASIS = re.compile(r"\*\*|__|(?<![\w])_(?=\S)|(?<=\S)_(?![\w])|</?(?:u|b|i|em|strong|sup|sub)>")


def clean_markdown(md: str) -> str:
    """Keep structure (headings, bullets, line breaks); drop what breaks quote matching."""
    md = html.unescape(html.unescape(md))
    md = md.replace("<br>", "\n")
    lines: list[str] = []
    for line in md.splitlines():
        if "intentionally omitted" in line:               # pymupdf4llm image placeholders
            continue
        line = _LINK.sub(_flatten_link, line)
        line = _EMPHASIS.sub("", line)
        # Tabs, odd spaces and U+FFFD (a tab or icon with no glyph in the PDF font) become one space.
        line = re.sub(r"[\t\u00a0\u2002-\u200a\u202f\ufffd]+", " ", line)
        line = re.sub(r" {2,}", " ", line).strip()
        line = re.sub(r"^[•▪◦●‣■]\s+", "- ", line)              # bullet glyphs → Markdown bullets
        line = re.sub(r"^#+\s*$", "", line)                 # heading marker left empty
        # A bullet wrapped onto the next line: the bullet has not ended and the line continues it in
        # lower case. Anything else (e.g. the next role's header) stays on its own line.
        if (line and lines and lines[-1].startswith("- ") and not lines[-1].endswith((".", "!", "?"))
                and line[0].islower()):
            lines[-1] += " " + line
            continue
        lines.append(line)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def normalize_document(filename: str, data: bytes) -> str:
    """Parse and clean one upload. Raises DocumentError with a user-facing message."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        md = _pdf_markdown(data)
    elif suffix == ".docx":
        md = _docx_markdown(data)
    else:
        raise DocumentError("Upload a PDF or DOCX file.")
    text = clean_markdown(md)
    if len("".join(re.sub(r"[#*\-|]", "", text).split())) < config.MIN_DOCUMENT_CHARS:
        raise DocumentError(UNREADABLE_MESSAGE)
    return text


def extract_text(filename: str, data: bytes, conn=None) -> str:
    """Normalized text of a document, reused by file hash when `conn` is given (stored in document_texts)."""
    if conn is None:
        return normalize_document(filename, data)
    digest = sha256(data)
    row = conn.execute("SELECT text FROM document_texts WHERE file_sha256 = ? AND normalizer = ?",
                       (digest, NORMALIZER)).fetchone()
    if row is not None:
        return row[0]
    text = normalize_document(filename, data)
    from core.db import now_iso
    with conn:
        conn.execute("INSERT OR REPLACE INTO document_texts (file_sha256, normalizer, text, created_at) "
                     "VALUES (?, ?, ?, ?)", (digest, NORMALIZER, text, now_iso()))
    return text


def save_upload(candidate_id: str, filename: str, data: bytes) -> Path:
    """Keep the original upload so extraction can be re-run later."""
    folder = config.UPLOAD_DIR / candidate_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256(data)[:16]}{Path(filename).suffix.lower()}"
    path.write_bytes(data)
    return path
