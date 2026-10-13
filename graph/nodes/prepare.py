"""prepare_match: relevance of every role and project, judged once per baseline (specs/02).

Relevance labels are shared by Relevance, Depth, Recency, Complexity and every
bullet-level Authority subcategory.
"""
from __future__ import annotations

from graph.context import Ask, Ctx, project_text, role_text


def relevance_asks(ctx: Ctx) -> list[Ask]:
    asks = [Ask("relevance", "role", r.id, {"baseline": ctx.baseline_summary, "role": role_text(r)})
            for r in ctx.profile.roles]
    asks += [Ask("relevance", "project", p.id, {"baseline": ctx.baseline_summary, "project": project_text(p)})
             for p in ctx.counted_projects()]
    return asks
