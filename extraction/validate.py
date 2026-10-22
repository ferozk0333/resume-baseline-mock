"""Turn raw extractions into validated records: quote check, dates, normalization, dedupe.

Runs before anything is saved. Items whose quote is not in the source text are
dropped (CLAUDE.md rule 3) and reported as warnings so the user can re-add them.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime

from core import config
from core.text import contains_phrase, normalize_name
from extraction import schemas
from extraction import dedupe
from extraction.records import (
    BulletRec, CertificationRec, ClaimRec, Profile, ProjectRec, RoleRec, new_id, sources_of,
)
from extraction.synonyms import CLAIM_SYNONYMS

# --- Quote check ------------------------------------------------------------------

_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
    "•": " ", "▪": " ", "●": " ", "·": " ", "": " ",
})


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_PUNCT)
    return " ".join(text.casefold().split())


class QuoteIndex:
    """Normalized views of one source text for tolerant-but-exact quote lookup.

    Tolerates case, whitespace/line breaks, curly quotes, dashes, bullet glyphs and
    PDF line-break hyphenation. It does not tolerate paraphrase.
    """

    def __init__(self, text: str):
        self.plain = _norm(text)
        self.dehyphenated = _norm(re.sub(r"-[ \t]*\n[ \t]*", "", text))
        self.compact = re.sub(r"\s+", "", self.dehyphenated)
        self.compact_plain = re.sub(r"\s+", "", self.plain)

    def contains(self, quote: str | None) -> bool:
        if not quote or not quote.strip():
            return False
        q = _norm(quote)
        if q in self.plain or q in self.dehyphenated:
            return True
        # PDF extraction sometimes drops or adds spaces between words.
        qc = q.replace(" ", "")
        return qc in self.compact or qc in self.compact_plain


# --- Dates --------------------------------------------------------------------------

_PRESENT = {"present", "current", "now", "today", "ongoing"}
_FORMATS = ("%b %Y", "%B %Y", "%m/%Y", "%m-%Y", "%Y/%m", "%b. %Y")


@dataclass
class ParsedDate:
    value: date | None
    year_only: bool = False
    unparsed: bool = False
    present: bool = False


def parse_month(raw: str | None, *, is_end: bool) -> ParsedDate:
    """YYYY-MM → first of month; YYYY → January (start) / December (end), flagged year_only."""
    if raw is None or not str(raw).strip():
        return ParsedDate(None)
    s = str(raw).strip()
    if s.casefold() in _PRESENT:
        return ParsedDate(None, present=True)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return ParsedDate(date(int(m.group(1)), int(m.group(2)), 1))
    m = re.fullmatch(r"(\d{4})", s)
    if m:
        return ParsedDate(date(int(s), 12 if is_end else 1, 1), year_only=True)
    for fmt in _FORMATS:
        try:
            d = datetime.strptime(s, fmt).date()
            return ParsedDate(date(d.year, d.month, 1))
        except ValueError:
            continue
    return ParsedDate(None, unparsed=True)


def _month(d: date) -> int:
    return d.year * 12 + d.month - 1


def resolve_dates(
    start_raw: str | None, end_raw: str | None, is_current: bool, *, today: date, is_role: bool,
) -> tuple[date | None, date | None, bool, list[str]]:
    """Normalize a start/end pair and collect review reasons (01 edge cases)."""
    start, end = parse_month(start_raw, is_end=False), parse_month(end_raw, is_end=True)
    reasons: list[str] = []
    current = is_current or end.present
    if start.year_only:
        reasons.append("year_only_start")
    if end.year_only and not current:
        reasons.append("year_only_end")
    if start.unparsed:
        reasons.append("unparsed_start_date")
    if end.unparsed:
        reasons.append("unparsed_end_date")
    s, e = start.value, None if current else end.value
    if is_role:
        if s is None and not start.unparsed:
            reasons.append("missing_start_date")
        if s is not None and e is None and not current and not end.unparsed:
            e = s
            reasons.append("missing_end_date")
    now = _month(today)
    if (s and _month(s) > now) or (e and _month(e) > now):
        reasons.append("future_date")
    if s and e and e < s:
        reasons.append("end_before_start")
    return s, e, current, reasons


# --- Claim names ----------------------------------------------------------------------

def normalize_claim(name: str) -> str:
    n = normalize_name(name)
    return CLAIM_SYNONYMS.get(n, n)


# --- Per-source validation -----------------------------------------------------------

@dataclass
class SourceDoc:
    """One evidence item ready for extraction/validation."""
    evidence_id: str
    type: str                        # resume | linkedin_pdf | portfolio | github_repo
    text: str
    label: str                       # human-readable, used in warnings
    repo: object | None = None       # ingest.github.Repo for github_repo
    file_path: str | None = None     # the saved upload (resume, LinkedIn PDF): parsers read its layout


@dataclass
class DroppedItem:
    """An extracted item whose quote was not found in the source. Kept so the user can add it back."""
    item_type: str                   # role | project | bullet | claim | certification
    text: str                        # shown in the review step
    payload: dict                    # the raw extracted item (schemas.* model_dump)
    evidence_id: str
    parent_type: str | None = None   # bullets: role | project
    parent_id: str | None = None
    reason: str = "quote_not_found"
    id: str = field(default_factory=new_id)


@dataclass
class ValidationReport:
    warnings: list[str] = field(default_factory=list)
    dropped_items: list[DroppedItem] = field(default_factory=list)
    kept: int = 0
    merged: int = 0
    role_remap: dict[str, str] = field(default_factory=dict)      # merged-away id → surviving id
    project_remap: dict[str, str] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)               # evidence ids whose extraction failed
    sources: dict[str, tuple] = field(default_factory=dict)       # evidence id → (profile, its own report)
    unused_bullets: list[BulletRec] = field(default_factory=list)  # another source's bullets on an item that has its own
    placements: list[tuple[str, str]] = field(default_factory=list)  # (award name, hackathon it became the placement of)

    @property
    def dropped(self) -> int:
        return len(self.dropped_items)

    def drop(self, doc: "SourceDoc", item: DroppedItem, what: str) -> None:
        self.dropped_items.append(item)
        self.warnings.append(f"{doc.label}: dropped {what} (quote not found in the source).")


# --- Record builders (shared by validation and "Add back") ---------------------------

def role_rec(r: schemas.Role, evidence_id: str | None, today: date, source: str = "extracted") -> RoleRec:
    start, end, current, reasons = resolve_dates(r.start, r.end, r.is_current, today=today, is_role=True)
    return RoleRec(
        title=r.title.strip(), employer=r.employer, industry=r.industry,
        start_date=start, end_date=end, is_current=current, team_size=r.team_size,
        quote=r.source.quote, evidence_id=evidence_id, review_reasons=reasons, source=source,
    )


def project_rec(p: schemas.Project, evidence_id: str | None, today: date, role_id: str | None = None,
                source: str = "extracted") -> ProjectRec:
    start, end, _, reasons = resolve_dates(p.start, p.end, False, today=today, is_role=False)
    return ProjectRec(
        kind=p.kind, name=p.name.strip(), description=p.description, url=p.url,
        start_date=start, end_date=end, quote=p.source.quote, evidence_id=evidence_id,
        role_id=role_id, review_reasons=reasons, source=source,
    )


def bullet_rec(b: schemas.Bullet, evidence_id: str | None, source: str = "extracted") -> BulletRec:
    return BulletRec(text=b.text.strip(), location=b.source.location, evidence_id=evidence_id, source=source)


def claim_rec(c: schemas.Claim, evidence_id: str | None, bullet_id: str | None = None,
              source: str = "extracted") -> ClaimRec:
    return ClaimRec(kind=c.kind, name_raw=c.name.strip(), name_norm=normalize_claim(c.name),
                    quote=c.source.quote, evidence_id=evidence_id, bullet_id=bullet_id, source=source)


def certification_rec(c: schemas.Certification, evidence_id: str | None,
                      source: str = "extracted") -> CertificationRec:
    return CertificationRec(name=c.name.strip(), issuer=c.issuer,
                            issued_date=parse_month(c.date, is_end=False).value,
                            credential_id=c.credential_id, verify_url=c.verify_url,
                            evidence_id=evidence_id, source=source)


def _bullets(raw: list[schemas.Bullet], index: QuoteIndex, doc: SourceDoc, report: ValidationReport,
             owner: str, parent_type: str, parent_id: str, method_of=None) -> list[BulletRec]:
    out = []
    for b in raw:
        if not index.contains(b.source.quote):
            report.drop(doc, DroppedItem("bullet", b.text, b.model_dump(), doc.evidence_id, parent_type, parent_id),
                        f"a bullet under {owner}")
            continue
        out.append(bullet_rec(b, doc.evidence_id))
        out[-1].method = _method(method_of, b)
    return out


def _method(method_of, item) -> str:
    """How an item was read: the parser's record (parser | jev), else the LLM (specs/18 §4)."""
    return (method_of(item) if method_of is not None else None) or "llm"


# --- Claim lists and publications listed as certifications ------------------------------------

# Names that contain "and"/"&" but are one thing. Slashes ("CI/CD", "A/B testing") are never split.
_ONE_THING = {"research and development", "r&d", "p&l", "profit and loss", "mergers and acquisitions", "m&a",
              "sales and marketing", "search and rescue", "q&a", "at&t", "h&m", "b&w", "s&p", "johnson & johnson",
              "procter & gamble", "black and white", "trial and error"}
_AND = re.compile(r"\s+(?:and|&)\s+", re.I)


def split_claim_names(name: str) -> list[str]:
    """One claim per named skill or tool, for flat lists only: "Chroma, Qdrant, OpenSearch" → 3 names,
    "Python and SQL" → 2. A name with parentheses is never split here: whether "X (a, b)" is a list of
    tools or one method with variants is for the extraction prompt to decide, not code."""
    name = name.strip()
    if "(" in name or ")" in name:
        return [name]
    if name.casefold() in _ONE_THING:
        return [name]
    parts = [p.strip() for p in re.split(r"[,;]", name) if p.strip()]
    if len(parts) > 1:                                         # "A, B and C" / "A, B, and C"
        parts = [x.strip() for p in parts for x in _AND.split(re.sub(r"^(?:and|&)\s+", "", p, flags=re.I))
                 if x.strip()]
    elif _AND.search(name):
        sides = [p.strip() for p in _AND.split(name) if p.strip()]
        # "Python and SQL", "Docker & Kubernetes": short names on both sides.
        if len(sides) > 1 and all(len(s.split()) <= 2 for s in sides):
            parts = sides
    return parts if len(parts) > 1 else [name]


_AUTHORED = re.compile(r"\b(co-?authors?(?:ed)?|authors?(?:ed)?|et al\.?|journal|proceedings|published|publication|"
                       r"conference paper|paper|poster|talk|preprint|arxiv|doi|commission)\b", re.I)
_CREDENTIAL = re.compile(r"\b(certified|certificate|certification|license|licen[cs]ed|credential|associate|"
                         r"professional|specialty|nanodegree|badge)\b", re.I)


AWARD_MAX_CHARS = 120
_CLAUSE_END = re.compile(r"(?<=\w)[.;:](?:\s|$)|\s[–—]\s")


def award_name(name: str, quote: str | None) -> str:
    """Award claims keep their event or context: when the name is a shorter piece of its quote
    ("Top 10 finalist" in "Top 10 finalist out of 600+ teams, CSI National Hackathon: led a team of 4"),
    use the clause of the quote that contains it ("… CSI National Hackathon"), at most one sentence and
    about AWARD_MAX_CHARS characters (cut at a word boundary)."""
    q = re.sub(r"\s+", " ", (quote or "")).strip().strip("-•·,;. ")
    n = name.strip()
    if not q or len(q) <= len(n) or normalize_name(n) not in normalize_name(q):
        return n
    clauses = [c.strip(" ,") for c in _CLAUSE_END.split(q) if c and c.strip(" ,")]
    clause = next((c for c in clauses if normalize_name(n) in normalize_name(c)), q)
    if len(clause) > AWARD_MAX_CHARS:
        cut = clause[:AWARD_MAX_CHARS]
        clause = cut[:cut.rfind(" ")] if " " in cut else cut
        clause = clause.rstrip(" ,;-–—") if normalize_name(n) in normalize_name(clause) else n
    return clause if len(clause) >= len(n) else n


def looks_like_publication(c: schemas.Certification) -> bool:
    """An authored paper, article, poster or talk listed as a certification (e.g. "Co-Author, 'The Lancet …'")."""
    text = " ".join(x for x in (c.name, c.issuer, c.source.quote) if x)
    return bool(_AUTHORED.search(text)) and not _CREDENTIAL.search(text) and not c.credential_id


def _link_claim(claim: schemas.Claim, bullets: list[BulletRec]) -> str | None:
    if claim.bullet_ref is None:
        return None
    for b in bullets:
        if b.location and normalize_name(b.location) == normalize_name(claim.bullet_ref):
            return b.id
    for b in bullets:  # fall back to the first bullet that names it
        if contains_phrase(b.text, claim.name):
            return b.id
    return None


def validate_extraction(
    doc: SourceDoc,
    extract: schemas.ProfileExtract | schemas.PortfolioExtract,
    today: date | None = None,
    method_of=None,
) -> tuple[Profile, ValidationReport]:
    """Keep items whose quote is in the source text; record the rest as DroppedItems."""
    today = today or config.today()
    index = QuoteIndex(doc.text)
    report = ValidationReport()
    profile = Profile()

    def drop(item_type: str, raw, text: str, what: str) -> None:
        report.drop(doc, DroppedItem(item_type, text, raw.model_dump(), doc.evidence_id), what)

    role_ids: dict[int, str] = {}
    for i, r in enumerate(getattr(extract, "roles", [])):
        if not index.contains(r.source.quote):
            drop("role", r, f"{r.title}" + (f" at {r.employer}" if r.employer else ""), f"role '{r.title}'")
            continue
        rec = role_rec(r, doc.evidence_id, today)
        rec.method = _method(method_of, r)
        rec.bullets = _bullets(r.bullets, index, doc, report, f"'{rec.title}'", "role", rec.id, method_of)
        profile.roles.append(rec)
        role_ids[i] = rec.id

    for p in extract.projects:
        if not index.contains(p.source.quote):
            drop("project", p, p.name, f"project '{p.name}'")
            continue
        rec = project_rec(p, doc.evidence_id, today,
                          role_id=role_ids.get(p.role_index) if p.role_index is not None else None)
        rec.method = _method(method_of, p)
        rec.bullets = _bullets(p.bullets, index, doc, report, f"'{rec.name}'", "project", rec.id, method_of)
        profile.projects.append(rec)

    bullets = profile.all_bullets()
    for c in extract.claims:
        if not index.contains(c.source.quote):
            drop("claim", c, f"{c.kind}: {c.name}", f"{c.kind} '{c.name}'")
            continue
        names = split_claim_names(c.name)
        if len(names) > 1 and all(contains_phrase(c.source.quote, n) for n in names):
            parts = [c.model_copy(update={"name": n}) for n in names]
        else:
            parts = [c]
        for part in parts:
            if part.kind == "award":
                part = part.model_copy(update={"name": award_name(part.name, part.source.quote)})
            profile.claims.append(claim_rec(part, doc.evidence_id, _link_claim(part, bullets)))
            profile.claims[-1].method = _method(method_of, c)

    for c in getattr(extract, "certifications", []):
        if not index.contains(c.source.quote):
            drop("certification", c, c.name, f"certification '{c.name}'")
            continue
        if looks_like_publication(c):              # authored work, not a credential: count it as a publication
            pub = schemas.Project(kind="publication", name=c.name, description=None, url=c.verify_url, start=c.date,
                                  end=c.date, role_index=None, bullets=[], source=c.source)
            profile.projects.append(project_rec(pub, doc.evidence_id, today))
            profile.projects[-1].method = _method(method_of, c)
            continue
        profile.certifications.append(certification_rec(c, doc.evidence_id))
        profile.certifications[-1].method = _method(method_of, c)

    report.kept = (len(profile.roles) + len(profile.projects) + len(profile.all_bullets())
                   + len(profile.claims) + len(profile.certifications))
    return profile, report


def repo_profile(doc: SourceDoc, readme_extract: schemas.PortfolioExtract | None,
                 today: date | None = None, method_of=None) -> tuple[Profile, ValidationReport]:
    """A GitHub repo becomes one project from API metadata; its README adds bullets and tool claims.
    A parsed README (PARSE_README) brings the kept README text as the description instead of bullets."""
    repo = doc.repo
    pushed = date.fromisoformat(repo.pushed_at[:10]) if repo.pushed_at else None
    created = date.fromisoformat(repo.created_at[:10]) if repo.created_at else None
    project = ProjectRec(
        kind="repo", name=repo.name, description=repo.description, url=repo.html_url,
        start_date=created.replace(day=1) if created else None,
        end_date=pushed.replace(day=1) if pushed else None,
        quote=f"Repository: {repo.full_name}", evidence_id=doc.evidence_id,
        is_fork=repo.is_fork, own_commits=repo.own_commits, method="parser",
    )
    profile, report = Profile(projects=[project]), ValidationReport()
    if readme_extract is not None:
        sub, report = validate_extraction(doc, readme_extract, today, method_of)
        for p in sub.projects:  # fold README "projects" into the repo instead of duplicating it
            project.bullets += p.bullets
            if p.method in ("parser", "jev") and p.description:
                project.description, project.method = p.description, p.method   # kept README text, verbatim
            project.description = project.description or p.description
            report.project_remap[p.id] = project.id
        for d in report.dropped_items:  # dropped README bullets now belong to the repo project
            if d.parent_id in report.project_remap:
                d.parent_id = report.project_remap[d.parent_id]
        profile.claims = sub.claims
    return profile, report


# --- Dedupe across all sources -------------------------------------------------------

def _overlap(a_start, a_end, a_current, b_start, b_end, b_current) -> bool:
    if not a_start or not b_start:
        return True  # cannot tell; same title + employer is treated as the same role
    far = date(9999, 12, 1)
    a_end = far if a_current else (a_end or a_start)
    b_end = far if b_current else (b_end or b_start)
    return a_start <= b_end and b_start <= a_end


_ROLE_STOPWORDS = {"the", "of", "for", "and", "at", "in", "a", "an", "inc", "ltd", "llc", "co", "corp", "company"}


def _role_tokens(*parts: str | None) -> set[str]:
    return set(normalize_name(" ".join(p or "" for p in parts)).split()) - _ROLE_STOPWORDS


def role_similarity(a: RoleRec, b: RoleRec) -> float:
    """Share of the shorter (title + employer) word set found in the other one (0..1)."""
    ta, tb = _role_tokens(a.title, a.employer), _role_tokens(b.title, b.employer)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def _same_role_score(kept: RoleRec, new: RoleRec, absorbed_sources: set) -> float | None:
    """How well `new` matches an already-kept role; None if it must not be merged.

    Exact title + employer: same role (any source). Otherwise only across sources (a resume and a
    LinkedIn PDF word the same job differently): dates overlap, titles share a word, and most words
    of title + employer match (config.ROLE_MATCH_THRESHOLD). A kept role absorbs at most one role
    per other source, so two concurrent jobs listed in both sources pair up one-to-one.
    """
    if not _overlap(kept.start_date, kept.end_date, kept.is_current, new.start_date, new.end_date, new.is_current):
        return None
    if normalize_name(kept.title) == normalize_name(new.title) and \
            normalize_name(kept.employer) == normalize_name(new.employer):
        return 2.0
    if new.evidence_id is None or new.evidence_id == kept.evidence_id or new.evidence_id in absorbed_sources:
        return None
    if not (_role_tokens(kept.title) & _role_tokens(new.title)):
        return None
    score = role_similarity(kept, new)
    if score < config.ROLE_MATCH_THRESHOLD:
        return None
    same_start = kept.start_date is not None and kept.start_date == new.start_date
    return score + (0.01 if same_start else 0.0)


def _merge_bullets(into: list[BulletRec], extra: list[BulletRec]) -> dict[str, str]:
    """Append bullets not already present (by normalized text). Returns dropped-id → kept-id."""
    seen = {_norm(b.text): b.id for b in into}
    remap = {}
    for b in extra:
        key = _norm(b.text)
        if key in seen:
            remap[b.id] = seen[key]
        else:
            seen[key] = b.id
            into.append(b)
    return remap


def _take_bullets(into, other, report: "ValidationReport") -> dict[str, str | None]:
    """An item's bullets come from one source (audit round, item 2a): the earlier source's when it has any; the
    later source's only when it has none. Never both. A bullet typed by the person is always kept. Returns
    dropped-id → kept-id, or None for a bullet that is not used (a claim read from it becomes a listed one)."""
    if not into.bullets:
        kept: list[BulletRec] = []
        remap = _merge_bullets(kept, other.bullets)
        into.bullets = kept
        return remap
    seen = {_norm(b.text): b.id for b in into.bullets}
    remap: dict[str, str | None] = {}
    for b in other.bullets:
        k = _norm(b.text)
        if k in seen:
            remap[b.id] = seen[k]
        elif b.source == "user_edit":
            seen[k] = b.id
            into.bullets.append(b)
        else:
            remap[b.id] = None
            report.unused_bullets.append(b)
    return remap


def _one_source(item, rank, report: "ValidationReport") -> dict[str, None]:
    """A saved item whose bullets came from several documents (an earlier merge appended them): only its own
    document's bullets stay, or the best-ranked other document's when it has none of its own."""
    extracted = [b for b in item.bullets if b.source != "user_edit"]
    sources = list(dict.fromkeys(b.evidence_id for b in extracted))
    if len(sources) < 2:
        return {}
    keep = item.evidence_id if item.evidence_id in sources else min(sources, key=rank)
    gone = [b for b in extracted if b.evidence_id != keep]
    item.bullets = [b for b in item.bullets if b not in gone]
    report.unused_bullets += gone
    return {b.id: None for b in gone}


def _fill(target, source, fields: tuple[str, ...]) -> None:
    for f in fields:
        if getattr(target, f) in (None, "") and getattr(source, f) not in (None, ""):
            setattr(target, f, getattr(source, f))


def _add_sources(into, other) -> None:
    """Where a merged item came from, for the source chips (display only; never scored)."""
    into.sources = [x for x in dict.fromkeys([*sources_of(into), *sources_of(other)]) if x != into.evidence_id]


def claim_key(c: ClaimRec) -> tuple[str, str]:
    """One claim per name (specs/19 item 17): awards apart from skills and tools."""
    group = "award" if c.kind == "award" else "skill"
    if not config.DEDUPE_NAMES:
        return group, c.name_norm
    k = dedupe.canonical(c.name_raw)
    return group, CLAIM_SYNONYMS.get(k, k)


def _same_project(a: ProjectRec, b: ProjectRec) -> bool:
    if not config.DEDUPE_NAMES:
        return normalize_name(a.name) == normalize_name(b.name)
    if dedupe.same_project_name(a.name, b.name):
        return True
    ua, ub = dedupe.url_key(a.url), dedupe.url_key(b.url)
    return bool(ua) and ua == ub


def same_claim(a: ClaimRec, b: ClaimRec) -> bool:
    """One claim per name; awards (audit round, item 2d): the same award key, or, from different documents, one
    award's key found whole in the other award's text ("… Fellowship" in "Recognized with the … Fellowship for …")."""
    if a.kind == "award" and b.kind == "award" and config.DEDUPE_NAMES:
        ka, kb = dedupe.award_key(a.name_raw), dedupe.award_key(b.name_raw)
        if ka == kb:
            return True
        return a.evidence_id != b.evidence_id and (dedupe.contained(ka, b.name_raw) or dedupe.contained(kb, a.name_raw))
    return claim_key(a) == claim_key(b)


def _hackathon_placement(award: ClaimRec, projects: list[ProjectRec]) -> ProjectRec | None:
    """A hackathon item the award names (audit round, item 2e): "Top 10 Finalist … in CSI National Hackathon"."""
    for p in projects:
        if p.kind != "hackathon" and "hackathon" not in normalize_name(p.name).split():
            continue
        if dedupe.contained(dedupe.award_text(p.name), award.name_raw) and \
                dedupe.award_text(p.name) != dedupe.award_text(award.name_raw):
            return p
    return None


def _placement_text(award: str, event: str) -> str:
    """"Top 10 Finalist from 600+ teams in CSI National Hackathon" → "Top 10 Finalist from 600+ teams"."""
    m = re.search(re.escape(event), award, re.I)
    if not m:
        return award
    before = re.sub(r"[\s,;:–—-]*(?:\b(?:in|at|of|from|for)\b)?[\s,;:–—-]*$", "", award[:m.start()], flags=re.I)
    return before.strip() or award


def _same_certification(a: CertificationRec, b: CertificationRec) -> bool:
    if not config.DEDUPE_NAMES:
        return normalize_name(a.name) == normalize_name(b.name)
    if dedupe.canonical(a.name) != dedupe.canonical(b.name):
        return False
    return not (a.issuer and b.issuer) or dedupe.canonical(a.issuer) == dedupe.canonical(b.issuer)


CONFLICT = "sources_disagree_dates"


def _flag_date_conflict(kept: RoleRec, other: RoleRec) -> None:
    """Two documents give different months for the same role (Review's "Needs your attention"; display only, never
    scored). Code: "sources_disagree_dates|<other evidence id>|<start>|<end or present>"."""
    if not other.evidence_id or other.evidence_id == kept.evidence_id or kept.source == "user_edit":
        return
    def ym(d):
        return d.strftime("%Y-%m") if d else ""
    differ = (kept.start_date and other.start_date and kept.start_date != other.start_date) or \
        (kept.is_current != other.is_current and (kept.end_date or other.end_date)) or \
        (not kept.is_current and not other.is_current and kept.end_date and other.end_date
         and kept.end_date != other.end_date)
    if differ and not any(r.startswith(CONFLICT) for r in kept.review_reasons):
        kept.review_reasons.append(f"{CONFLICT}|{other.evidence_id}|{ym(other.start_date)}|"
                                   f"{'present' if other.is_current else ym(other.end_date)}")


def _claim_strength(c: ClaimRec, artifacts: set) -> int:
    """artifact > bullet > claimed only (specs/19 item 17)."""
    return 2 if c.evidence_id in artifacts else 1 if c.bullet_id else 0


def merge_profiles(profiles: list[Profile], evidence_types: dict[str, str] | None = None
                   ) -> tuple[Profile, ValidationReport]:
    """Merge per-source profiles in order (resume first). Earlier sources win on conflicts. `evidence_types`
    (evidence id → type) marks claims read from a repository or portfolio as backed by an artifact."""
    out, report = Profile(), ValidationReport()
    bullet_remap: dict[str, str] = {}
    role_remap: dict[str, str] = {}
    absorbed: dict[str, set] = {}          # kept role id → evidence ids already merged into it
    artifacts = {e for e, t in (evidence_types or {}).items() if t in ("github_repo", "portfolio")} | \
        {p.evidence_id for prof in profiles for p in prof.projects if p.is_github and p.evidence_id}

    order = {t: i for i, t in enumerate(("resume", "linkedin_pdf", "portfolio", "github_repo"))}

    def rank(eid):
        return order.get((evidence_types or {}).get(eid), len(order)), str(eid)

    for prof in profiles:
        for role in prof.roles:
            role.bullets = _dedupe_own_bullets(role.bullets, bullet_remap)
            bullet_remap.update(_one_source(role, rank, report))
            scored = [(s, r) for r in out.roles
                      if (s := _same_role_score(r, role, absorbed.get(r.id, set()))) is not None]
            match = max(scored, key=lambda x: x[0])[1] if scored else None
            if match is None:
                out.roles.append(role)
                absorbed[role.id] = {role.evidence_id}
                continue
            absorbed.setdefault(match.id, set()).add(role.evidence_id)
            _flag_date_conflict(match, role)
            _fill(match, role, ("employer", "industry", "start_date", "end_date", "team_size", "location"))
            match.is_current = match.is_current or role.is_current
            bullet_remap.update(_take_bullets(match, role, report))
            _add_sources(match, role)
            role_remap[role.id] = match.id
            report.role_remap[role.id] = match.id
            report.merged += 1

        for project in prof.projects:
            project.bullets = _dedupe_own_bullets(project.bullets, bullet_remap)
            bullet_remap.update(_one_source(project, rank, report))
            if project.role_id in role_remap:
                project.role_id = role_remap[project.role_id]
            match = next((p for p in out.projects if _same_project(p, project)), None)
            if match is None:
                out.projects.append(project)
                continue
            _fill(match, project, ("description", "url", "start_date", "end_date", "role_id"))
            if project.is_github and not match.is_github:
                match.is_fork, match.own_commits = project.is_fork, project.own_commits
                if project.end_date and (match.end_date is None or project.end_date > match.end_date):
                    match.end_date = project.end_date
            if config.DEDUPE_NAMES and {match.kind, project.kind} == {"project", "repo"}:
                match.kind = "project"             # a resume project and its repository: one project (item 16)
            match.details = {**project.details, **match.details}
            bullet_remap.update(_take_bullets(match, project, report))
            _add_sources(match, project)
            report.project_remap[project.id] = match.id
            report.merged += 1

        for claim in prof.claims:
            if claim.bullet_id in bullet_remap:
                claim.bullet_id = bullet_remap[claim.bullet_id]
            match = next((c for c in out.claims if same_claim(c, claim)), None)
            if match is None:
                out.claims.append(claim)
                continue
            report.merged += 1
            if claim.kind == "award" and config.DEDUPE_NAMES:
                # the most complete name (with its amount) is kept, with every source
                if dedupe.award_completeness(claim.name_raw) > dedupe.award_completeness(match.name_raw):
                    _add_sources(claim, match)
                    out.claims[out.claims.index(match)] = claim
                else:
                    _add_sources(match, claim)
                continue
            if config.DEDUPE_NAMES:
                stronger = _claim_strength(claim, artifacts) > _claim_strength(match, artifacts)
            else:
                stronger = match.bullet_id is None and claim.bullet_id is not None
            if stronger:                           # keep the strongest evidence link, and every source
                _add_sources(claim, match)
                claim.skill_group = claim.skill_group or match.skill_group
                out.claims[out.claims.index(match)] = claim
            else:
                _add_sources(match, claim)

        for cert in prof.certifications:
            match = next((c for c in out.certifications if _same_certification(c, cert)), None)
            if match is None:
                out.certifications.append(cert)
            else:
                _fill(match, cert, ("issuer", "issued_date", "credential_id", "verify_url"))
                if config.DEDUPE_NAMES and dedupe.exam_code(cert.name) and not dedupe.exam_code(match.name):
                    match.name = cert.name         # keep the exam code for display
                _add_sources(match, cert)
                report.merged += 1

        out.references += prof.references
    if config.DEDUPE_NAMES:
        for claim in [c for c in out.claims if c.kind == "award"]:
            event = _hackathon_placement(claim, out.projects)
            if event is None:
                continue
            if not (event.details or {}).get("placement"):
                event.details = {**(event.details or {}), "placement": _placement_text(claim.name_raw, event.name)}
            _add_sources(event, claim)
            out.claims.remove(claim)
            report.placements.append((claim.name_raw, event.name))
    return out, report


def _dedupe_own_bullets(bullets: list[BulletRec], remap: dict[str, str]) -> list[BulletRec]:
    kept: list[BulletRec] = []
    remap.update(_merge_bullets(kept, bullets))
    return kept
