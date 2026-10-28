"""Onboarding: ingest → extract → validate/dedupe → save (the steps before human review).

Each failure that the user can work around becomes a warning; only a missing
resume blocks onboarding outright (01 edge cases).
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from core import config, timing
from core.llm import StructuredLLM
from extraction import store
from extraction.extract import extract_all
from extraction.records import (
    CertificationRec, Profile, ReferenceRec, scoring_blocker,
)
from extraction.validate import (
    SourceDoc, ValidationReport, merge_profiles, parse_month, repo_profile, validate_extraction,
)
from ingest import documents, github, portfolio
from ingest.http_cache import CachedSession

NO_RESUME_MESSAGE = "Upload a resume to get your score."


@dataclass
class Upload:
    filename: str
    data: bytes


@dataclass
class CertificationForm:
    name: str
    issuer: str | None = None
    date: str | None = None
    credential_id: str | None = None
    verify_url: str | None = None


@dataclass
class ReferenceForm:
    name: str
    relationship: str | None = None   # manager | supervisor | peer | other
    contact: str | None = None
    statement: str | None = None


@dataclass
class OnboardingInputs:
    resume: Upload | None
    linkedin_pdf: Upload | None = None
    github: str | None = None
    github_pdf: Upload | None = None    # a saved GitHub profile page: read in code, used instead of `github`
    portfolio_url: str | None = None
    portfolio_text: str | None = None   # pasted content when the page is JavaScript-rendered
    portfolio_pdf: Upload | None = None  # the portfolio saved as PDF: its text is used instead of `portfolio_url`
    certifications: list[CertificationForm] = field(default_factory=list)
    references: list[ReferenceForm] = field(default_factory=list)
    name: str | None = None
    target_role: str | None = None
    candidate_id: str | None = None     # re-run extraction for this candidate (replaces the saved profile)


@dataclass
class OnboardingResult:
    candidate_id: str | None
    profile: Profile | None
    warnings: list[str] = field(default_factory=list)
    blocked: str | None = None   # message that blocks moving on to scoring
    dropped: int = 0             # items dropped by the quote check (listed in the review step)
    kept: int = 0


def _ingest_document(conn, candidate_id: str, upload: Upload, ev_type: str, label: str,
                     warnings: list[str]) -> SourceDoc | None:
    path = documents.save_upload(candidate_id, upload.filename, upload.data)
    sha = documents.sha256(upload.data)
    try:
        text = documents.extract_text(upload.filename, upload.data, conn)
    except documents.DocumentError as exc:
        store.add_evidence(conn, candidate_id, ev_type, "upload", file_path=str(path), file_sha256=sha,
                           status="failed", error=str(exc))
        warnings.append(f"{label}: {exc}")
        return None
    eid = store.add_evidence(conn, candidate_id, ev_type, "upload", raw_text=text, file_path=str(path),
                             file_sha256=sha)
    return SourceDoc(eid, ev_type, text, label, file_path=str(path))


def ingest(conn: sqlite3.Connection, inputs: OnboardingInputs, *,
           github_client: github.GitHubClient | None = None,
           portfolio_session=None) -> tuple[str, list[SourceDoc], list[str]]:
    """Create the candidate and evidence items. Returns (candidate_id, docs to extract, warnings)."""
    warnings: list[str] = []
    username = github.parse_username(inputs.github)
    pdf_repos: list[github.Repo] | None = None
    if inputs.github_pdf:                       # used instead of the GitHub field (no API call)
        with timing.span(timing.INGEST, "github_pdf") as t:
            from ingest.github_pdf import parse_github_pdf
            pdf_user, pdf_repos, pdf_warnings = parse_github_pdf(inputs.github_pdf.data)
            t["repos"] = len(pdf_repos)
        warnings += [w.replace("GitHub PDF:", "GitHub:", 1) for w in pdf_warnings]
        username = username or pdf_user
    if inputs.candidate_id and store.get_candidate(conn, inputs.candidate_id):
        candidate_id = inputs.candidate_id
        store.reset_candidate_evidence(conn, candidate_id)
        with conn:
            conn.execute("UPDATE candidates SET name = COALESCE(?, name), target_role = ?, github_username = ?, "
                         "portfolio_url = ? WHERE id = ?",
                         (inputs.name, inputs.target_role, username, inputs.portfolio_url, candidate_id))
    else:
        candidate_id = store.create_candidate(conn, inputs.name, inputs.target_role, username, inputs.portfolio_url)
    docs: list[SourceDoc] = []

    with timing.span(timing.INGEST, "resume"):
        resume = _ingest_document(conn, candidate_id, inputs.resume, "resume", "Resume", warnings)
    if resume:
        docs.append(resume)
    if inputs.linkedin_pdf:
        with timing.span(timing.INGEST, "linkedin_pdf"):
            linkedin = _ingest_document(conn, candidate_id, inputs.linkedin_pdf, "linkedin_pdf", "LinkedIn PDF",
                                        warnings)
        if linkedin:
            docs.append(linkedin)

    if pdf_repos is not None:
        path = documents.save_upload(candidate_id, inputs.github_pdf.filename, inputs.github_pdf.data)
        for repo in pdf_repos:
            eid = store.add_evidence(conn, candidate_id, "github_repo", "upload", raw_text=repo.as_text(),
                                     file_path=str(path), file_sha256=documents.sha256(inputs.github_pdf.data))
            docs.append(SourceDoc(eid, "github_repo", repo.as_text(), f"GitHub repo {repo.name}", repo=repo))
    elif inputs.github and not username:
        warnings.append(f"GitHub: '{inputs.github}' is not a valid GitHub username or profile URL.")
    elif username:
        with timing.span(timing.INGEST, "github") as t:
            try:
                client = github_client or github.GitHubClient(session=CachedSession(conn))
                repos = client.fetch_repos(
                    username, include_readme=config.GITHUB_README_EXTRACTION)
            except github.GitHubError as exc:
                warnings.append(f"GitHub: {exc}")
                repos = exc.partial
            t["repos"] = len(repos)
        for repo in repos:
            eid = store.add_evidence(conn, candidate_id, "github_repo", "api", raw_text=repo.as_text())
            docs.append(SourceDoc(eid, "github_repo", repo.as_text(), f"GitHub repo {repo.name}", repo=repo))

    page, source = None, None
    if inputs.portfolio_pdf:                    # used instead of the portfolio URL (nothing is fetched)
        pdf = inputs.portfolio_pdf
        path = documents.save_upload(candidate_id, pdf.filename, pdf.data)
        try:
            with timing.span(timing.INGEST, "portfolio_pdf"):
                page = portfolio.page_from_text(None, documents.extract_text(pdf.filename, pdf.data, conn))
            source = "upload"
        except documents.DocumentError as exc:
            store.add_evidence(conn, candidate_id, "portfolio", "upload", file_path=str(path),
                               file_sha256=documents.sha256(pdf.data), status="failed", error=str(exc))
            warnings.append(f"Portfolio: {exc}")
    elif inputs.portfolio_text or inputs.portfolio_url:
        try:
            with timing.span(timing.INGEST, "portfolio"):
                page = (portfolio.page_from_text(inputs.portfolio_url, inputs.portfolio_text) if inputs.portfolio_text
                        else portfolio.fetch_portfolio(inputs.portfolio_url, portfolio_session))
        except portfolio.PortfolioError as exc:
            warnings.append(f"Portfolio: {exc}")
        source = "form" if inputs.portfolio_text else "url"
    if page is not None:
        if page.warning:
            warnings.append(f"Portfolio: {page.warning}")
            store.add_evidence(conn, candidate_id, "portfolio", source, raw_text=page.text,
                               status="failed", error=page.warning)
        else:
            eid = store.add_evidence(conn, candidate_id, "portfolio", source, raw_text=page.text)
            docs.append(SourceDoc(eid, "portfolio", page.text, "Portfolio"))
    return candidate_id, docs, warnings


def form_profile(conn: sqlite3.Connection, candidate_id: str, inputs: OnboardingInputs) -> Profile:
    """Certifications and references typed into the form. No quote check: the user wrote them."""
    profile = Profile()
    for c in inputs.certifications:
        if not c.name.strip():
            continue
        eid = store.add_evidence(conn, candidate_id, "certification", "form", raw_text=c.name, status="validated")
        profile.certifications.append(CertificationRec(
            name=c.name.strip(), issuer=c.issuer, issued_date=parse_month(c.date, is_end=False).value,
            credential_id=c.credential_id, verify_url=c.verify_url, evidence_id=eid,
        ))
    for r in inputs.references:
        if not r.name.strip():
            continue
        store.add_evidence(conn, candidate_id, "reference", "form", raw_text=r.statement, status="validated")
        profile.references.append(ReferenceRec(r.name.strip(), r.relationship, r.contact, r.statement))
    return profile


def validate_results(conn: sqlite3.Connection, docs: list[SourceDoc], results: dict[str, object],
                     today: date | None = None, set_status: bool = True) -> tuple[list[Profile], ValidationReport]:
    """Validate each document's extraction against its own text and record per-source results.

    `results` maps evidence_id → extraction model, a ParseResult (read in code), or an Exception when extraction
    failed. set_status=False leaves the evidence status to the caller (set once the profile is saved).
    """
    from extraction.parse.common import ParseResult
    report = ValidationReport()
    profiles: list[Profile] = []
    for doc in docs:
        result = results.get(doc.evidence_id)
        method_of = None
        if isinstance(result, ParseResult):
            result, method_of = result.extract, result.method_of
        failed = isinstance(result, Exception)
        if failed:
            if set_status:
                store.set_evidence_status(conn, doc.evidence_id, "failed", error=str(result))
            report.warnings.append(f"{doc.label}: extraction failed ({result}).")
            report.failed.append(doc.evidence_id)
            if doc.type != "github_repo":
                continue
            result = None  # a repo still counts from its API metadata
        if doc.type == "github_repo":
            profile, sub = repo_profile(doc, result, today, method_of)
        else:
            profile, sub = validate_extraction(doc, result, today, method_of)
        report.warnings += sub.warnings
        report.dropped_items += sub.dropped_items
        report.kept += sub.kept
        report.project_remap.update(sub.project_remap)
        if not failed and set_status:
            store.set_evidence_status(conn, doc.evidence_id, "validated", extracted=profile,
                                      items_extracted=sub.kept, items_dropped=sub.dropped)
        profiles.append(profile)
        report.sources[doc.evidence_id] = (profile, sub)
    return profiles, report


def docs_to_extract(docs: list[SourceDoc]) -> list[SourceDoc]:
    """Repos need an LLM call only for a README of the candidate's own work."""
    return [d for d in docs if d.type != "github_repo" or (d.repo.readme and d.repo.counted)]


def extract_and_validate(conn: sqlite3.Connection, docs: list[SourceDoc], llm: StructuredLLM,
                         today: date | None = None, parsers=None) -> tuple[list[Profile], ValidationReport]:
    to_extract = docs_to_extract(docs)
    results = extract_all(to_extract, llm, parsers=parsers) if to_extract else {}
    return validate_results(conn, docs, results, today)


def finish_onboarding(conn: sqlite3.Connection, candidate_id: str, forms: Profile, profiles: list[Profile],
                      report: ValidationReport, warnings: list[str]) -> OnboardingResult:
    """Merge sources, save the profile and the dropped items. Form entries first so the user's details win."""
    merged, merge_report = merge_profiles([forms, *profiles], _types(conn, candidate_id))
    # A dropped bullet's parent may have been merged into another role/project; point at the survivor.
    remap = {**merge_report.role_remap, **merge_report.project_remap}
    for d in report.dropped_items:
        while d.parent_id in remap:
            d.parent_id = remap[d.parent_id]
    store.save_profile(conn, candidate_id, merged)
    store.save_dropped(conn, candidate_id, report.dropped_items)
    for e in store.list_evidence(conn, candidate_id):
        if e["type"] in EXTRA_SOURCES and e["status"] != "failed":
            read_extra(conn, candidate_id, e["id"], e["raw_text"] or "", merged)
    return OnboardingResult(candidate_id, merged, [*warnings, *report.warnings], scoring_blocker(merged),
                            dropped=report.dropped, kept=report.kept)


EXTRA_SOURCES = ("resume", "linkedin_pdf")


def _types(conn, candidate_id: str) -> dict[str, str]:
    return {e["id"]: e["type"] for e in store.list_evidence(conn, candidate_id)}


def read_extra(conn, candidate_id: str, evidence_id: str, text: str, profile: Profile) -> None:
    """Education, languages, courses, patents and volunteering named in a document (display only, specs/19 Phase D).
    Code only: no model call."""
    from core.text import normalize_name
    from extraction.extra import extra_items
    employers = {normalize_name(r.employer) for r in profile.roles if r.employer}
    store.merge_extracted_extra(conn, candidate_id, evidence_id, extra_items(text, evidence_id, employers))


def run_onboarding(conn: sqlite3.Connection, inputs: OnboardingInputs, llm: StructuredLLM, *,
                   github_client: github.GitHubClient | None = None, portfolio_session=None,
                   today: date | None = None) -> OnboardingResult:
    if inputs.resume is None:
        return OnboardingResult(None, None, blocked=NO_RESUME_MESSAGE)
    candidate_id, docs, warnings = ingest(conn, inputs, github_client=github_client,
                                          portfolio_session=portfolio_session)
    forms = form_profile(conn, candidate_id, inputs)
    profiles, report = extract_and_validate(conn, docs, llm, today)
    return finish_onboarding(conn, candidate_id, forms, profiles, report, warnings)


class ProgressiveSave:
    """Show Review early (specs/09 item 5, specs/18 Phase 3): each source is validated, merged into the saved profile
    and marked read as soon as it and every source before it are done, in the usual order (resume, LinkedIn, repos,
    portfolio). Merging in that order gives the same profile as merging everything at the end; merging into the
    saved profile keeps what the candidate already edited in Review. `on_ready(candidate_id, doc_type)` runs after
    each source once the resume is read (early-start judging); it must not block.
    """

    def __init__(self, db_path: str, candidate_id: str, docs: list[SourceDoc], forms: Profile, *,
                 needs_extraction: set[str], today: date | None = None, on_ready=None):
        import threading
        self.db_path, self.candidate_id, self.docs = db_path, candidate_id, docs
        self.forms, self.today, self.on_ready = forms, today, on_ready
        self.results: dict[str, object] = {d.evidence_id: None for d in docs if d.evidence_id not in needs_extraction}
        self.report = ValidationReport()
        self.next = 0
        self.started = False
        self.resume_ok = False
        self._lock = threading.Lock()

    def start(self) -> None:
        """Merge the sources that need no extraction and come first (none usually: the resume is first), or save the
        form entries alone when there is no document at all."""
        with self._lock:
            self._advance()

    def done(self, evidence_id: str, result) -> None:
        with self._lock:
            self.results[evidence_id] = result
            self._advance()

    def _advance(self) -> None:
        from core import db
        while self.next < len(self.docs) and self.docs[self.next].evidence_id in self.results:
            doc = self.docs[self.next]
            conn = db.connect(self.db_path)
            try:
                self._apply(conn, doc, self.results[doc.evidence_id])
            finally:
                conn.close()
            self.next += 1
            if self.resume_ok and self.on_ready is not None:
                self.on_ready(self.candidate_id, doc.type)
        if not self.started and not self.docs:
            conn = db.connect(self.db_path)
            try:
                with store.candidate_lock(self.candidate_id):
                    store.save_profile(conn, self.candidate_id, merge_profiles([self.forms])[0])
                    store.save_dropped(conn, self.candidate_id, [])
                self.started = True
            finally:
                conn.close()

    def _apply(self, conn, doc: SourceDoc, result) -> None:
        profiles, sub = validate_results(conn, [doc], {doc.evidence_id: result}, self.today, set_status=False)
        with store.candidate_lock(self.candidate_id):
            if self.started:                             # what the candidate deleted in Review stays deleted
                from extraction.deletions import keep_deleted_out
                profiles = [keep_deleted_out(conn, self.candidate_id, p) for p in profiles]
            current = store.load_profile(conn, self.candidate_id) if self.started else self.forms
            merged, merge_report = merge_profiles([current, *profiles], _types(conn, self.candidate_id))
            remap = {**merge_report.role_remap, **merge_report.project_remap}
            for d in sub.dropped_items:
                while d.parent_id in remap:
                    d.parent_id = remap[d.parent_id]
            store.save_profile(conn, self.candidate_id, merged)
            if self.started:
                store.append_dropped(conn, self.candidate_id, sub.dropped_items)
            else:
                store.save_dropped(conn, self.candidate_id, sub.dropped_items)
            self.started = True
            if doc.evidence_id in sub.failed:
                store.set_evidence_status(conn, doc.evidence_id, "failed", error=str(result))
            elif doc.type in EXTRA_SOURCES:
                read_extra(conn, self.candidate_id, doc.evidence_id, doc.text, merged)
            if doc.evidence_id in sub.sources:
                profile, own = sub.sources[doc.evidence_id]
                if doc.evidence_id not in sub.failed:
                    store.set_evidence_status(conn, doc.evidence_id, "validated", extracted=profile,
                                              items_extracted=own.kept, items_dropped=own.dropped)
        self.report.warnings += sub.warnings
        self.report.dropped_items += sub.dropped_items
        self.report.kept += sub.kept
        if doc.type == "resume":
            self.resume_ok = doc.evidence_id not in sub.failed

    def finish(self, warnings: list[str]) -> OnboardingResult:
        """After every source: the same result finish_onboarding gives."""
        from core import db
        with self._lock:
            missing = [d.evidence_id for d in self.docs if d.evidence_id not in self.results]
            for eid in missing:                          # never reached (extraction always reports); be safe
                self.results[eid] = RuntimeError("not read")
            self._advance()
        conn = db.connect(self.db_path)
        try:
            merged = store.load_profile(conn, self.candidate_id)
        finally:
            conn.close()
        return OnboardingResult(self.candidate_id, merged, [*warnings, *self.report.warnings], scoring_blocker(merged),
                                dropped=self.report.dropped, kept=self.report.kept)
