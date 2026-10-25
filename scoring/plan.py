"""Improvement plan (specs/05 §C): actions that build NEW evidence for the top gaps.

The model returns a structured Plan. Code validates it against the gaps and the
baseline, computes each action's "up to N points if fully closed" from the gap
gains (04 §7), orders the actions and renders the Markdown. The validated plan
is stored per run; a weight change re-prices and re-orders it without a model call.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from core import config
from core.db import new_id, now_iso
from providers.llm import LLMError, StructuredLLM
from core.models import Baseline
from core.text import contains_phrase, normalize_name, wrap_tag
from extraction.extract import load_prompt
from extraction.records import Profile
from scoring.aggregate import aggregate
from scoring.formulas import is_entry_level
from scoring.gaps import Gap, rank_gaps

log = logging.getLogger(__name__)

ActionType = Literal["build_project", "earn_certification", "get_reference", "publish_work", "contribute_or_compete"]

TYPE_LABELS = {
    "build_project": "Build a project",
    "earn_certification": "Earn a certification",
    "get_reference": "Get a reference",
    "publish_work": "Publish existing work",
    "contribute_or_compete": "Contribute or compete",
}


# --- Model output ---------------------------------------------------------------------------

class Action(BaseModel):
    type: ActionType
    title: str
    what_to_do: str = Field(description="2-4 sentences.")
    must_show: list[str] = Field(description="What the finished evidence must show.")
    addresses: list[str] = Field(description="Subcategory keys, only from the gaps given.")
    covers_criteria: list[str] = Field(description="Baseline skill/tool/certification names, exactly as listed.")
    builds_on: str | None = Field(description="What the candidate already has that this builds on.")


class Plan(BaseModel):
    actions: list[Action] = Field(description="3-5 actions, most valuable gaps first.")
    quick_wins: list[str] = Field(description="At most 3 short items.")
    not_actionable: list[str] = Field(description="Gaps no near-term action closes, one line each.")


# --- Which action types can earn points where (from what the formulas count as evidence) ------

def type_fit(baseline: Baseline) -> dict[str, set[str]]:
    entry = is_entry_level(baseline.required_years)  # projects/hackathons count for Depth/Recency only if entry
    project_depth = {"depth"} if entry else set()
    return {
        "build_project": {"role_skills", "applied_knowledge", "tools", "complexity", "work_samples", "outcomes",
                          "impact", "proof_density", *project_depth},
        "earn_certification": {"validation_strength", "tools"},
        "get_reference": {"validation_strength"},
        "publish_work": {"work_samples", "proof_density", "consistency"},
        "contribute_or_compete": {"recognition", *({"depth", "recency"} if entry else set())},
    }


# --- Prompt input -------------------------------------------------------------------------------

def _criteria_lines(baseline: Baseline, kind: str) -> str:
    rows = [f"- {c.name} ({c.priority})" + (f": {c.description}" if c.description else "")
            for c in baseline.items(kind)]
    return "\n".join(rows) or "- none"


def target_role_text(baseline: Baseline) -> str:
    certs = [f"- {c.name}" + (f" (also: {', '.join(c.aliases)})" if c.aliases else "")
             for c in baseline.items("certification")]
    return "\n".join([
        f"Title: {baseline.title}", f"Level: {baseline.level} (required years: {baseline.required_years:g})",
        f"Description: {baseline.description or ''}",
        f"Scope expectation: {baseline.scope_expectation or ''}",
        f"Complexity expectation: {baseline.complexity_criteria or ''}",
        "Skills:", _criteria_lines(baseline, "skill"),
        "Tools:", _criteria_lines(baseline, "tool"),
        "Certification criteria:", "\n".join(certs) or "- none (recommend no certification)",
    ])


def candidate_text(profile: Profile) -> str:
    lines = []
    for r in profile.roles:
        when = f"{r.start_date or '?'} to {'present' if r.is_current else r.end_date or '?'}"
        lines.append(f"Role: {r.title}" + (f" at {r.employer}" if r.employer else "") + f" ({when})")
        lines += [f"  - {b.text}" for b in r.bullets]
    for p in profile.projects:
        if p.is_fork and not p.own_commits:
            continue
        where = " (GitHub repo)" if p.is_github else ""
        lines.append(f"Project: {p.name} [{p.kind}]{where}" + (f": {p.description}" if p.description else ""))
        lines += [f"  - {b.text}" for b in p.bullets]
    skills = [c.name_raw for c in profile.claims if c.kind in ("skill", "tool")]
    if skills:
        lines.append("Skills and tools listed: " + ", ".join(skills))
    lines += [f"Certification: {c.name}" for c in profile.certifications]
    lines += [f"Reference: {r.relationship or 'unspecified'}" for r in profile.references]
    return "\n".join(lines) or "(no roles, projects or skills)"


def build_user_message(baseline: Baseline, subscores: dict, weights: dict, gaps: list[Gap],
                       profile: Profile, peer_lines: dict[str, list[str]] | None = None) -> str:
    agg = aggregate(subscores, weights)
    scores = {"final": None if agg.final is None else round(agg.final, 1),
              "dimensions": {d: None if v is None else round(v, 2) for d, v in agg.dimensions.items()}}
    gap_data = [{"rank": i, "key": g.subcategory, "name": g.label, "status": g.status, "score": round(g.score, 2),
                 "guidance": g.guidance, **g.details,
                 **({"what_higher_scorers_have": peer_lines[g.subcategory]} if peer_lines and peer_lines.get(g.subcategory)
                    else {})} for i, g in enumerate(gaps, 1)]
    fit = {t: sorted(keys) for t, keys in type_fit(baseline).items()}
    return "\n\n".join([
        "Recommend actions for this candidate and target role.",
        wrap_tag("target_role", target_role_text(baseline)),
        wrap_tag("scores", json.dumps(scores, indent=1)),
        wrap_tag("gaps", json.dumps(gap_data, indent=1, default=str)),
        wrap_tag("action_types", "Subcategory keys each action type can address:\n"
                 + json.dumps(fit, indent=1)),
        wrap_tag("candidate", candidate_text(profile)),
    ])


# --- Validation ----------------------------------------------------------------------------------

def _canonical_criterion(name: str, baseline: Baseline) -> str | None:
    """Baseline criterion name for `name` (exact name or alias, case-insensitive), else None."""
    n = normalize_name(name)
    for c in baseline.criteria:
        if n == normalize_name(c.name) or n in {normalize_name(a) for a in c.aliases}:
            return c.name
    return None


def validate_action(a: Action, gap_keys: set[str], baseline: Baseline) -> tuple[Action | None, list[str]]:
    """Return (normalized action, errors). Errors mean the action is rejected."""
    errors = []
    if not a.addresses:
        errors.append("addresses is empty")
    bad_keys = [k for k in a.addresses if k not in gap_keys]
    if bad_keys:
        errors.append(f"addresses {bad_keys} are not among the gaps given {sorted(gap_keys)}")
    unfit = [k for k in a.addresses if k in gap_keys and k not in type_fit(baseline)[a.type]]
    if unfit:
        errors.append(f"a {a.type} action cannot earn points for {unfit} (see <action_types>)")
    canonical = []
    for name in a.covers_criteria:
        c = _canonical_criterion(name, baseline)
        if c is None:
            errors.append(f"'{name}' is not a skill, tool or certification in the baseline")
        elif c not in canonical:
            canonical.append(c)
    if a.type == "get_reference" and not re.search(r"\b(manager|supervisor)s?\b", f"{a.title} {a.what_to_do}", re.I):
        errors.append("Validation strength only counts a reference from a manager or supervisor; "
                      "name that relationship")
    if a.type == "earn_certification":
        certs = baseline.items("certification")
        text = f"{a.title} {a.what_to_do}"
        if not certs:
            errors.append("the baseline lists no certification criteria, so no certification may be recommended")
        elif not any(contains_phrase(text, t) for c in certs for t in [c.name, *c.aliases]):
            errors.append("the certification is not one of the baseline's certification criteria "
                          f"({', '.join(c.name for c in certs)} or their aliases)")
    if errors:
        return None, errors
    return a.model_copy(update={"covers_criteria": canonical}), []


def validate_plan(plan: Plan, gap_keys: set[str], baseline: Baseline) -> tuple[list[Action], list[dict]]:
    kept, rejected = [], []
    for a in plan.actions:
        ok, errors = validate_action(a, gap_keys, baseline)
        if ok:
            kept.append(ok)
        else:
            rejected.append({"title": a.title, "type": a.type, "errors": errors})
    return kept, rejected


# --- Points, ordering, rendering ---------------------------------------------------------------

@dataclass
class PricedAction:
    action: Action
    points: float

    @property
    def points_text(self) -> str:
        return f"up to {self.points:.1f} points if fully closed"


@dataclass
class PlanResult:
    run_id: str
    weights: dict
    gaps: list[Gap]
    actions: list[PricedAction] = field(default_factory=list)
    quick_wins: list[str] = field(default_factory=list)
    not_actionable: list[str] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    first_attempt_errors: list[dict] = field(default_factory=list)   # fixed by the retry
    markdown: str | None = None
    model_version: str | None = None
    error: str | None = None
    cached: bool = False


def price(actions: list[Action], subscores: dict, weights: dict) -> list[PricedAction]:
    """Points = sum of the addressed subcategories' potential gains under these weights; most first."""
    gains = {g.subcategory: g.gain for g in rank_gaps(subscores, weights)}
    priced = [PricedAction(a, sum(gains.get(k, 0.0) for k in a.addresses)) for a in actions]
    return sorted(priced, key=lambda p: -p.points)  # stable: ties keep the model's order


def render(baseline: Baseline, actions: list[PricedAction], quick_wins: list[str], not_actionable: list[str]) -> str:
    out = [f"## Improvement plan: {baseline.title} ({baseline.level})"]
    for i, p in enumerate(actions, 1):
        a = p.action
        out += ["", f"### {i}. {a.title}", f"*{TYPE_LABELS[a.type]} · {p.points_text}*", "", a.what_to_do]
        if a.must_show:
            out += ["", "**Must show:**", *[f"- {m}" for m in a.must_show]]
        meta = [f"**Gaps addressed:** {', '.join(config.SUBCATEGORY_LABELS.get(k, k) for k in a.addresses)}"]
        if a.covers_criteria:
            meta.append(f"**Covers:** {', '.join(a.covers_criteria)}")
        if a.builds_on:
            meta.append(f"**Builds on:** {a.builds_on}")
        out += ["", "  \n".join(meta)]
    if quick_wins:
        out += ["", "### Quick wins", *[f"- {q}" for q in quick_wins]]
    if not_actionable:
        out += ["", "### Not closable by a near-term action", *[f"- {n}" for n in not_actionable]]
    out += ["", "_Points are the most each action could add if its gaps were fully closed, not a promised score._"]
    return "\n".join(out)


# --- Generate / store / re-price -----------------------------------------------------------------

def _ask(planner: StructuredLLM, model: str, user: str) -> Plan:
    return planner.parse(model=model, system=load_prompt("plan_system"), user=user, output_type=Plan)


def generate_plan(conn: sqlite3.Connection, run_id: str, subscores: dict, weights: dict, baseline: Baseline,
                  profile: Profile, planner: StructuredLLM | None, *, regenerate: bool = False,
                  model: str | None = None,
                  peer_lines: dict[str, list[str]] | None = None) -> PlanResult:
    """Stored plan re-priced for these weights, or a new one from the model (first time / regenerate)."""
    model = model or config.REASONING_MODEL
    gaps = rank_gaps(subscores, weights, baseline)[:5]
    if not regenerate:
        stored = load_plan(conn, run_id, subscores, weights, baseline)
        if stored is not None:
            return stored
    if planner is None:
        return PlanResult(run_id, weights, gaps, error="No plan model configured.")
    if not gaps:
        return PlanResult(run_id, weights, gaps, markdown="No gaps: every scored subcategory is at its maximum.")

    gap_keys = {g.subcategory for g in gaps}
    user = build_user_message(baseline, subscores, weights, gaps, profile, peer_lines)
    first_attempt: list[dict] = []
    from providers.routing import clear_last
    clear_last()
    try:
        plan = _ask(planner, model, user)
        kept, rejected = validate_plan(plan, gap_keys, baseline)
        if rejected:  # retry once with the errors appended; then drop what still fails
            first_attempt = rejected
            problems = "\n".join(f"- '{r['title']}' ({r['type']}): {'; '.join(r['errors'])}" for r in rejected)
            plan = _ask(planner, model, f"{user}\n\nYour previous plan had invalid actions:\n{problems}\n"
                                        "Return the whole plan again with these fixed.")
            kept, rejected = validate_plan(plan, gap_keys, baseline)
    except LLMError as exc:
        log.warning("plan generation failed: %s", exc)
        return PlanResult(run_id, weights, gaps, error=str(exc))

    from providers.routing import last_model
    model = last_model(model)                       # the model that answered (failover may change it)
    quick_wins, not_actionable = plan.quick_wins[:3], plan.not_actionable
    dropped = list(rejected)
    if len(plan.quick_wins) > 3:
        dropped.append({"title": f"{len(plan.quick_wins) - 3} extra quick win(s)", "type": "quick_win",
                        "errors": ["at most 3 quick wins"]})
    priced = price(kept, subscores, weights)
    markdown = render(baseline, priced, quick_wins, not_actionable)
    stored = {"actions": [a.model_dump() for a in kept], "quick_wins": quick_wins, "not_actionable": not_actionable,
              "dropped": dropped, "first_attempt_errors": first_attempt, "gap_keys": sorted(gap_keys)}
    with conn:
        conn.execute(
            "INSERT INTO gap_reports (id, run_id, weights_json, ranked_gaps, plan_json, report_markdown, model_version, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id(), run_id, json.dumps(weights, sort_keys=True), json.dumps([g.as_dict() for g in gaps], default=str),
             json.dumps(stored), markdown, model, now_iso()),
        )
    return PlanResult(run_id, weights, gaps, priced, quick_wins, not_actionable, dropped, first_attempt, markdown,
                      model)


def load_plan(conn: sqlite3.Connection, run_id: str, subscores: dict, weights: dict,
              baseline: Baseline) -> PlanResult | None:
    """Latest stored plan for the run, re-priced and re-ordered for `weights`. No model call."""
    row = conn.execute("SELECT * FROM gap_reports WHERE run_id = ? AND plan_json IS NOT NULL "
                       "ORDER BY created_at DESC, rowid DESC LIMIT 1", (run_id,)).fetchone()
    if row is None:
        return None
    stored = json.loads(row["plan_json"])
    priced = price([Action.model_validate(a) for a in stored["actions"]], subscores, weights)
    return PlanResult(run_id, weights, rank_gaps(subscores, weights, baseline)[:5], priced, stored["quick_wins"],
                      stored["not_actionable"], stored["dropped"], stored.get("first_attempt_errors", []),
                      render(baseline, priced, stored["quick_wins"], stored["not_actionable"]),
                      row["model_version"], cached=True)
