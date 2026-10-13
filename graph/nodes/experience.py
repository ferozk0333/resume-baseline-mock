"""Experience: relevance | depth | scope | progression | recency (specs/04 §2)."""
from __future__ import annotations

from graph.context import Ask, Ctx, Labels, role_text
from graph.nodes.base import Subcategory, no_asks
from scoring import formulas as f


def _relevance(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.relevance(ctx.role_ins(), ctx.today)


def _depth(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.depth(ctx.role_ins(), ctx.project_ins(), ctx.baseline.required_years, ctx.today)


def _recency(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.recency(ctx.role_ins(), ctx.project_ins(), ctx.baseline.required_years, ctx.today)


def _scope_role(ctx: Ctx):
    chosen = f.select_scope_role(ctx.role_ins(), ctx.today)
    return None if chosen is None else next(r for r in ctx.profile.roles if r.id == chosen.id)


def scope_plan(ctx: Ctx) -> list[Ask]:
    role = _scope_role(ctx)
    if role is None:
        return []
    return [Ask("scope_vs_baseline", "role", role.id,
                {"baseline": f"Expected scope: {ctx.baseline.scope_expectation or ''}", "role": role_text(role)})]


def scope_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    chosen = f.select_scope_role(ctx.role_ins(), ctx.today)
    label = labels.get("scope_vs_baseline", chosen.id) if chosen else None
    return f.scope(chosen, label, has_roles=bool(ctx.profile.roles))


def _roles_oldest_first(ctx: Ctx):
    return sorted(ctx.profile.roles, key=lambda r: (r.start_date is None, r.start_date or r.end_date))


def progression_plan(ctx: Ctx) -> list[Ask]:
    if len(ctx.profile.roles) < 2:
        return []  # N/A: nothing to judge
    return [Ask("progression", "candidate", ctx.candidate_id,
                {"roles": [role_text(r) for r in _roles_oldest_first(ctx)]})]


def progression_compute(ctx: Ctx, labels: Labels) -> f.SubScore:
    return f.progression(len(ctx.profile.roles), labels.get("progression", ctx.candidate_id))


SUBCATEGORIES = [
    Subcategory("relevance", no_asks, _relevance),
    Subcategory("depth", no_asks, _depth),
    Subcategory("scope", scope_plan, scope_compute),
    Subcategory("progression", progression_plan, progression_compute),
    Subcategory("recency", no_asks, _recency),
]
