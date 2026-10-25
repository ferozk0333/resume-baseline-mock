"""Expertise: role_skills | applied_knowledge | tools | complexity | work_samples (specs/04 §3)."""
from __future__ import annotations

from core.models import BaselineCriterion
from core.text import contains_phrase
from graph.context import Ask, Ctx, Labels, project_text, role_text
from graph.nodes.base import Subcategory
from judges.questions import QUESTIONS
from scoring import formulas as f


# --- Pre-filtering (specs/05: select bullets by alias/substring match) ------------------

def terms(c: BaselineCriterion) -> list[str]:
    return [c.name, *c.aliases]


def mentions(text: str | None, c: BaselineCriterion) -> bool:
    return any(contains_phrase(text, t) for t in terms(c))


def evidence_lines(ctx: Ctx) -> list[str]:
    """Role/project bullets and project descriptions: where a skill can be *shown*."""
    return [text for text, _ in sourced_lines(ctx)]


def sourced_lines(ctx: Ctx) -> list[tuple[str, str | None]]:
    """evidence_lines with the evidence item each line comes from (a merged role keeps each bullet's source)."""
    lines = [(b.text, b.evidence_id) for r in ctx.profile.roles for b in r.bullets]
    for p in ctx.counted_projects():
        if p.description:
            lines.append((f"{p.name}: {p.description}", p.evidence_id))
        lines += [(b.text, b.evidence_id) for b in p.bullets]
    return lines


def listed(ctx: Ctx) -> list[str]:
    """Skills/tools named outside any bullet (skills section)."""
    return [c.name_raw for c in ctx.profile.claims if c.kind in ("skill", "tool") and c.bullet_id is None]


def repo_lines(ctx: Ctx) -> list[str]:
    return [line for p in ctx.counted_projects() if p.is_github for line in repo_lines_of(ctx, p)]


def repo_lines_of(ctx: Ctx, p) -> list[str]:
    raw = (ctx.evidence.get(p.evidence_id) or {}).get("raw_text") or ""
    langs = next((ln.split(":", 1)[1].strip() for ln in raw.splitlines() if ln.startswith("Languages:")), "")
    return [f"{p.name}: {p.description or ''} (languages: {langs})".strip()]


def criterion_text(c: BaselineCriterion) -> str:
    also = f" Also called: {', '.join(c.aliases)}." if c.aliases else ""
    return f"{c.name}: {c.description or ''}{also}"


# --- Skills ------------------------------------------------------------------------------
# Alias match in a bullet → judge (may be Jev) with the matched lines. Matched only in the skills list → that same
# judge with the list (the "alias route"), AND the all-bullets judge as a second judgment on subject
# "<criterion id>#all" (specs/13 §0.4): a bullet with a near variant ("6 A/B tests" for "A/B testing") can earn
# credit, and the final label is the better of the two, so the all-bullets route only ever adds credit.
# No match anywhere → the all-bullets route alone (model; the label must carry a quote found in the input, else
# retry once, then missing). Nothing at all → missing by rule.

ALL_BULLETS = "#all"


def _all_bullets(key: str, c: BaselineCriterion, context: dict, subject_id: str | None = None) -> Ask:
    return Ask(key, "criterion", subject_id or c.id, context, route="claude", require_quote=True)


def _everything(key: str, c: BaselineCriterion, context: dict, subject_id: str | None = None) -> list[Ask]:
    """The all-bullets route: one ask over everything."""
    return [_all_bullets(key, c, context, subject_id)]


def _skill_asks(ctx: Ctx, key: str, none_label: str, list_counts: bool) -> list[Ask]:
    asks = []
    lines, skills = evidence_lines(ctx), listed(ctx)
    for c in ctx.baseline.items("skill"):
        hits = [ln for ln in lines if mentions(ln, c)]
        in_list = list_counts and any(mentions(s, c) for s in skills)
        everything = {"criterion": criterion_text(c), "bullets": lines, "skills_list": skills}
        if hits or in_list:
            asks.append(Ask(key, "criterion", c.id,
                            {"criterion": criterion_text(c), "bullets": hits, "skills_list": skills}))
            if not hits and lines:
                asks += _everything(key, c, everything, c.id + ALL_BULLETS)
        elif lines or skills:
            asks += _everything(key, c, everything)
        else:
            asks.append(Ask(key, "criterion", c.id, {"criterion": criterion_text(c)}, rule_label=none_label,
                            rule_reason="The profile has no bullets and no skills list."))
    return asks


def best_label(key: str, *labels: str | None) -> str | None:
    """The better of several labels for one criterion (options are listed best first); None if all failed."""
    order = list(QUESTIONS[key].options)
    found = [label for label in labels if label is not None]
    return min(found, key=order.index) if found else None


def criterion_label(labels: Labels, key: str, c: BaselineCriterion) -> str | None:
    """Alias route and all-bullets route: the better answer."""
    return best_label(key, labels.get(key, c.id), labels.get(key, c.id + ALL_BULLETS))


def _by_priority(ctx: Ctx, labels: Labels, kind: str, key: str) -> tuple[dict, dict]:
    req = {c.name: criterion_label(labels, key, c) for c in ctx.baseline.items(kind, "required")}
    pref = {c.name: criterion_label(labels, key, c) for c in ctx.baseline.items(kind, "preferred")}
    return req, pref


def skills_plan(ctx: Ctx) -> list[Ask]:
    return _skill_asks(ctx, "skill_evidence", "missing", list_counts=True)


def skills_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.role_skills(*_by_priority(ctx, labels, "skill", "skill_evidence"))


def applied_plan(ctx: Ctx) -> list[Ask]:
    # Applying a skill needs a bullet: a skills-list match alone does not count, so no alias-route ask.
    return _skill_asks(ctx, "applied_level", "mention_or_missing", list_counts=False)


def applied_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.applied_knowledge(*_by_priority(ctx, labels, "skill", "applied_level"))


# --- Tools ---------------------------------------------------------------------------------

def tools_plan(ctx: Ctx) -> list[Ask]:
    asks = []
    lines, skills, repos = evidence_lines(ctx), listed(ctx), repo_lines(ctx)
    certs = [c.name for c in ctx.profile.certifications]
    for c in ctx.baseline.items("tool"):
        context = {
            "criterion": criterion_text(c),
            "bullets": [ln for ln in lines if mentions(ln, c)],
            "repositories": [r for r in repos if mentions(r, c)],
            "certifications": [n for n in certs if mentions(n, c)],
            "skills_list": [s for s in skills if mentions(s, c)],
        }
        everything = {"criterion": criterion_text(c), "bullets": lines, "repositories": repos,
                      "certifications": certs, "skills_list": skills}
        if any(context[k] for k in ("bullets", "repositories", "certifications", "skills_list")):
            asks.append(Ask("tool_evidence", "criterion", c.id, context))
            # Matched only in the skills list: also the all-bullets judge; the better label counts (§0.4).
            if not any(context[k] for k in ("bullets", "repositories", "certifications")) and (lines or repos):
                asks += _everything("tool_evidence", c, everything, c.id + ALL_BULLETS)
        elif lines or repos or certs or skills:
            asks += _everything("tool_evidence", c, everything)
        else:
            asks.append(Ask("tool_evidence", "criterion", c.id, {"criterion": criterion_text(c)},
                            rule_label="missing", rule_reason="The profile has no bullets, repositories, "
                                                              "certifications or skills list."))
    return asks


def tools_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.tools(*_by_priority(ctx, labels, "tool", "tool_evidence"))


# --- Complexity -------------------------------------------------------------------------------

def _relevant_items(ctx: Ctx):
    for r in ctx.profile.roles:
        yield "role", r.id, role_text(r), bool(r.bullets)
    for p in ctx.counted_projects():
        yield "project", p.id, project_text(p), bool(p.bullets or (p.description or "").strip())


def complexity_plan(ctx: Ctx) -> list[Ask]:
    """An item with no bullets and no description has nothing to judge: it gets "no" by rule, no model call.
    Complexity takes the best item, so this never lowers a score that a judged item sets."""
    rubric = f"Target role: {ctx.baseline.title} ({ctx.baseline.level}). " \
             f"Desired complexity: {ctx.baseline.complexity_criteria or ''}"
    return [Ask("complexity", kind, item_id, {"baseline": rubric, "item": text},
                rule_label=None if has_content else "no",
                rule_reason=None if has_content else "Nothing to judge: no bullets or description.")
            for kind, item_id, text, has_content in _relevant_items(ctx)
            if (f.RELEVANCE.get(ctx.relevance.get(item_id)) or 0) > 0]


def complexity_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    items = [f.GradedItem(item_id, ctx.relevance.get(item_id), labels.get("complexity", item_id))
             for _, item_id, _, _ in _relevant_items(ctx)]
    return f.complexity(items)


# --- Work samples --------------------------------------------------------------------------------

def _work_samples(ctx: Ctx) -> list[tuple[str, object]]:
    """(work-sample kind, project) for every repo, portfolio project, presentation and publication."""
    out = []
    for p in ctx.profile.projects:
        if p.is_github:
            out.append(("repo", p))
        elif ctx.from_portfolio(p):
            out.append(("portfolio", p))
        elif p.kind in ("presentation", "publication"):
            out.append((p.kind, p))
    return out


def _counted(p) -> bool:
    return not (p.is_fork and not p.own_commits)


def work_samples_plan(ctx: Ctx) -> list[Ask]:
    return [Ask("work_sample_relevant", "project", p.id,
                {"baseline": ctx.baseline_summary, "work_sample": project_text(p)})
            for kind, p in _work_samples(ctx) if _counted(p)]


def work_samples_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.work_samples([
        f.WorkSampleIn(p.id, kind, labels.yes("work_sample_relevant", p.id) if _counted(p) else None,
                       counted=_counted(p))
        for kind, p in _work_samples(ctx)
    ])


SUBCATEGORIES = [
    Subcategory("role_skills", skills_plan, skills_compute),
    Subcategory("applied_knowledge", applied_plan, applied_compute),
    Subcategory("tools", tools_plan, tools_compute),
    Subcategory("complexity", complexity_plan, complexity_compute),
    Subcategory("work_samples", work_samples_plan, work_samples_compute),
]
