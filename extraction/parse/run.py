"""Which documents are parsed in code (flags), the shadow résumé parser, and the per-document log (parser_runs).

PARSE_LINKEDIN / PARSE_README: the parser's result is used when its gate passes; otherwise the document goes to
the LLM exactly as today and the fallback reason is logged. PARSE_RESUME=shadow: the résumé is still read by the
LLM; afterwards, in one background thread, the parser reads it too and its result is compared with the LLM's and
logged. Nothing from the shadow run is saved to the profile, shown or scored, and it never delays extraction.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from core import config, db
from core.db import new_id, now_iso
from extraction.parse.claims import vocabulary
from extraction.parse.common import ParseResult, Report
from extraction.parse.labeller import NoLabeller, default_labeller

log = logging.getLogger(__name__)

SHADOW = ThreadPoolExecutor(max_workers=1, thread_name_prefix="parse-shadow")
_SHADOW_JOBS: list[Future] = []


def wait_shadow(timeout: float = 60) -> None:
    """Tests and scripts: wait for queued shadow parses."""
    for job in list(_SHADOW_JOBS):
        job.result(timeout)
    _SHADOW_JOBS.clear()


class Parsers:
    def __init__(self, db_path: str | None = None, labeller=None, vocab=None, llm=None):
        self.db_path = db_path
        self.llm = llm                                  # the small claims call (shadow, PARSE_SHADOW_CALLS only)
        self.labeller = labeller if labeller is not None else default_labeller()
        self._vocab = vocab

    @property
    def vocab(self):
        if self._vocab is None:
            if self.db_path:
                conn = db.connect(self.db_path)
                try:
                    self._vocab = vocabulary(conn)
                finally:
                    conn.close()
            else:
                self._vocab = vocabulary()
        return self._vocab

    @staticmethod
    def any_on() -> bool:
        return config.PARSE_LINKEDIN or config.PARSE_README or config.PARSE_RESUME == "shadow"

    def wants(self, doc) -> bool:
        if doc.type == "linkedin_pdf":
            return config.PARSE_LINKEDIN and bool(doc.file_path) and doc.file_path.lower().endswith(".pdf")
        if doc.type == "github_repo":
            return config.PARSE_README and doc.repo is not None and bool(doc.repo.readme)
        return False

    def parse(self, doc) -> ParseResult:
        try:
            if doc.type == "linkedin_pdf":
                from extraction.parse.linkedin import parse_linkedin
                res = parse_linkedin(Path(doc.file_path).read_bytes(), doc.text, self.vocab, self.labeller)
            else:
                from extraction.parse.readme import parse_readme
                res = parse_readme(doc.repo, self.vocab, self.labeller)
        except Exception as exc:                       # a parser bug never stops extraction: the LLM reads it
            log.exception("parser failed for %s", doc.type)
            report = Report(doc.type)
            report.fail(f"parser error: {exc.__class__.__name__}")
            res = ParseResult(None, report)
        self.record(doc, res.report, "on")
        return res

    def shadow(self, doc, llm_result) -> None:
        """Queue the résumé shadow parse (never blocks the caller)."""
        if config.PARSE_RESUME != "shadow" or doc.type != "resume" or not doc.file_path \
                or isinstance(llm_result, Exception) or llm_result is None:
            return
        _SHADOW_JOBS.append(SHADOW.submit(self._shadow, doc, llm_result))

    def _shadow(self, doc, llm_result) -> None:
        from extraction.parse.compare import compare_profiles
        from extraction.parse.resume import parse_resume
        from extraction.validate import validate_extraction
        calls = config.PARSE_SHADOW_CALLS
        labeller = self.labeller if calls else NoLabeller()
        try:
            res = parse_resume(Path(doc.file_path).name, Path(doc.file_path).read_bytes(), doc.text, self.vocab,
                               labeller, llm=self.llm if calls else None)
            reference, _ = validate_extraction(doc, llm_result)
            comparison = None
            if res.extract is not None:
                got, rep = validate_extraction(doc, res.extract, method_of=res.method_of)
                comparison = compare_profiles(asdict(reference), asdict(got))
                comparison["quote_check_dropped"] = rep.dropped
            self.record(doc, res.report, "shadow", comparison)
        except Exception:                               # shadow mode must never affect anything
            log.exception("shadow parse failed")

    def record(self, doc, report: Report, mode: str, comparison: dict | None = None) -> None:
        log.info("parser %s %s: %s %s", mode, doc.type, report.outcome, "; ".join(report.reasons))
        if not self.db_path:
            return
        conn = db.connect(self.db_path)
        try:
            row = conn.execute("SELECT candidate_id FROM evidence_items WHERE id = ?", (doc.evidence_id,)).fetchone()
            with conn:
                conn.execute(
                    "INSERT INTO parser_runs (id, candidate_id, evidence_id, doc_type, mode, outcome, reasons_json, "
                    "notes_json, items_json, comparison_json, seconds, jev_requests, jev_questions, llm_calls, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), row[0] if row else None, doc.evidence_id, doc.type, mode, report.outcome,
                     json.dumps(report.reasons), json.dumps(report.notes), json.dumps(report.items),
                     json.dumps(comparison, default=str) if comparison is not None else None, report.seconds,
                     report.jev_requests, report.jev_questions, report.llm_calls, now_iso()))
        finally:
            conn.close()
