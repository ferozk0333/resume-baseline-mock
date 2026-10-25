"""Validated profile records: the shape saved to SQLite and shown in the review step."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core.db import new_id


# Reason codes stored in roles/projects.review_reasons, with the review-screen text.
REVIEW_MESSAGES = {
    "year_only_start": "Start date had only a year; set to January. Check the month.",
    "year_only_end": "End date had only a year; set to December. Check the month.",
    "missing_start_date": "No start date found.",
    "missing_end_date": "No end date and not marked current; end set to the start date.",
    "unparsed_start_date": "Start date could not be read.",
    "unparsed_end_date": "End date could not be read.",
    "future_date": "Date is in the future; excluded from date calculations until fixed.",
    "end_before_start": "End date is before the start date; excluded from date calculations until fixed.",
}


@dataclass
class BulletRec:
    text: str
    location: str | None
    evidence_id: str | None
    source: str = "extracted"         # extracted | user_edit
    method: str | None = None         # how it was read: parser | jev | llm (None: typed or edited by the user)
    id: str = field(default_factory=new_id)


@dataclass
class RoleRec:
    title: str
    employer: str | None
    industry: str | None
    start_date: date | None
    end_date: date | None
    is_current: bool
    team_size: int | None
    quote: str | None
    evidence_id: str | None
    bullets: list[BulletRec] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    source: str = "extracted"
    method: str | None = None
    id: str = field(default_factory=new_id)
    location: str | None = None       # display only (specs/19 item 7)
    sources: list[str] = field(default_factory=list)   # evidence ids merged into this item; display only (item 18)

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)


@dataclass
class ProjectRec:
    kind: str
    name: str
    description: str | None
    url: str | None
    start_date: date | None
    end_date: date | None
    quote: str | None
    evidence_id: str | None
    role_id: str | None = None
    is_fork: bool | None = None       # GitHub only
    own_commits: int | None = None    # GitHub only; non-null marks a GitHub-backed project
    bullets: list[BulletRec] = field(default_factory=list)
    review_reasons: list[str] = field(default_factory=list)
    source: str = "extracted"
    method: str | None = None
    id: str = field(default_factory=new_id)
    details: dict = field(default_factory=dict)        # type-specific fields shown in Review (venue, event, …)
    sources: list[str] = field(default_factory=list)

    @property
    def needs_review(self) -> bool:
        return bool(self.review_reasons)

    @property
    def is_github(self) -> bool:
        return self.own_commits is not None


@dataclass
class ClaimRec:
    kind: str                         # skill | tool | award
    name_raw: str
    name_norm: str
    quote: str | None
    evidence_id: str | None
    bullet_id: str | None = None      # None = listed only
    source: str = "extracted"
    method: str | None = None
    id: str = field(default_factory=new_id)
    details: dict = field(default_factory=dict)        # awards added in Review: issuer, date, what it was for
    skill_group: str | None = None    # a group the candidate picked when adding it (display only)
    sources: list[str] = field(default_factory=list)


@dataclass
class CertificationRec:
    name: str
    issuer: str | None
    issued_date: date | None
    credential_id: str | None
    verify_url: str | None
    evidence_id: str | None
    source: str = "extracted"
    method: str | None = None
    id: str = field(default_factory=new_id)
    sources: list[str] = field(default_factory=list)

    @property
    def verified(self) -> bool:
        # MVP rule (04): verified only if a credential ID or verify URL is present.
        return bool((self.credential_id or "").strip() or (self.verify_url or "").strip())


@dataclass
class ReferenceRec:
    name: str
    relationship: str | None          # manager | supervisor | peer | other
    contact: str | None
    statement: str | None
    id: str = field(default_factory=new_id)


@dataclass
class ExtraItem:
    """A display-only proof type (specs/19 Phase D): education, patents, volunteering, courses, languages.
    Never part of Profile, so it never reaches a judge or a score; shown in Review and to recruiters."""
    type: str                         # education | patent | volunteering | course | language
    fields: dict
    evidence_id: str | None = None
    quote: str | None = None
    source: str = "extracted"         # extracted | user_edit
    id: str = field(default_factory=new_id)


def sources_of(rec) -> list[str]:
    """Evidence ids an item came from (its own first); "user" for one typed in Review."""
    own = [rec.evidence_id] if rec.evidence_id else (["user"] if rec.source == "user_edit" else [])
    return list(dict.fromkeys([*own, *rec.sources]))


@dataclass
class Profile:
    roles: list[RoleRec] = field(default_factory=list)
    projects: list[ProjectRec] = field(default_factory=list)
    claims: list[ClaimRec] = field(default_factory=list)
    certifications: list[CertificationRec] = field(default_factory=list)
    references: list[ReferenceRec] = field(default_factory=list)

    def all_bullets(self) -> list[BulletRec]:
        return [b for r in self.roles for b in r.bullets] + [b for p in self.projects for b in p.bullets]


def scoring_blocker(profile: Profile) -> str | None:
    """Message that blocks scoring, or None when the profile can be scored (01 edge cases)."""
    if not profile.roles and not profile.projects:
        return "Add at least one role or project before scoring."
    return None
