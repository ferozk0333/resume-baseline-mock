"""Shared pieces for graph nodes: dependencies, per-node DB context, and judgment execution.

Large data (profile, baseline, judgments) is read from SQLite by candidate_id /
run_id, not carried in graph state (specs/02). Each node opens its own SQLite
connection so parallel nodes never share one.
"""
from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date

from core import config, db, timing
from core.models import Baseline
from extraction import store
from extraction.records import Profile, ProjectRec, RoleRec
from judges.base import Judge, Judgment, render_context
from judges.questions import QUESTIONS
from judges.service import JudgmentService
from scoring import formulas as f


@dataclass
class Deps:
    """Everything the graph needs from outside. Tests pass FakeJudge / FakeLLM here."""
    db_path: str
    judge: Judge
    llm: object | None = None                 # StructuredLLM for extraction
    github_client: object | None = None
    portfolio_session: object | None = None
    today: date | None = None
    max_parallel_calls: int = config.JUDGE_CONCURRENCY    # shared by every subgraph of a run
    claude_judge: Judge | None = None         # for asks that need a quote; default: the judge's Claude fallback
    planner: object | None = None             # StructuredLLM for the improvement plan; None = ranked gaps only
    writer: object | None = None              # StructuredLLM for recruiter email drafts; None = code templates
    job_llm: object | None = None             # StructuredLLM for reading job descriptions; None = manual form
    plan_in_background: bool = True           # return the score first, write the plan after (specs/09 §2.6)
    extraction_cache: bool = False            # reuse saved extraction outputs for identical documents (app: on)
    labeller: object | None = None            # Jev for code-first extraction (specs/18 §4); None = default
    on_source_ready: object | None = None     # (candidate_id, doc_type) after each source is saved (early start)
    _sem: threading.BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self):
        self._sem = threading.BoundedSemaphore(self.max_parallel_calls)
        if self.claude_judge is None:
            self.claude_judge = getattr(self.judge, "fallback", self.judge)

    def day(self) -> date:
        return self.today or config.today()


@dataclass(frozen=True)
class Ask:
    """One judgment a subcategory needs. With rule_label set, code decides and no model is called."""
    key: str
    subject_type: str
    subject_id: str
    context: dict
    rule_label: str | None = None
    rule_reason: str | None = None
    route: str | None = None          # "claude": skip Jev (a quote is needed to explain the label)
    require_quote: bool = False       # labels above the none-option need a quote found in the input


class Labels:
    """Judgments resolved for a list of Asks, looked up by (question, subject_id)."""

    def __init__(self, found: dict[tuple[str, str], Judgment | None], asks: list[Ask]):
        self.found = found
        self.asks = asks

    def judgment(self, key: str, subject_id: str) -> Judgment | None:
        return self.found.get((key, subject_id))

    def get(self, key: str, subject_id: str) -> str | None:
        j = self.judgment(key, subject_id)
        return j.label if j else None

    def yes(self, key: str, subject_id: str) -> bool | None:
        label = self.get(key, subject_id)
        return None if label is None else label == "yes"

    def trail(self) -> list[dict]:
        """Every judgment behind a score, for details JSON and the UI (CLAUDE.md: every score is explainable).

        `subject` is a short excerpt of what was judged: shown as evidence when a judge returns no quote (Jev).
        """
        out = []
        for a in self.asks:
            j = self.found.get((a.key, a.subject_id))
            out.append({
                "question": a.key, "subject_type": a.subject_type, "subject_id": a.subject_id,
                "subject": subject_excerpt(a),
                "label": j.label if j else None, "method": j.method if j else None,
                "model_version": j.model_version if j else None, "quote": j.quote if j else None,
                "confidence": j.confidence if j else None, "reasoning": j.reasoning if j else None,
            })
        return out


_SUBJECT_KEYS = ("bullet", "role", "project", "item", "work_sample", "claim", "award", "publication",
                 "reference", "criterion", "source", "roles")


def subject_excerpt(a: "Ask", limit: int = 200) -> str:
    for k in _SUBJECT_KEYS:
        if k in a.context:
            v = a.context[k]
            text = v if isinstance(v, str) else " | ".join(map(str, v)) if isinstance(v, list) else str(v)
            text = text.split("\n")[0] if k in ("role", "project", "item", "criterion") else text
            if len(text) <= limit:
                return text
            cut = text[:limit].rsplit(" ", 1)[0]          # never mid-word (audit round, item 1)
            return cut.rstrip(" ,;:–—-") + "…"
    return ""


@dataclass
class Ctx:
    conn: object
    deps: Deps
    svc: JudgmentService
    candidate_id: str
    run_id: str | None
    baseline: Baseline
    profile: Profile
    today: date
    evidence: dict[str, dict]                         # evidence_id → row
    relevance: dict[str, str | None] = field(default_factory=dict)   # role/project id → label

    @property
    def entry(self) -> bool:
        return f.is_entry_level(self.baseline.required_years)

    @property
    def baseline_summary(self) -> str:
        b = self.baseline
        return f"{b.title} ({b.level}). {b.description or ''}".strip()

    def counted_projects(self) -> list[ProjectRec]:
        """Projects that are the candidate's own work (forks with no own commits excluded)."""
        return [p for p in self.profile.projects if not (p.is_fork and not p.own_commits)]

    def evidence_type(self, evidence_id: str | None) -> str | None:
        row = self.evidence.get(evidence_id or "")
        return row["type"] if row else None

    def from_portfolio(self, item) -> bool:
        """Read from a portfolio, itself or merged into it (a resume project and its case study are one item)."""
        return any(self.evidence_type(e) == "portfolio" for e in [item.evidence_id, *(item.sources or [])])

    def role_ins(self) -> list[f.RoleIn]:
        return [f.RoleIn(r.id, r.start_date, r.end_date, r.is_current, self.relevance.get(r.id))
                for r in self.profile.roles]

    def project_ins(self) -> list[f.ProjectIn]:
        return [f.ProjectIn(p.id, "repo" if p.kind == "repo" else p.kind, self.relevance.get(p.id),
                            p.start_date, p.end_date, bool(p.is_fork), p.own_commits)
                for p in self.profile.projects]

    def parent_relevance(self) -> dict[str, str | None]:
        """bullet id → relevance label of its role/project (bullets inherit relevance, 04 §1)."""
        out = {}
        for item in [*self.profile.roles, *self.counted_projects()]:
            for b in item.bullets:
                out[b.id] = self.relevance.get(item.id)
        return out


# --- Text for judge contexts -------------------------------------------------------------

def _dates(start: date | None, end: date | None, current: bool = False) -> str:
    s = start.strftime("%Y-%m") if start else "?"
    e = "present" if current else (end.strftime("%Y-%m") if end else "?")
    return f"{s} to {e}" if (start or end or current) else ""


def role_text(r: RoleRec) -> str:
    head = f"{r.title} at {r.employer}" if r.employer else r.title
    meta = [m for m in (r.industry, _dates(r.start_date, r.end_date, r.is_current),
                        f"team size {r.team_size}" if r.team_size else None) if m]
    lines = [head + (f" ({'; '.join(meta)})" if meta else "")]
    lines += [f"- {b.text}" for b in r.bullets]
    return "\n".join(lines)


def project_text(p: ProjectRec) -> str:
    meta = [m for m in (p.kind.replace("_", " "), _dates(p.start_date, p.end_date)) if m]
    head = f"{p.name} ({'; '.join(meta)})"
    lines = [head + (f": {p.description}" if p.description else "")]
    lines += [f"- {b.text}" for b in p.bullets]
    return "\n".join(lines)


# --- Context loading ----------------------------------------------------------------------

@contextmanager
def open_ctx(deps: Deps, state: dict, *, with_relevance: bool = True):
    conn = db.connect(deps.db_path)
    try:
        baseline = db.get_baseline(conn, state["baseline_id"])
        if baseline is None:
            raise ValueError(f"baseline {state['baseline_id']!r} not found")
        candidate_id = state["candidate_id"]
        evidence = {e["id"]: e for e in store.list_evidence(conn, candidate_id)}
        profile = store.load_profile(conn, candidate_id)
        if config.REPO_CAP:                               # judge at most MAX_REPOS repositories (specs/18 item 11)
            profile = _capped(profile, baseline, evidence)
        ctx = Ctx(conn=conn, deps=deps, svc=JudgmentService(conn, deps.judge, candidate_id, state.get("run_id")),
                  candidate_id=candidate_id, run_id=state.get("run_id"), baseline=baseline,
                  profile=profile, today=deps.day(), evidence=evidence)
        if with_relevance:
            from graph.nodes.prepare import relevance_asks
            labels = resolve(ctx, relevance_asks(ctx), call=False)
            ctx.relevance = {a.subject_id: labels.get("relevance", a.subject_id) for a in labels.asks}
        yield ctx
    finally:
        conn.close()


def _capped(profile: Profile, baseline: Baseline, evidence: dict) -> Profile:
    """The profile as judged under REPO_CAP: repositories beyond MAX_REPOS (by relevance to this role, then latest
    push) are left out; they stay listed on the saved profile."""
    from dataclasses import replace
    from extraction.repo_cap import keep
    repos = [p for p in profile.projects if p.is_github and not (p.is_fork and not p.own_commits)]
    if len(repos) <= config.MAX_REPOS:
        return profile
    items = [(p.id, " ".join([p.name, p.description or "", *(b.text for b in p.bullets),
                              (evidence.get(p.evidence_id) or {}).get("raw_text") or ""]),
              p.end_date.isoformat() if p.end_date else None, 0) for p in repos]
    kept = keep(items, baseline)
    return replace(profile, projects=[p for p in profile.projects if not p.is_github or p.id in kept
                                      or (p.is_fork and not p.own_commits)])


# Question families whose unmatched criteria go to Claude in one call per candidate and baseline (specs/09 §2.2).
BATCH_KEYS = {"skill_evidence", "applied_level", "tool_evidence"}
# GROUPED_ITEM_JUDGING (specs/18 item 10, narrowed in review): complexity in one call over all items, same schema, a
# quote required above the lowest label, each failed item retried alone, reasons first. Work-sample relevance stays on
# Jev (multi-question requests) with its fallback.
ITEM_BATCH_KEYS = {"complexity"}


def _batched(ctx: Ctx, asks: list[Ask]) -> dict[tuple[str, str], Judgment | None]:
    """Judge the not-yet-saved Claude-routed criteria of each batchable question in one call."""
    groups: dict[str, list[Ask]] = {}
    for a in asks:
        if a.key in BATCH_KEYS and a.route == "claude" and a.require_quote and a.rule_label is None:
            groups.setdefault(a.key, []).append(a)
        elif config.GROUPED_ITEM_JUDGING and a.key in ITEM_BATCH_KEYS and a.rule_label is None:
            groups.setdefault(a.key, []).append(a)
    found: dict[tuple[str, str], Judgment | None] = {}
    judge = ctx.deps.claude_judge
    if not hasattr(judge, "batch"):
        return found
    if config.PARTITIONED_BATCHES:                   # one batch per question and source document (Phase 3 review 2b)
        split: dict[str, list[Ask]] = {}
        for key, group in groups.items():
            for a in group:
                source = a.subject_id.split("#all@", 1)[1] if "#all@" in a.subject_id else ""
                split.setdefault(f"{key}\x00{source}", []).append(a)
        groups = split
    for gkey, group in groups.items():
        key = gkey.split("\x00", 1)[0]
        baseline = ctx.baseline if QUESTIONS[key].job_dependent else None
        misses = [a for a in group if ctx.svc.cached(a.key, a.subject_id, a.context, baseline, judge) is None]
        if len(misses) < 2:
            continue
        with ctx.deps._sem:
            got = ctx.svc.ask_batch(key, misses[0].subject_type, [(a.subject_id, a.context) for a in misses],
                                    baseline, judge)
        found.update({(key, sid): j for sid, j in got.items()})
    return found


def _grouped(ctx: Ctx, asks: list[Ask]) -> dict[tuple[str, str], Judgment | None]:
    """Questions about the same subject with the same context, asked in one judge call (specs/09 §2.3)."""
    judge = ctx.deps.judge
    if not hasattr(judge, "group"):
        return {}
    only = getattr(judge, "CLAUDE_ONLY", set())
    groups: dict[tuple[str, str], list[Ask]] = {}
    for a in asks:
        if a.rule_label is None and a.route is None and a.key not in only:
            groups.setdefault((a.subject_id, render_context(a.context)), []).append(a)
    work = []
    for group in groups.values():
        misses = []
        for a in group:
            baseline = ctx.baseline if QUESTIONS[a.key].job_dependent else None
            if ctx.svc.cached(a.key, a.subject_id, a.context, baseline) is None:
                misses.append(a)
        if len(misses) >= 2 and len({QUESTIONS[a.key].job_dependent for a in misses}) == 1:
            work.append(misses)

    def run(misses: list[Ask]) -> dict:
        a = misses[0]
        baseline = ctx.baseline if QUESTIONS[a.key].job_dependent else None
        with ctx.deps._sem:
            got = ctx.svc.ask_group([m.key for m in misses], a.subject_type, a.subject_id, a.context, baseline)
        return {(k, a.subject_id): j for k, j in got.items()}

    found: dict[tuple[str, str], Judgment | None] = {}
    if len(work) > 1:
        with ThreadPoolExecutor(max_workers=ctx.deps.max_parallel_calls) as pool:
            for part in pool.map(timing.bind(run), work):
                found.update(part)
    else:
        for misses in work:
            found.update(run(misses))
    return found


def resolve(ctx: Ctx, asks: list[Ask], *, call: bool) -> Labels:
    """call=True: judge (cache first, parallel, bounded). call=False: read saved judgments only, never a model."""
    batched = {**_batched(ctx, asks), **_grouped(ctx, asks)} if call else {}

    def one(a: Ask) -> tuple[tuple[str, str], Judgment | None]:
        baseline = ctx.baseline if QUESTIONS[a.key].job_dependent else None
        if a.rule_label is not None:
            if call:
                return (a.key, a.subject_id), ctx.svc.rule(a.key, a.subject_type, a.subject_id, a.rule_label,
                                                           a.context, baseline, a.rule_reason)
            return (a.key, a.subject_id), Judgment(label=a.rule_label, reasoning=a.rule_reason, method="rule",
                                                   model_version="rule")
        if (a.key, a.subject_id) in batched:
            return (a.key, a.subject_id), batched[(a.key, a.subject_id)]
        judge = ctx.deps.claude_judge if a.route == "claude" else None
        if not call:
            return (a.key, a.subject_id), ctx.svc.cached(a.key, a.subject_id, a.context, baseline, judge)
        with ctx.deps._sem:
            return (a.key, a.subject_id), ctx.svc.ask(a.key, a.subject_type, a.subject_id, a.context, baseline,
                                                      judge=judge, require_quote=a.require_quote)

    if call and len(asks) > 1:
        with ThreadPoolExecutor(max_workers=ctx.deps.max_parallel_calls) as pool:
            found = dict(pool.map(timing.bind(one), asks))
    else:
        found = dict(map(one, asks))
    return Labels(found, asks)


def evidence_json(ctx: Ctx, evidence_id: str) -> dict:
    raw = (ctx.evidence.get(evidence_id) or {}).get("extracted_json")
    return json.loads(raw) if raw else {}
