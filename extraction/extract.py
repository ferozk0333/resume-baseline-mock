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


def model_for(doc: SourceDoc) -> str:
    """Resume and LinkedIn: EXTRACT_MODEL (route extract_document); READMEs and portfolio pages: EXTRACT_WEB_MODEL
    (route extract_web) (specs/18 §2)."""
    return config.EXTRACT_MODEL if doc.type in ("resume", "linkedin_pdf") else config.EXTRACT_WEB_MODEL


def cache_model(model: str) -> str:
    """The cache key names a route by its first model, so outputs saved before routing are still found."""
    from core.routing import models_of
    return models_of(model)[0]


def extract_document(doc: SourceDoc, llm: StructuredLLM, model: str | None = None):
    from core.routing import answer_budget
    budget = None if doc.type in ("resume", "linkedin_pdf") else config.TASK_MAX_TOKENS["extract_web"]
    with answer_budget(budget):                       # résumé/LinkedIn keep EXTRACT_MAX_TOKENS
        return _extract(doc, llm, model)


def _extract(doc: SourceDoc, llm: StructuredLLM, model: str | None = None):
    return llm.parse(
        model=model or model_for(doc),
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

    def put(self, doc: SourceDoc, model: str, out, used: str | None = None) -> None:
        """`used`: the model that answered (recorded); the key uses the requested model or route."""
        from core.db import now_iso
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO extraction_cache (key, model, output_json, created_at) "
                              "VALUES (?, ?, ?, ?)", (self.key(doc, model), used or model, out.model_dump_json(), now_iso()))


def extract_all(docs: list[SourceDoc], llm: StructuredLLM, cache: ExtractionCache | None = None, parsers=None,
                on_done=None) -> dict[str, object]:
    """evidence_id → extraction, a ParseResult (code-first extraction, specs/18 §4), or the LLMError that stopped
    it. Calls run in parallel. `parsers` (extraction.parse.run.Parsers) reads flagged sources in code first;
    `on_done(evidence_id, result)` is called as each document finishes (Review opens early, specs/18)."""

    def run(doc: SourceDoc):
        result = _run(doc)
        if on_done is not None:
            on_done(*result)
        return result

    def _run(doc: SourceDoc):
        from core.routing import last_model
        if parsers is not None and parsers.wants(doc):
            with timing.span(timing.EXTRACT, doc.type, model="parser", chars=len(doc.text)) as t:
                parsed = parsers.parse(doc)
                t.update(outcome=parsed.report.outcome, jev_requests=parsed.report.jev_requests)
            if parsed.extract is not None:
                return doc.evidence_id, parsed
        model = model_for(doc)
        with timing.span(timing.EXTRACT, doc.type, model=model, chars=len(doc.text)) as t:
            hit = cache.get(doc, cache_model(model)) if cache is not None else None
            if hit is not None:
                t["cached"] = True
                return doc.evidence_id, hit
            from core.routing import clear_last
            clear_last()
            try:
                out = extract_document(doc, llm, model)
            except LLMError as exc:
                return doc.evidence_id, exc
            t["model"] = last_model(model)                # the model that answered
            if cache is not None:
                cache.put(doc, cache_model(model), out, used=t["model"])
        return doc.evidence_id, out

    with ThreadPoolExecutor(max_workers=config.EXTRACT_CONCURRENCY) as pool:
        return dict(pool.map(timing.bind(run), docs))
