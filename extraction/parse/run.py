"""Which documents are parsed in code (PARSE_LINKEDIN) and the per-document log (parser_runs).

PARSE_LINKEDIN: the parser's result is used when its gate passes; otherwise the document goes to the LLM exactly as
today and the fallback reason is logged.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from core import config, db
from core.db import new_id, now_iso
from extraction.parse.claims import vocabulary
from extraction.parse.common import ParseResult, Report
from extraction.parse.labeller import default_labeller

log = logging.getLogger(__name__)


class Parsers:
    def __init__(self, db_path: str | None = None, labeller=None, vocab=None):
        self.db_path = db_path
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
        return config.PARSE_LINKEDIN

    def wants(self, doc) -> bool:
        if doc.type == "linkedin_pdf":
            return config.PARSE_LINKEDIN and bool(doc.file_path) and doc.file_path.lower().endswith(".pdf")
        return False

    def parse(self, doc) -> ParseResult:
        try:
            from extraction.parse.linkedin import parse_linkedin
            res = parse_linkedin(Path(doc.file_path).read_bytes(), doc.text, self.vocab, self.labeller)
        except Exception as exc:                       # a parser bug never stops extraction: the LLM reads it
            log.exception("parser failed for %s", doc.type)
            report = Report(doc.type)
            report.fail(f"parser error: {exc.__class__.__name__}")
            res = ParseResult(None, report)
        self.record(doc, res.report, "on")
        return res

    def record(self, doc, report: Report, mode: str) -> None:
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
                     None, report.seconds,
                     report.jev_requests, report.jev_questions, report.llm_calls, now_iso()))
        finally:
            conn.close()
