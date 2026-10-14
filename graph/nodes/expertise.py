"""Expertise: role_skills | applied_knowledge | tools | complexity | work_samples (specs/04 §3)."""
from __future__ import annotations

from core.models import BaselineCriterion
from core.text import contains_phrase
from graph.context import Ask, Ctx, Labels, project_text, role_text
from graph.nodes.base import Subcategory
from scoring import formulas as f


# --- Pre-filtering (specs/05: select bullets by alias/substring match) ------------------

def terms(c: BaselineCriterion) -> list[str]:
    return [c.name, *c.aliases]


def mentions(text: str | None, c: BaselineCriterion) -> bool:
    return any(contains_phrase(text, t) for t in terms(c))


def evidence_lines(ctx: Ctx) -> list[str]:
    """Role/project bullets and project descriptions: where a skill can be *shown*."""
    lines = [b.text for r in ctx.profile.roles for b in r.bullets]
    for p in ctx.counted_projects():
        if p.description:
            lines.append(f"{p.name}: {p.description}")
        lines += [b.text for b in p.bullets]
    return lines


def listed(ctx: Ctx) -> list[str]:
    """Skills/tools named outside any bullet (skills section)."""
    return [c.name_raw for c in ctx.profile.claims if c.kind in ("skill", "tool") and c.bullet_id is None]


def repo_lines(ctx: Ctx) -> list[str]:
    out = []
    for p in ctx.counted_projects():
        if p.is_github:
            raw = (ctx.evidence.get(p.evidence_id) or {}).get("raw_text") or ""
            langs = next((ln.split(":", 1)[1].strip() for ln in raw.splitlines() if ln.startswith("Languages:")), "")
            out.append(f"{p.name}: {p.description or ''} (languages: {langs})".strip())
    return out


def criterion_text(c: BaselineCriterion) -> str:
    also = f" Also called: {', '.join(c.aliases)}." if c.aliases else ""
    return f"{c.name}: {c.description or ''}{also}"


# --- Skills ------------------------------------------------------------------------------
# Alias match in a bullet → judge (may be Jev) with the matched lines. No bullet match → the unmatched route
# (model with ALL lines; the label must carry a quote found in the input, else retry once, then missing), also
# when the alias matches only the skills list: a bullet with a near variant ("6 A/B tests" for "A/B testing")
# can then earn credit (specs/13 §0.4). Nothing at all → missing by rule.

def _skill_asks(ctx: Ctx, key: str, none_label: str) -> list[Ask]:
    asks = []
    lines, skills = evidence_lines(ctx), listed(ctx)
    for c in ctx.baseline.items("skill"):
        hits = [ln for ln in lines if mentions(ln, c)]
        if hits:
            asks.append(Ask(key, "criterion", c.id,
                            {"criterion": criterion_text(c), "bullets": hits, "skills_list": skills}))
        elif lines or skills:
            asks.append(Ask(key, "criterion", c.id,
                            {"criterion": criterion_text(c), "bullets": lines, "skills_list": skills},
                            route="claude", require_quote=True))
        else:
            asks.append(Ask(key, "criterion", c.id, {"criterion": criterion_text(c)}, rule_label=none_label,
                            rule_reason="The profile has no bullets and no skills list."))
    return asks


def _by_priority(ctx: Ctx, labels: Labels, kind: str, key: str) -> tuple[dict, dict]:
    req = {c.name: labels.get(key, c.id) for c in ctx.baseline.items(kind, "required")}
    pref = {c.name: labels.get(key, c.id) for c in ctx.baseline.items(kind, "preferred")}
    return req, pref


def skills_plan(ctx: Ctx) -> list[Ask]:
    return _skill_asks(ctx, "skill_evidence", "missing")


def skills_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.role_skills(*_by_priority(ctx, labels, "skill", "skill_evidence"))


def applied_plan(ctx: Ctx) -> list[Ask]:
    return _skill_asks(ctx, "applied_level", "mention_or_missing")


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
        # Matched only in the skills list → the unmatched route, so a bullet with a near variant can count (§0.4).
        if any(context[k] for k in ("bullets", "repositories", "certifications")):
            asks.append(Ask("tool_evidence", "criterion", c.id, context))
        elif lines or repos or certs or skills:
            asks.append(Ask("tool_evidence", "criterion", c.id, everything, route="claude", require_quote=True))
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
        elif ctx.evidence_type(p.evidence_id) == "portfolio":
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
