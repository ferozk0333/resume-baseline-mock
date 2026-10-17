"""Authority: outcomes | impact | ownership | scope_of_impact | recognition (specs/04 §4).

Bullet labels are job-independent (cached across baselines) and multiplied by the
bullet's inherited relevance at scoring time.
"""
from __future__ import annotations

from graph.context import Ask, Ctx, Labels, project_text
from graph.nodes.base import Subcategory
from scoring import formulas as f


def _bullets(ctx: Ctx):
    rel = ctx.parent_relevance()
    for item in [*ctx.profile.roles, *ctx.counted_projects()]:
        for b in item.bullets:
            yield b, rel.get(b.id)


def _bullet_asks(ctx: Ctx, *keys: str) -> list[Ask]:
    return [Ask(k, "bullet", b.id, {"bullet": b.text}) for b, _ in _bullets(ctx) for k in keys]


def outcomes_plan(ctx: Ctx) -> list[Ask]:
    return _bullet_asks(ctx, "outcome_metric", "outcome_direction")


def outcomes_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.outcomes([f.BulletIn(b.id, rel, has_metric=labels.yes("outcome_metric", b.id),
                                  has_direction=labels.yes("outcome_direction", b.id))
                       for b, rel in _bullets(ctx)])


def impact_plan(ctx: Ctx) -> list[Ask]:
    return _bullet_asks(ctx, "impact_type")


def impact_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.impact([f.BulletIn(b.id, rel, impact=labels.get("impact_type", b.id)) for b, rel in _bullets(ctx)])


def ownership_plan(ctx: Ctx) -> list[Ask]:
    return _bullet_asks(ctx, "ownership")


def ownership_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.ownership([f.BulletIn(b.id, rel, ownership=labels.get("ownership", b.id))
                        for b, rel in _bullets(ctx)])


def reach_plan(ctx: Ctx) -> list[Ask]:
    return _bullet_asks(ctx, "reach")


def reach_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.scope_of_impact([f.BulletIn(b.id, rel, reach=labels.get("reach", b.id)) for b, rel in _bullets(ctx)],
                             ctx.baseline.expected_reach)


def _awards(ctx: Ctx):
    return [c for c in ctx.profile.claims if c.kind == "award"]


def award_text(c) -> str:
    """What the judge reads for an award: its source quote, or for one typed in Review (specs/19 item 22) its name
    with the issuer and what it was for. An award with no extra fields reads exactly as before."""
    if c.quote:
        return c.quote
    d = c.details or {}
    text = c.name_raw + (f" ({d['issuer']})" if d.get("issuer") else "")
    return text + (f": {d['for']}" if d.get("for") else "")


def _publications(ctx: Ctx):
    return [p for p in ctx.profile.projects if p.kind == "publication"]


def recognition_plan(ctx: Ctx) -> list[Ask]:
    asks = [Ask("award_relevant", "claim", c.id,
                {"baseline": ctx.baseline_summary, "award": award_text(c)}) for c in _awards(ctx)]
    asks += [Ask("publication_relevant", "project", p.id,
                 {"baseline": ctx.baseline_summary, "publication": project_text(p)}) for p in _publications(ctx)]
    return asks


def recognition_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.recognition([labels.yes("award_relevant", c.id) for c in _awards(ctx)],
                         [labels.yes("publication_relevant", p.id) for p in _publications(ctx)])


SUBCATEGORIES = [
    Subcategory("outcomes", outcomes_plan, outcomes_compute),
    Subcategory("impact", impact_plan, impact_compute),
    Subcategory("ownership", ownership_plan, ownership_compute),
    Subcategory("scope_of_impact", reach_plan, reach_compute),
    Subcategory("recognition", recognition_plan, recognition_compute),
]
