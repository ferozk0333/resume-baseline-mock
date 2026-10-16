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
    portfolio_url: str | None = None
    portfolio_text: str | None = None   # pasted content when the page is JavaScript-rendered
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
    return SourceDoc(eid, ev_type, text, label)


def ingest(conn: sqlite3.Connection, inputs: OnboardingInputs, *,
           github_client: github.GitHubClient | None = None,
           portfolio_session=None) -> tuple[str, list[SourceDoc], list[str]]:
    """Create the candidate and evidence items. Returns (candidate_id, docs to extract, warnings)."""
    warnings: list[str] = []
    username = github.parse_username(inputs.github)
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

    if inputs.github and not username:
        warnings.append(f"GitHub: '{inputs.github}' is not a valid GitHub username or profile URL.")
    if username:
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

    if inputs.portfolio_text or inputs.portfolio_url:
        try:
            with timing.span(timing.INGEST, "portfolio"):
                page = (portfolio.page_from_text(inputs.portfolio_url, inputs.portfolio_text) if inputs.portfolio_text
                        else portfolio.fetch_portfolio(inputs.portfolio_url, portfolio_session))
        except portfolio.PortfolioError as exc:
            warnings.append(f"Portfolio: {exc}")
            page = None
        if page is not None:
            source = "form" if inputs.portfolio_text else "url"
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
                     today: date | None = None) -> tuple[list[Profile], ValidationReport]:
    """Validate each document's extraction against its own text and record per-source results.

    `results` maps evidence_id → extraction model, or an Exception when extraction failed.
    """
    report = ValidationReport()
    profiles: list[Profile] = []
    for doc in docs:
        result = results.get(doc.evidence_id)
        failed = isinstance(result, Exception)
        if failed:
            store.set_evidence_status(conn, doc.evidence_id, "failed", error=str(result))
            report.warnings.append(f"{doc.label}: extraction failed ({result}).")
            if doc.type != "github_repo":
                continue
            result = None  # a repo still counts from its API metadata
        if doc.type == "github_repo":
            profile, sub = repo_profile(doc, result, today)
        else:
            profile, sub = validate_extraction(doc, result, today)
        report.warnings += sub.warnings
        report.dropped_items += sub.dropped_items
        report.kept += sub.kept
        report.project_remap.update(sub.project_remap)
        if not failed:
            store.set_evidence_status(conn, doc.evidence_id, "validated", extracted=profile,
                                      items_extracted=sub.kept, items_dropped=sub.dropped)
        profiles.append(profile)
    return profiles, report


def docs_to_extract(docs: list[SourceDoc]) -> list[SourceDoc]:
    """Repos need an LLM call only for a README of the candidate's own work."""
    return [d for d in docs if d.type != "github_repo" or (d.repo.readme and d.repo.counted)]


def extract_and_validate(conn: sqlite3.Connection, docs: list[SourceDoc], llm: StructuredLLM,
                         today: date | None = None) -> tuple[list[Profile], ValidationReport]:
    to_extract = docs_to_extract(docs)
    results = extract_all(to_extract, llm) if to_extract else {}
    return validate_results(conn, docs, results, today)


def finish_onboarding(conn: sqlite3.Connection, candidate_id: str, forms: Profile, profiles: list[Profile],
                      report: ValidationReport, warnings: list[str]) -> OnboardingResult:
    """Merge sources, save the profile and the dropped items. Form entries first so the user's details win."""
    merged, merge_report = merge_profiles([forms, *profiles])
    # A dropped bullet's parent may have been merged into another role/project; point at the survivor.
    remap = {**merge_report.role_remap, **merge_report.project_remap}
    for d in report.dropped_items:
        while d.parent_id in remap:
            d.parent_id = remap[d.parent_id]
    store.save_profile(conn, candidate_id, merged)
    store.save_dropped(conn, candidate_id, report.dropped_items)
    return OnboardingResult(candidate_id, merged, [*warnings, *report.warnings], scoring_blocker(merged),
                            dropped=report.dropped, kept=report.kept)


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
