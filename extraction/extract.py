"""One structured LLM call per evidence item (specs/05 §A).

Document text goes only into the user message, inside delimiter tags; the
system prompt holds every instruction (CLAUDE.md rule 9).
"""
from __future__ import annotations

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


def extract_all(docs: list[SourceDoc], llm: StructuredLLM) -> dict[str, object]:
    """evidence_id → extraction, or the LLMError that stopped it. Calls run in parallel."""

    def run(doc: SourceDoc):
        with timing.span(timing.EXTRACT, doc.type, model=config.EXTRACT_MODEL, chars=len(doc.text)):
            try:
                return doc.evidence_id, extract_document(doc, llm)
            except LLMError as exc:
                return doc.evidence_id, exc

    with ThreadPoolExecutor(max_workers=config.EXTRACT_CONCURRENCY) as pool:
        return dict(pool.map(timing.bind(run), docs))
