"""Pure scoring formulas, one function per subcategory (specs/04).

No I/O and no model calls. Inputs carry judge *labels*; this module maps them
to values and applies the formulas. A label of ``None`` means the judgment
failed after retries: the item is skipped, and the subcategory becomes
``error`` only when no usable judgment is left (specs/05 §D).

Status convention: ``missing`` when there is no evidence to judge (score 0,
with guidance); ``scored`` (possibly 0) once evidence was judged. Recognition,
Scope of impact and Validation strength are ``missing`` when nothing qualifies,
as 04 states for them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Literal

from core import config

Status = Literal["scored", "missing", "na", "error"]


@dataclass
class SubScore:
    status: Status
    score: float | None
    details: dict = field(default_factory=dict)
    guidance: str | None = None


# --- Label → value maps (specs/05 judgment catalogue) ----------------------

RELEVANCE = config.RELEVANCE_VALUES
SCOPE = {"meets": 1.0, "partly": 0.5, "below": 0.0}
PROGRESSION = {"more_impactful": 1.0, "same": 0.5, "less": 0.0}
SKILL_EVIDENCE = {"bullet": 1.0, "keyword": 0.5, "missing": 0.0}
APPLIED_LEVEL = {"skill_task_result": 1.0, "skill_task": 0.5, "mention_or_missing": 0.0}
COMPLEXITY = {"meets": 1.0, "partly": 0.5, "no": 0.0}
IMPACT = {"business": 1.0, "operational": 0.5, "none": 0.0}
OWNERSHIP = {"led": 1.0, "contributed": 0.5, "not_stated": 0.0}
REACH = {"own_work": 1, "team": 2, "cross_functional": 3, "organization": 4, "external": 5, "not_stated": None}
PROOF_SUPPORT = {"artifact": 1.0, "bullet": 0.5, "claimed_only": 0.0}


def tool_evidence_values() -> dict[str, float]:
    # Read at call time so tests can change the [OPEN] config value.
    return {"used": 1.0, "certified": config.UNVERIFIED_CERT_TOOL_SCORE, "listed": 0.5, "missing": 0.0}


# Project kinds that count toward Depth's Project_score (04 §2).
DEPTH_PROJECT_KINDS = frozenset({"project", "hackathon", "publication", "case_study", "repo"})
# Project kinds eligible for Recency on entry-level baselines (04 §2, literal list).
RECENCY_PROJECT_KINDS = frozenset({"project", "publication", "hackathon"})
WORK_SAMPLE_KINDS = frozenset({"repo", "portfolio", "presentation", "publication"})
VALIDATING_RELATIONSHIPS = frozenset({"manager", "supervisor"})

GUIDANCE = {
    "relevance": "Add your work experience (roles with titles and dates).",
    "relevance_dates": "Add start and end dates to your roles so their length can be counted.",
    "depth": "Add relevant roles with dates, or projects, hackathons, publications or repositories.",
    "scope": "Add a role related to this position, describing what you were responsible for.",
    "recency": "Add recent relevant roles or projects with dates.",
    "complexity": "Add a relevant role or project describing the problem, its constraints and how you solved it.",
    "work_samples": "Add a GitHub profile, a portfolio, or links to presentations or publications.",
    "outcomes": "Add bullets that state a measurable result (a number and a direction, e.g. 'cut runtime 40%').",
    "impact": "Add bullets that describe the business or operational effect of your work.",
    "ownership": "Add bullets that state your own role in the work (what you led or contributed).",
    "scope_of_impact": "Add bullets that say who your work reached (your team, other teams, the organization, customers).",
    "recognition": "Add role-relevant awards or publications.",
    "proof_density": "List the skills and tools you use, and link artifacts that show them.",
    "validation_strength": "Add a reference from a manager or supervisor, or a certification with a credential ID or verification link.",
    "consistency": "Add a LinkedIn PDF, GitHub profile or portfolio so your resume can be cross-checked.",
    "evidence_quality": "Add roles or projects with details: tools, numbers, dates, context and your own role.",
}


# --- Small helpers ------------------------------------------------------------

def _scored(score: float, **details) -> SubScore:
    return SubScore("scored", float(score), details)


def _missing(key: str, **details) -> SubScore:
    return SubScore("missing", 0.0, details, GUIDANCE[key])


def _na(reason: str, **details) -> SubScore:
    return SubScore("na", None, {"reason": reason, **details})


def _error(reason: str, **details) -> SubScore:
    return SubScore("error", None, {"reason": reason, **details})


def _value(mapping: dict, label: str | None, question: str):
    """Map a label to its value. None (failed judgment) stays None; unknown labels raise."""
    if label is None:
        return None
    if label not in mapping:
        raise ValueError(f"unknown {question} label {label!r}; expected one of {sorted(mapping)}")
    return mapping[label]


def _unique(items: list) -> list:
    """Drop repeated items by id (first wins) so the same item never counts twice."""
    seen: set[str] = set()
    out = []
    for item in items:
        if item.id not in seen:
            seen.add(item.id)
            out.append(item)
    return out


def _today(today: date | None) -> date:
    return today or config.today()


def month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


# --- Inputs ---------------------------------------------------------------------

@dataclass(frozen=True)
class RoleIn:
    id: str
    start: date | None
    end: date | None
    is_current: bool = False
    relevance: str | None = None  # relevance label for the current baseline


@dataclass(frozen=True)
class ProjectIn:
    id: str
    kind: str
    relevance: str | None = None
    start: date | None = None
    end: date | None = None
    is_fork: bool = False
    own_commits: int | None = None

    @property
    def counts_as_own_work(self) -> bool:
        # Forks with no commits by the user are not their work (01, GitHub).
        return not (self.kind == "repo" and self.is_fork and not self.own_commits)


@dataclass(frozen=True)
class GradedItem:
    """A role or project with its relevance and one graded label (e.g. complexity)."""
    id: str
    relevance: str | None
    label: str | None


@dataclass(frozen=True)
class WorkSampleIn:
    id: str
    kind: str              # repo | portfolio | presentation | publication
    relevant: bool | None  # work_sample_relevant judgment
    counted: bool = True   # repos: False for forks with no own commits


@dataclass(frozen=True)
class BulletIn:
    id: str
    relevance: str | None              # inherited from the parent role/project
    has_metric: bool | None = None
    has_direction: bool | None = None
    impact: str | None = None
    ownership: str | None = None
    reach: str | None = None


@dataclass(frozen=True)
class ReferenceIn:
    id: str
    relationship: str | None
    confirms_relevant: bool | None


@dataclass(frozen=True)
class CertificationIn:
    id: str
    verified: bool
    matched_criterion: str | None = None  # from scoring.matching.match_certification
    judged_relevant: bool | None = None   # reserved for the proposed certification_relevant judge

    @property
    def relevant(self) -> bool:
        return self.matched_criterion is not None or self.judged_relevant is True

    @property
    def match_status(self) -> str:
        if self.matched_criterion is not None:
            return "matched"
        if self.judged_relevant is not None:
            return "judged_relevant" if self.judged_relevant else "judged_not_relevant"
        return "not_matched"


@dataclass(frozen=True)
class SourceIn:
    id: str
    type: str              # linkedin_pdf | github | portfolio | work_sample
    aligned: bool | None


@dataclass(frozen=True)
class QualityIn:
    id: str
    checks: tuple[bool | None, ...]  # tools, numbers, dates, context, own role


# --- Date math ------------------------------------------------------------------

def role_span(role: RoleIn, today: date | None = None) -> tuple[int, int] | None:
    """Inclusive (start, end) month indexes, or None when dates are unusable.

    Current roles end today. A role with no end that is not current ends at its
    start (validation flags it for review). Future or reversed dates are excluded
    from date math (01 edge cases).
    """
    if role.start is None:
        return None
    now = month_index(_today(today))
    start = month_index(role.start)
    if role.is_current:
        end = now
    elif role.end is not None:
        end = month_index(role.end)
    else:
        end = start
    if start > now or end > now or end < start:
        return None
    return start, end


def role_months(role: RoleIn, today: date | None = None) -> int | None:
    span = role_span(role, today)
    return None if span is None else span[1] - span[0] + 1


def _activity_end(start: date | None, end: date | None, is_current: bool, today: date) -> int | None:
    """Month index an activity ended (today if ongoing); None if undated or in the future."""
    now = month_index(today)
    if is_current:
        idx = now
    elif end is not None:
        idx = month_index(end)
    elif start is not None:
        idx = month_index(start)
    else:
        return None
    if idx > now or (start is not None and month_index(start) > now):
        return None
    return idx


def is_entry_level(required_years: float) -> bool:
    return required_years <= config.ENTRY_LEVEL_MAX_YEARS


# --- Experience -------------------------------------------------------------------

def relevance(roles: list[RoleIn], today: date | None = None) -> SubScore:
    """Σ(months × relevance) / Σ months over roles."""
    roles = _unique(roles)
    if not roles:
        if config.NO_ROLES_RELEVANCE_SCOPE_NA:
            return _na("no roles (NO_ROLES_RELEVANCE_SCOPE_NA)")
        return _missing("relevance")
    today = _today(today)
    rows, num, den = [], 0.0, 0
    for r in roles:
        rel = _value(RELEVANCE, r.relevance, "relevance")
        months = role_months(r, today)
        rows.append({"role_id": r.id, "months": months, "relevance": rel})
        if rel is None or months is None:
            continue
        num += months * rel
        den += months
    if all(r.relevance is None for r in roles):
        return _error("all relevance judgments failed", roles=rows)
    if den == 0:
        return _missing("relevance_dates", roles=rows)
    return _scored(num / den, roles=rows, total_months=den)


def depth(
    roles: list[RoleIn],
    projects: list[ProjectIn],
    required_years: float,
    today: date | None = None,
) -> SubScore:
    """Work_score from a de-duplicated monthly timeline; entry level also counts projects."""
    today = _today(today)
    entry = is_entry_level(required_years)
    roles, projects = _unique(roles), _unique(projects)

    timeline: dict[int, float] = {}
    for r in roles:
        rel = _value(RELEVANCE, r.relevance, "relevance")
        span = role_span(r, today)
        if rel is None or span is None:
            continue
        for m in range(span[0], span[1] + 1):
            timeline[m] = max(timeline.get(m, 0.0), rel)  # overlapping months counted once

    relevant_years = sum(timeline.values()) / 12
    required = max(1.0, required_years)
    work_score = min(1.0, relevant_years / required)

    counted = [p for p in projects if p.kind in DEPTH_PROJECT_KINDS and p.counts_as_own_work]
    project_rels = [_value(RELEVANCE, p.relevance, "relevance") for p in counted]
    usable_projects = [v for v in project_rels if v is not None]
    project_score = min(1.0, sum(usable_projects) / config.PROJECT_TARGET)

    details = {
        "entry_level": entry,
        "relevant_years": relevant_years,
        "required_years": required,
        "work_score": work_score,
        "project_score": project_score if entry else None,
        "counted_project_ids": [p.id for p in counted] if entry else [],
    }
    pool_roles = roles
    pool_projects = counted if entry else []
    if not pool_roles and not pool_projects:
        return _missing("depth", **details)
    judged = [r.relevance for r in pool_roles] + [p.relevance for p in pool_projects]
    if all(label is None for label in judged):
        return _error("all relevance judgments failed", **details)
    has_work = bool(timeline)
    has_projects = bool(usable_projects)
    if not has_work and not has_projects:
        return _missing("depth", **details)
    score = max(work_score, project_score) if entry else work_score
    return _scored(score, **details)


def select_scope_role(roles: list[RoleIn], today: date | None = None) -> RoleIn | None:
    """Most recent role with relevance >= 0.5, preferring `relevant` over `adjacent`."""
    today = _today(today)

    def recency_key(r: RoleIn):
        span = role_span(r, today)
        return (span[1], span[0]) if span else (float("-inf"), float("-inf"))

    for wanted in ("relevant", "adjacent"):
        matches = [r for r in roles if r.relevance == wanted]
        if matches:
            return max(matches, key=recency_key)
    return None


def scope(role: RoleIn | None, label: str | None, has_roles: bool = True) -> SubScore:
    if role is None:
        if not has_roles and config.NO_ROLES_RELEVANCE_SCOPE_NA:
            return _na("no roles (NO_ROLES_RELEVANCE_SCOPE_NA)")
        return _missing("scope")
    value = _value(SCOPE, label, "scope_vs_baseline")
    if value is None:
        return _error("scope judgment failed", role_id=role.id)
    return _scored(value, role_id=role.id, label=label)


def progression(n_roles: int, label: str | None) -> SubScore:
    if n_roles < 2:
        return _na("fewer than 2 roles", n_roles=n_roles)
    value = _value(PROGRESSION, label, "progression")
    if value is None:
        return _error("progression judgment failed")
    return _scored(value, label=label, n_roles=n_roles)


def recency(
    roles: list[RoleIn],
    projects: list[ProjectIn],
    required_years: float,
    today: date | None = None,
) -> SubScore:
    """max(relevance × DECAY ^ gap_years) over eligible activities with relevance > 0."""
    today = _today(today)
    entry = is_entry_level(required_years)
    # End month is None for undated or future-dated activities; they are skipped below.
    eligible: list[tuple[str, str, str | None, int | None]] = [
        ("role", r.id, r.relevance, _activity_end(r.start, r.end, r.is_current, today)) for r in roles
    ]
    if entry:
        eligible += [
            ("project", p.id, p.relevance, _activity_end(p.start, p.end, False, today))
            for p in projects
            if p.kind in RECENCY_PROJECT_KINDS and p.counts_as_own_work
        ]
    if not eligible:
        return _missing("recency", entry_level=entry)
    if all(label is None for _, _, label, _ in eligible):
        return _error("all relevance judgments failed", entry_level=entry)

    now = month_index(today)
    activities, any_dated = [], False
    for kind, item_id, label, end_idx in eligible:
        rel = _value(RELEVANCE, label, "relevance")
        if rel is None or end_idx is None:
            continue
        any_dated = True
        gap_years = max(0, now - end_idx) / 12
        activities.append({
            "kind": kind, "id": item_id, "relevance": rel, "gap_years": gap_years,
            "activity": rel * config.DECAY ** gap_years,
        })
    if not any_dated:
        return _missing("recency", entry_level=entry)
    scoring = [a for a in activities if a["relevance"] > 0]
    if not scoring:
        return _scored(0.0, entry_level=entry, activities=activities, best=None)
    best = max(scoring, key=lambda a: a["activity"])
    return _scored(best["activity"], entry_level=entry, activities=activities, best=best["id"])


# --- Expertise --------------------------------------------------------------------

def combine_required_preferred(r: float | None, p: float | None) -> float | None:
    """(2R + P) / 3, falling back to whichever side exists (04 §1)."""
    if r is None and p is None:
        return None
    if p is None:
        return r
    if r is None:
        return p
    return (2 * r + p) / 3


def _criteria_score(
    required: dict[str, str | None],
    preferred: dict[str, str | None],
    values: dict[str, float],
    question: str,
) -> SubScore:
    """Per-criterion labels → R and P averages → combined."""

    def group(labels: dict[str, str | None]):
        per = {name: _value(values, label, question) for name, label in labels.items()}
        usable = [v for v in per.values() if v is not None]
        avg = sum(usable) / len(usable) if usable else None
        failed = [name for name, v in per.items() if v is None]
        return avg, per, failed

    r, r_items, r_failed = group(required)
    p, p_items, p_failed = group(preferred)
    details = {
        "required": {"score": r, "items": {k: required[k] for k in required}, "failed": r_failed},
        "preferred": {"score": p, "items": {k: preferred[k] for k in preferred}, "failed": p_failed},
    }
    if not required and not preferred:
        return _na("baseline has no criteria of this kind", **details)
    combined = combine_required_preferred(r, p)
    if combined is None:
        return _error(f"all {question} judgments failed", **details)
    return _scored(combined, **details)


def role_skills(required: dict[str, str | None], preferred: dict[str, str | None]) -> SubScore:
    return _criteria_score(required, preferred, SKILL_EVIDENCE, "skill_evidence")


def applied_knowledge(required: dict[str, str | None], preferred: dict[str, str | None]) -> SubScore:
    return _criteria_score(required, preferred, APPLIED_LEVEL, "applied_level")


def tools(required: dict[str, str | None], preferred: dict[str, str | None]) -> SubScore:
    return _criteria_score(required, preferred, tool_evidence_values(), "tool_evidence")


def complexity(items: list[GradedItem]) -> SubScore:
    """max graded complexity over roles/projects with relevance > 0."""
    if items and all(i.relevance is None for i in items):
        return _error("all relevance judgments failed")
    relevant = [i for i in items if (_value(RELEVANCE, i.relevance, "relevance") or 0) > 0]
    if not relevant:
        return _missing("complexity")
    graded = [(i.id, _value(COMPLEXITY, i.label, "complexity"), i.label) for i in relevant]
    usable = [g for g in graded if g[1] is not None]
    rows = [{"id": i, "label": label} for i, _, label in graded]
    if not usable:
        return _error("all complexity judgments failed", items=rows)
    best = max(usable, key=lambda g: g[1])
    return _scored(best[1], items=rows, best=best[0])


def work_samples(samples: list[WorkSampleIn]) -> SubScore:
    """1 if any counted work sample is relevant, else 0."""
    for s in samples:
        if s.kind not in WORK_SAMPLE_KINDS:
            raise ValueError(f"unknown work sample kind {s.kind!r}")
    counted = [s for s in samples if s.counted]
    if not counted:
        return _missing("work_samples")
    rows = [{"id": s.id, "kind": s.kind, "relevant": s.relevant} for s in counted]
    if all(s.relevant is None for s in counted):
        return _error("all work sample judgments failed", samples=rows)
    found = sorted({s.kind for s in counted if s.relevant})
    return _scored(1.0 if found else 0.0, samples=rows, relevant_kinds=found)


# --- Authority -------------------------------------------------------------------

def _best_bullet(bullets: list[BulletIn], level_of, key: str) -> SubScore:
    """max(level × inherited relevance) over bullets."""
    if not bullets:
        return _missing(key)
    rows = []
    for b in bullets:
        rel = _value(RELEVANCE, b.relevance, "relevance")
        level = level_of(b)
        if rel is None or level is None:
            continue
        rows.append({"bullet_id": b.id, "level": level, "relevance": rel, "value": level * rel})
    if not rows:
        return _error(f"all {key} judgments failed")
    best = max(rows, key=lambda r: r["value"])
    return _scored(best["value"], best=best, judged=len(rows))


def _outcome_level(b: BulletIn) -> float | None:
    if b.has_metric is None or b.has_direction is None:
        return None
    return {2: 1.0, 1: 0.5, 0: 0.0}[int(b.has_metric) + int(b.has_direction)]


def outcomes(bullets: list[BulletIn]) -> SubScore:
    return _best_bullet(bullets, _outcome_level, "outcomes")


def impact(bullets: list[BulletIn]) -> SubScore:
    return _best_bullet(bullets, lambda b: _value(IMPACT, b.impact, "impact_type"), "impact")


def ownership(bullets: list[BulletIn]) -> SubScore:
    return _best_bullet(bullets, lambda b: _value(OWNERSHIP, b.ownership, "ownership"), "ownership")


def scope_of_impact(bullets: list[BulletIn], expected_reach: int) -> SubScore:
    """Compare the max reach over relevant bullets with the baseline's expected reach."""
    if not bullets:
        return _missing("scope_of_impact", expected_reach=expected_reach)
    # With the [OPEN] relevance filter off, bullets with a failed relevance judgment still count.
    judged = [b for b in bullets if b.reach is not None
              and (b.relevance is not None or not config.SCOPE_OF_IMPACT_RELEVANCE_FILTER)]
    if not judged:
        return _error("all reach judgments failed", expected_reach=expected_reach)
    use_filter = config.SCOPE_OF_IMPACT_RELEVANCE_FILTER
    reached = [
        (b.id, _value(REACH, b.reach, "reach")) for b in judged
        if (not use_filter or _value(RELEVANCE, b.relevance, "relevance") > 0)
        and _value(REACH, b.reach, "reach") is not None
    ]
    if not reached:
        return _missing("scope_of_impact", expected_reach=expected_reach)
    best_id, candidate_reach = max(reached, key=lambda x: x[1])
    gap = expected_reach - candidate_reach
    score = 1.0 if gap <= 0 else 0.5 if gap == 1 else 0.0
    return _scored(score, expected_reach=expected_reach, candidate_reach=candidate_reach, gap=gap, best=best_id)


def recognition(awards: list[bool | None], publications: list[bool | None]) -> SubScore:
    """(relevant award + relevant publication) / 2; missing when neither is present."""
    if (awards or publications) and all(x is None for x in [*awards, *publications]):
        return _error("all recognition judgments failed")
    award = any(a is True for a in awards)
    publication = any(p is True for p in publications)
    details = {"award": award, "publication": publication,
               "awards_judged": len(awards), "publications_judged": len(publications)}
    if not award and not publication:
        return _missing("recognition", **details)
    return _scored((award + publication) / 2, **details)


# --- Trust ------------------------------------------------------------------------

def proof_density(claims: Iterable[tuple[str, str | None]]) -> SubScore:
    """Average best support per distinct claim (deduplicated by name_norm)."""
    best: dict[str, float | None] = {}
    for name, label in claims:
        value = _value(PROOF_SUPPORT, label, "proof_support")
        prev = best.get(name)
        if name not in best or (value is not None and (prev is None or value > prev)):
            best[name] = value
    if not best:
        return _missing("proof_density")
    usable = {k: v for k, v in best.items() if v is not None}
    if not usable:
        return _error("all proof_support judgments failed", claims=best)
    return _scored(sum(usable.values()) / len(usable), claims=best, n_claims=len(usable))


def validation_strength(references: list[ReferenceIn], certifications: list[CertificationIn]) -> SubScore:
    """1 for a manager/supervisor reference confirming relevant work; 0.5 for a verified relevant cert."""
    if not references and not certifications:
        return _missing("validation_strength")
    if not certifications and all(r.confirms_relevant is None for r in references):
        return _error("all reference_confirms judgments failed")
    ref = next((r for r in references
                if (r.relationship or "").lower() in VALIDATING_RELATIONSHIPS and r.confirms_relevant), None)
    cert = next((c for c in certifications if c.verified and c.relevant), None)
    details = {
        "reference_id": ref.id if ref else None,
        "certification_id": cert.id if cert else None,
        "certifications": [
            {"id": c.id, "verified": c.verified, "match": c.match_status, "criterion": c.matched_criterion}
            for c in certifications
        ],
    }
    if ref:
        return _scored(1.0, level="reference", **details)
    if cert:
        return _scored(0.5, level="verified_certification", **details)
    return _missing("validation_strength", **details)


def consistency(sources: list[SourceIn]) -> SubScore:
    """1 if any non-resume source aligns with the resume, else 0."""
    if not sources:
        return _missing("consistency")
    rows = [{"id": s.id, "type": s.type, "aligned": s.aligned} for s in sources]
    if all(s.aligned is None for s in sources):
        return _error("all source_alignment judgments failed", sources=rows)
    return _scored(1.0 if any(s.aligned for s in sources) else 0.0, sources=rows)


def evidence_quality(items: list[QualityIn]) -> SubScore:
    """Average over roles/projects of (yes checks / 5)."""
    if not items:
        return _missing("evidence_quality")
    for i in items:
        if len(i.checks) != 5:
            raise ValueError(f"evidence quality needs 5 checks, got {len(i.checks)} for {i.id}")
    usable = {i.id: sum(bool(c) for c in i.checks) / 5 for i in items if all(c is not None for c in i.checks)}
    if not usable:
        return _error("all quality checks failed")
    return _scored(sum(usable.values()) / len(usable), items=usable)
