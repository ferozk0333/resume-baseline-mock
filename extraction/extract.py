"""One structured LLM call per evidence item (specs/05 §A).

Document text goes only into the user message, inside delimiter tags; the
system prompt holds every instruction (CLAUDE.md rule 9).
"""
from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from core import config, timing
from core.llm import LLMError, StructuredLLM
from core.text import wrap_tag
from extraction import schemas
from extraction.validate import SourceDoc

DOC_TAGS = {"resume": "resume", "linkedin_pdf": "linkedin_pdf", "portfolio": "portfolio", "github_repo": "readme"}
DOC_NAMES = {"resume": "resume", "linkedin_pdf": "LinkedIn profile export", "portfolio": "portfolio page",
             "github_repo": "GitHub repository README"}


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    return (config.PROMPTS_DIR / f"{name}.txt").read_text().strip()


wrap_document = wrap_tag


def output_type(doc: SourceDoc) -> type:
    return schemas.ProfileExtract if doc.type in ("resume", "linkedin_pdf") else schemas.PortfolioExtract


def build_user_message(doc: SourceDoc) -> str:
    tag = DOC_TAGS[doc.type]
    return f"Extract the facts from this {DOC_NAMES[doc.type]}.\n\n{wrap_document(tag, doc.text)}"


def extract_document(doc: SourceDoc, llm: StructuredLLM, model: str | None = None):
    return llm.parse(
        model=model or config.EXTRACT_MODEL,
        system=load_prompt("extract_system"),
        user=build_user_message(doc),
        output_type=output_type(doc),
    )


class ExtractionCache:
    """Saved extraction outputs keyed by exactly what the model would see (cost: a re-upload of the same
    documents makes no extraction call). Key = model + system prompt + output schema + user message."""

    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.Lock()

    @staticmethod
    def key(doc: SourceDoc, model: str) -> str:
        schema = json.dumps(output_type(doc).model_json_schema(), sort_keys=True)
        raw = "\n\x00".join([model, load_prompt("extract_system"), schema, build_user_message(doc)])
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, doc: SourceDoc, model: str):
        with self._lock:
            row = self.conn.execute("SELECT output_json FROM extraction_cache WHERE key = ?",
                                    (self.key(doc, model),)).fetchone()
        return output_type(doc).model_validate_json(row[0]) if row else None

    def put(self, doc: SourceDoc, model: str, out) -> None:
        from core.db import now_iso
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO extraction_cache (key, model, output_json, created_at) "
                              "VALUES (?, ?, ?, ?)", (self.key(doc, model), model, out.model_dump_json(), now_iso()))


def extract_all(docs: list[SourceDoc], llm: StructuredLLM, cache: ExtractionCache | None = None) -> dict[str, object]:
    """evidence_id → extraction, or the LLMError that stopped it. Calls run in parallel."""

    def run(doc: SourceDoc):
        model = config.EXTRACT_MODEL
        with timing.span(timing.EXTRACT, doc.type, model=model, chars=len(doc.text)) as t:
            hit = cache.get(doc, model) if cache is not None else None
            if hit is not None:
                t["cached"] = True
                return doc.evidence_id, hit
            try:
                out = extract_document(doc, llm)
            except LLMError as exc:
                return doc.evidence_id, exc
            if cache is not None:
                cache.put(doc, model, out)
            return doc.evidence_id, out

    with ThreadPoolExecutor(max_workers=config.EXTRACT_CONCURRENCY) as pool:
        return dict(pool.map(timing.bind(run), docs))
