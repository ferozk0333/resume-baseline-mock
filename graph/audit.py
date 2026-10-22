"""Score computation, written out (audit round, item 1; flag SCORE_AUDIT). Read-only: no model calls, no writes.

For one run: the profile and baseline it was scored on, at the run's date, with exactly the judgments the compute
step read (the same saved-answer lookup, limited to answers saved before the run finished). Each subcategory's own
compute function runs again and must give the saved score (`matches_saved`); next to it, every input item with its
full text, label, the label's value in code, relevance, months and who decided the label, the items left out and
why, and the arithmetic with the real numbers. Dimensions and the final score are written out from the saved
subcategory scores with the weights shown on the page.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from core import config, db
from graph.context import Ctx, Deps, Labels, open_ctx, project_text, resolve, role_text
from judges.base import Judgment
from judges.service import JudgmentService, input_hash
from scoring import formulas as f
from scoring.aggregate import INCLUDED_STATUSES, normalize_weights

# --- Numbers ---------------------------------------------------------------------------------------------------


def n(x) -> str:
    """An operand: as short as it can be written exactly to 3 decimals (24, 0.5, 0.667)."""
    if x is None:
        return "—"
    if isinstance(x, bool):
        return "1" if x else "0"
    s = f"{float(x):.3f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def r(x) -> str:
    """A result: always 3 decimals."""
    return "—" if x is None else f"{float(x):.3f}"


# --- Saved answers as the run saw them ---------------------------------------------------------------------------

class _SavedJudge:
    """Any model's saved answer counts (the run's judge used one of them); never asked anything."""
    scripted = True

    def __init__(self, models: list[str]):
        self._models = models
        self.fallback = self

    def models_for(self, key: str) -> list[str]:
        return self._models

    def __getattr__(self, name):                        # yes_no, choice, …: never called with call=False
        raise RuntimeError("the computation view never asks a judge")


class _UntilService(JudgmentService):
    """Saved answers created before `cutoff` only: a later re-judgment of the same text is not what this run read."""

    def __init__(self, *a, cutoff: str, **k):
        super().__init__(*a, **k)
        self.cutoff = cutoff

    def _lookup(self, key, subject_id, context, baseline, models):
        baseline_id, baseline_hash = self._scope(key, baseline)
        row = self.conn.execute(
            f"SELECT * FROM judgments WHERE candidate_id = ? AND subject_id = ? AND question = ? "
            f"AND baseline_id IS ? AND baseline_hash IS ? AND input_hash = ? AND used = 1 AND created_at <= ? "
            f"AND model_version IN ({','.join('?' * len(models))}) ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (self.candidate_id, subject_id, key, baseline_id, baseline_hash, input_hash(key, context), self.cutoff,
             *models)).fetchone()
        if row is None:
            return None
        return Judgment(label=row["label"], quote=row["quote"], reasoning=row["reasoning"], method=row["method"],
                        model_version=row["model_version"], confidence=row["confidence"], score=row["score"],
                        probabilities=json.loads(row["probabilities"]) if row["probabilities"] else None)


def _run_date(run) -> date:
    """The date the run was scored on (runs.scored_on); runs saved before it was recorded: the day they started."""
    if "scored_on" in run.keys() and run["scored_on"]:
        return date.fromisoformat(run["scored_on"])
    started = datetime.fromisoformat(run["started_at"].replace("Z", "+00:00"))
    return started.astimezone().date()


# --- Who decided a label ------------------------------------------------------------------------------------------

def _provider(method: str | None, model: str | None) -> str:
    if method == "rule":
        return "rule (code)"
    if method == "jev":
        return f"Jev ({model})" if model else "Jev"
    if model and "/" in model:
        provider, name = model.split("/", 1)
        return f"{provider.capitalize()} · {name}"
    return f"{method or '?'} · {model or '?'}"


class Trail:
    """The judgment behind each (question, subject), with who decided it and whether it was a fallback."""

    def __init__(self, ctx: Ctx, cutoff: str):
        self.ctx, self.cutoff = ctx, cutoff
        self.labels: list[Labels] = []

    def add(self, labels: Labels) -> Labels:
        self.labels.append(labels)
        return labels

    def _ask(self, key, sid):
        for lab in self.labels:
            for a in lab.asks:
                if a.key == key and a.subject_id == sid:
                    return lab, a
        return None, None

    def who(self, key: str, sid: str) -> dict:
        lab, ask = self._ask(key, sid)
        j = lab.judgment(key, sid) if lab else None
        if j is None:
            return {"question": key, "label": None, "decided_by": "no saved answer (judgment failed)"}
        out = {"question": key, "label": j.label, "decided_by": _provider(j.method, j.model_version),
               "confidence": j.confidence, "fallback": None, "quote": j.quote}
        if ask is not None and ask.rule_label is not None:
            out["decided_by"] = "rule (code)"
            out["reason"] = ask.rule_reason
        elif j.method not in ("jev", "rule") and ask is not None:
            row = self.ctx.conn.execute(
                "SELECT confidence, label FROM judgments WHERE candidate_id = ? AND subject_id = ? AND question = ? "
                "AND input_hash = ? AND used = 0 AND method = 'jev' AND created_at <= ? ORDER BY created_at DESC "
                "LIMIT 1", (self.ctx.candidate_id, sid, key, input_hash(key, ask.context), self.cutoff)).fetchone()
            if row is not None:
                out["fallback"] = {"from": "Jev", "label": row["label"], "confidence": row["confidence"]}
        return out


# --- Items --------------------------------------------------------------------------------------------------------

def _source(ctx: Ctx, item) -> str:
    if getattr(item, "source", None) == "user_edit":
        return "your edit"
    t = ctx.evidence_type(getattr(item, "evidence_id", None))
    return {"resume": "resume", "linkedin_pdf": "LinkedIn", "github_repo": "GitHub", "portfolio": "portfolio"}.get(
        t or "", t or "typed in")


def _dates(start, end, current=False) -> str:
    s = start.strftime("%Y-%m") if start else "?"
    e = "present" if current else (end.strftime("%Y-%m") if end else "?")
    return f"{s} to {e}"


def _row(kind, text, *, label=None, value=None, relevance=None, factors=None, who=None, source=None, extra=None):
    out = {"kind": kind, "text": text, "label": label, "value": value, "relevance": relevance,
           "factors": factors or {}, "decided_by": who or [], "source": source}
    if extra:
        out.update(extra)
    return out


def _rel(ctx: Ctx, trail: Trail, item_id: str) -> tuple[str | None, float | None, dict]:
    label = ctx.relevance.get(item_id)
    return label, f.RELEVANCE.get(label) if label else None, trail.who("relevance", item_id)


# --- Per subcategory ------------------------------------------------------------------------------------------------

def _relevance(ctx, trail, labels, s):
    inputs, excluded, terms, months_total, num = [], [], [], 0, 0.0
    for role in ctx.profile.roles:
        label, value, who = _rel(ctx, trail, role.id)
        months = f.role_months(f.RoleIn(role.id, role.start_date, role.end_date, role.is_current, label), ctx.today)
        row = _row("role", role_text(role), label=label, value=value, relevance=value,
                   factors={"dates": _dates(role.start_date, role.end_date, role.is_current), "months": months},
                   who=[who], source=_source(ctx, role))
        if value is None:
            excluded.append({**row, "reason": "relevance judgment failed"})
        elif months is None:
            excluded.append({**row, "reason": "no usable dates (missing start, future, or end before start)"})
        else:
            inputs.append(row)
            terms.append(f"{months}×{n(value)}")
            num += months * value
            months_total += months
    arithmetic = [f"({' + '.join(terms)}) / {months_total} = {r(num / months_total)}"] if months_total else []
    return inputs, excluded, arithmetic


def _timeline(ctx) -> tuple[dict[int, tuple[float, str]], dict[str, int]]:
    """month → (best relevance, role id); months each role supplies (overlaps counted once, best relevance)."""
    best: dict[int, tuple[float, str]] = {}
    for role in ctx.profile.roles:
        label = ctx.relevance.get(role.id)
        value = f.RELEVANCE.get(label) if label else None
        span = f.role_span(f.RoleIn(role.id, role.start_date, role.end_date, role.is_current, label), ctx.today)
        if value is None or span is None:
            continue
        for m in range(span[0], span[1] + 1):
            if m not in best or value > best[m][0]:
                best[m] = (value, role.id)
    counted: dict[str, int] = {}
    for _, rid in best.values():
        counted[rid] = counted.get(rid, 0) + 1
    return best, counted


def _depth(ctx, trail, labels, s):
    best, counted = _timeline(ctx)
    inputs, excluded = [], []
    by_value: dict[float, int] = {}
    for value, _ in best.values():
        by_value[value] = by_value.get(value, 0) + 1
    for role in ctx.profile.roles:
        label, value, who = _rel(ctx, trail, role.id)
        months = f.role_months(f.RoleIn(role.id, role.start_date, role.end_date, role.is_current, label), ctx.today)
        row = _row("role", role_text(role), label=label, value=value, relevance=value,
                   factors={"dates": _dates(role.start_date, role.end_date, role.is_current), "months": months,
                            "months counted (overlaps once, best relevance)": counted.get(role.id, 0)},
                   who=[who], source=_source(ctx, role))
        (excluded.append({**row, "reason": "relevance judgment failed" if value is None else "no usable dates"})
         if value is None or months is None else inputs.append(row))
    required = max(1.0, ctx.baseline.required_years)
    relevant_months = sum(v for v, _ in best.values())
    terms = " + ".join(f"{k}×{n(v)}" for v, k in sorted(((v, k) for v, k in by_value.items()), reverse=True))
    work = min(1.0, relevant_months / 12 / required)
    arithmetic = [f"relevant months = {terms or '0'} = {n(relevant_months)}",
                  f"relevant years = {n(relevant_months)} / 12 = {r(relevant_months / 12)}",
                  f"work = min(1, {r(relevant_months / 12)} / max(1, {n(ctx.baseline.required_years)})) = {r(work)}"]
    if f.is_entry_level(ctx.baseline.required_years):
        values = []
        for p in ctx.profile.projects:
            label, value, who = _rel(ctx, trail, p.id)
            row = _row(p.kind, project_text(p), label=label, value=value, relevance=value, who=[who],
                       source=_source(ctx, p))
            if p.kind not in f.DEPTH_PROJECT_KINDS and not p.is_github:
                excluded.append({**row, "reason": f"kind '{p.kind}' does not count toward Depth"})
            elif p.is_fork and not p.own_commits:
                excluded.append({**row, "reason": "fork with 0 own commits"})
            elif value is None:
                excluded.append({**row, "reason": "relevance judgment failed"})
            else:
                inputs.append(row)
                values.append(value)
        project = min(1.0, sum(values) / config.PROJECT_TARGET)
        arithmetic += [f"projects = min(1, ({' + '.join(n(v) for v in values) or '0'}) / {config.PROJECT_TARGET}) "
                       f"= {r(project)}",
                       f"entry level ({n(ctx.baseline.required_years)} ≤ {config.ENTRY_LEVEL_MAX_YEARS} years): "
                       f"max(work {r(work)}, projects {r(project)}) = {r(max(work, project))}"]
    else:
        for p in ctx.profile.projects:
            excluded.append(_row(p.kind, project_text(p), source=_source(ctx, p),
                                 extra={"reason": "not entry level: projects do not count toward Depth"}))
    return inputs, excluded, arithmetic


def _scope(ctx, trail, labels, s):
    chosen = f.select_scope_role(f.RoleIn.__class__ and ctx.role_ins(), ctx.today)
    inputs, excluded = [], []
    for role in ctx.profile.roles:
        rl, rv, rwho = _rel(ctx, trail, role.id)
        if chosen and role.id == chosen.id:
            label = labels.get("scope_vs_baseline", role.id)
            inputs.append(_row("role", role_text(role), label=label, value=f.SCOPE.get(label) if label else None,
                               relevance=rv, factors={"expected scope": ctx.baseline.scope_expectation},
                               who=[rwho, trail.who("scope_vs_baseline", role.id)], source=_source(ctx, role)))
        else:
            excluded.append(_row("role", role_text(role), relevance=rv, who=[rwho], source=_source(ctx, role),
                                 extra={"reason": "not the most recent relevant role" if rv and rv >= 0.5
                                        else "relevance below 0.5"}))
    arithmetic = []
    if inputs and inputs[0]["value"] is not None:
        arithmetic = [f"most recent role with relevance ≥ 0.5 ({'relevant' if inputs[0]['relevance'] == 1 else 'adjacent'}"
                      f" first): {inputs[0]['label']} → {r(inputs[0]['value'])}"]
    return inputs, excluded, arithmetic


def _progression(ctx, trail, labels, s):
    if len(ctx.profile.roles) < 2:
        return [], [], []
    label = labels.get("progression", ctx.candidate_id)
    roles = sorted(ctx.profile.roles, key=lambda x: (x.start_date is None, x.start_date or x.end_date))
    text = "\n".join(role_text(x).split("\n")[0] for x in roles)
    return ([_row("roles (oldest first)", text, label=label, value=f.PROGRESSION.get(label) if label else None,
                  factors={"roles": len(roles)}, who=[trail.who("progression", ctx.candidate_id)])], [],
            [f"{label} → {r(f.PROGRESSION.get(label))}"] if label else [])


def _recency(ctx, trail, labels, s):
    entry = f.is_entry_level(ctx.baseline.required_years)
    now = f.month_index(ctx.today)
    inputs, excluded, terms, best = [], [], [], None
    items = [("role", x, x.start_date, x.end_date, x.is_current, role_text(x)) for x in ctx.profile.roles]
    items += [(p.kind, p, p.start_date, p.end_date, False, project_text(p)) for p in ctx.profile.projects]
    for kind, item, start, end, current, text in items:
        label, value, who = _rel(ctx, trail, item.id)
        row = _row(kind, text, label=label, value=value, relevance=value, who=[who], source=_source(ctx, item))
        if kind != "role":
            if not entry:
                excluded.append({**row, "reason": "not entry level: only roles count"})
                continue
            if kind not in f.RECENCY_PROJECT_KINDS or (item.is_fork and not item.own_commits):
                excluded.append({**row, "reason": "fork with 0 own commits" if item.is_fork and not item.own_commits
                                 else f"kind '{kind}' is not eligible for Recency (project, publication, hackathon)"})
                continue
        end_idx = f._activity_end(start, end, current, ctx.today)
        if value is None:
            excluded.append({**row, "reason": "relevance judgment failed"})
            continue
        if end_idx is None:
            excluded.append({**row, "reason": "undated or in the future"})
            continue
        gap = max(0, now - end_idx) / 12
        activity = value * config.DECAY ** gap
        row["factors"] = {"ended": "present" if current else _dates(start, end).split(" to ")[1],
                          "years since": r(gap), "activity": r(activity)}
        if value == 0:
            excluded.append({**row, "reason": "relevance 0"})
            continue
        inputs.append(row)
        terms.append(f"{n(value)}×{config.DECAY}^{n(gap)} = {r(activity)}")
        best = activity if best is None else max(best, activity)
    arithmetic = [f"max({'; '.join(terms)}) = {r(best)}"] if terms else []
    if not terms and s.status == "scored":
        arithmetic = ["every dated activity has relevance 0 → 0.000"]
    return inputs, excluded, arithmetic


def _criteria(key: str, values: dict):
    from graph.nodes.expertise import ALL_BULLETS, PART, best_label, criterion_text

    def compute(ctx, trail, labels, s):
        kind = "tool" if key == "tool_evidence" else "skill"
        inputs, arithmetic, groups = [], [], {}
        for prio in ("required", "preferred"):
            vals = []
            for c in ctx.baseline.items(kind, prio):
                subjects = [a.subject_id for a in labels.asks if a.key == key and
                            (a.subject_id == c.id or a.subject_id == c.id + ALL_BULLETS
                             or a.subject_id.startswith(f"{c.id}{ALL_BULLETS}{PART}"))]
                found = [labels.get(key, sid) for sid in subjects]
                label = best_label(key, *found)
                value = values.get(label) if label else None
                who = []
                for sid in subjects:
                    w = trail.who(key, sid)
                    w["route"] = "alias match" if sid == c.id else "all bullets" + (
                        f" ({sid.split(PART, 1)[1][:8]}…)" if PART in sid else "")
                    who.append(w)
                inputs.append(_row(f"{prio} {kind}", criterion_text(c), label=label, value=value, who=who,
                                   factors={"routes": len(subjects), "best of": [x for x in found if x]}))
                if value is not None:
                    vals.append(value)
            groups[prio] = vals
            if vals:
                arithmetic.append(f"{prio[0].upper()} = ({' + '.join(n(v) for v in vals)}) / {len(vals)} = "
                                  f"{r(sum(vals) / len(vals))}")
        R = sum(groups["required"]) / len(groups["required"]) if groups.get("required") else None
        P = sum(groups["preferred"]) / len(groups["preferred"]) if groups.get("preferred") else None
        if R is not None and P is not None:
            arithmetic.append(f"(2R + P) / 3 = (2×{r(R)} + {r(P)}) / 3 = {r((2 * R + P) / 3)}")
        elif R is not None or P is not None:
            arithmetic.append(f"only {'required' if R is not None else 'preferred'} criteria: {r(R if R is not None else P)}")
        return inputs, [], arithmetic
    return compute


def _complexity(ctx, trail, labels, s):
    inputs, excluded, terms = [], [], []
    items = [("role", x, role_text(x)) for x in ctx.profile.roles] + \
        [(p.kind, p, project_text(p)) for p in ctx.counted_projects()]
    for kind, item, text in items:
        rl, rv, rwho = _rel(ctx, trail, item.id)
        label = labels.get("complexity", item.id)
        row = _row(kind, text, label=label, value=f.COMPLEXITY.get(label) if label else None, relevance=rv,
                   who=[rwho, trail.who("complexity", item.id)] if label else [rwho], source=_source(ctx, item))
        if not rv:
            excluded.append({**row, "reason": "relevance 0" if rv == 0 else "relevance judgment failed"})
        elif label is None:
            excluded.append({**row, "reason": "complexity judgment failed"})
        else:
            inputs.append(row)
            terms.append(n(row["value"]))
    for p in ctx.profile.projects:
        if p.is_fork and not p.own_commits:
            excluded.append(_row(p.kind, project_text(p), source=_source(ctx, p),
                                 extra={"reason": "fork with 0 own commits"}))
    arithmetic = [f"max({', '.join(terms)}) = {r(max(float(t) for t in terms))}"] if terms else []
    return inputs, excluded, arithmetic


def _work_samples(ctx, trail, labels, s):
    from graph.nodes.expertise import _work_samples as samples
    inputs, excluded, found = [], [], []
    for kind, p in samples(ctx):
        if p.is_fork and not p.own_commits:
            excluded.append(_row(kind, project_text(p), source=_source(ctx, p),
                                 extra={"reason": "fork with 0 own commits"}))
            continue
        label = labels.get("work_sample_relevant", p.id)
        row = _row(kind, project_text(p), label=label, value=1 if label == "yes" else 0 if label else None,
                   who=[trail.who("work_sample_relevant", p.id)], source=_source(ctx, p),
                   factors={"url": p.url} if p.url else None)
        inputs.append(row)
        if label == "yes":
            found.append(kind)
    arithmetic = [f"any relevant work sample: {'yes (' + ', '.join(sorted(set(found))) + ')' if found else 'no'} → "
                  f"{r(1.0 if found else 0.0)}"] if inputs else []
    return inputs, excluded, arithmetic


def _bullets(key_value, questions, name):
    from graph.nodes.authority import _bullets as bullets

    def compute(ctx, trail, labels, s):
        inputs, excluded, best = [], [], None
        for b, rel_label in bullets(ctx):
            rel = f.RELEVANCE.get(rel_label) if rel_label else None
            level, label = key_value(labels, b.id)
            row = _row("bullet", b.text, label=label, value=level, relevance=rel,
                       who=[trail.who(q, b.id) for q in questions], source=_source(ctx, b),
                       factors={"level × relevance": r(level * rel) if level is not None and rel is not None
                                else None})
            if rel is None:
                excluded.append({**row, "reason": "parent role or project relevance judgment failed"})
            elif level is None:
                excluded.append({**row, "reason": f"{name} judgment failed"})
            else:
                inputs.append(row)
                v = level * rel
                if best is None or v > best[0]:
                    best = (v, level, rel)
        arithmetic = [f"max over {len(inputs)} bullets of level × relevance = {n(best[1])} × {n(best[2])} = "
                      f"{r(best[0])}"] if best else []
        return inputs, excluded, arithmetic
    return compute


def _outcome(labels, sid):
    m, d = labels.yes("outcome_metric", sid), labels.yes("outcome_direction", sid)
    if m is None or d is None:
        return None, None
    return {2: 1.0, 1: 0.5, 0: 0.0}[int(m) + int(d)], f"metric {'yes' if m else 'no'}, direction {'yes' if d else 'no'}"


def _scope_of_impact(ctx, trail, labels, s):
    from graph.nodes.authority import _bullets as bullets
    expected = ctx.baseline.expected_reach
    inputs, excluded, best = [], [], None
    for b, rel_label in bullets(ctx):
        rel = f.RELEVANCE.get(rel_label) if rel_label else None
        label = labels.get("reach", b.id)
        reach = f.REACH.get(label) if label else None
        row = _row("bullet", b.text, label=label, value=reach, relevance=rel, who=[trail.who("reach", b.id)],
                   source=_source(ctx, b))
        if label is None:
            excluded.append({**row, "reason": "reach judgment failed"})
        elif reach is None:
            excluded.append({**row, "reason": "reach not stated"})
        elif config.SCOPE_OF_IMPACT_RELEVANCE_FILTER and not rel:
            excluded.append({**row, "reason": "relevance 0" if rel == 0 else "relevance judgment failed"})
        else:
            inputs.append(row)
            best = reach if best is None else max(best, reach)
    arithmetic = []
    if best is not None:
        gap = expected - best
        arithmetic = [f"best reach {best}, expected {expected}: gap {expected} − {best} = {gap} → "
                      f"{r(1.0 if gap <= 0 else 0.5 if gap == 1 else 0.0)} (≤0 → 1 · 1 → 0.5 · ≥2 → 0)"]
    return inputs, excluded, arithmetic


def _recognition(ctx, trail, labels, s):
    from graph.nodes.authority import _awards, _publications, award_text
    inputs, award, pub = [], False, False
    for c in _awards(ctx):
        label = labels.get("award_relevant", c.id)
        inputs.append(_row("award", award_text(c), label=label, value=1 if label == "yes" else 0 if label else None,
                           who=[trail.who("award_relevant", c.id)], source=_source(ctx, c)))
        award = award or label == "yes"
    for p in _publications(ctx):
        label = labels.get("publication_relevant", p.id)
        inputs.append(_row("publication", project_text(p), label=label, value=1 if label == "yes" else 0 if label else None,
                           who=[trail.who("publication_relevant", p.id)], source=_source(ctx, p),
                           factors={"url": p.url} if p.url else None))
        pub = pub or label == "yes"
    arithmetic = [f"(relevant award {int(award)} + relevant publication {int(pub)}) / 2 = {r((award + pub) / 2)}"] \
        if (award or pub) else []
    return inputs, [], arithmetic


def _proof_density(ctx, trail, labels, s):
    from graph.nodes.trust import _claims
    inputs, best = [], {}
    for c in _claims(ctx):
        label = labels.get("proof_support", c.id)
        value = f.PROOF_SUPPORT.get(label) if label else None
        inputs.append(_row(c.kind, c.name_raw, label=label, value=value, who=[trail.who("proof_support", c.id)],
                           source=_source(ctx, c), factors={"counted as": c.name_norm}))
        if c.name_norm not in best or (value is not None and (best[c.name_norm] is None or value > best[c.name_norm])):
            best[c.name_norm] = value
    usable = {k: v for k, v in best.items() if v is not None}
    arithmetic = []
    if usable:
        arithmetic = [f"best support per distinct claim, averaged: ({' + '.join(n(v) for v in usable.values())}) / "
                      f"{len(usable)} = {r(sum(usable.values()) / len(usable))}"]
        if len(best) < len(inputs):
            arithmetic.insert(0, f"{len(inputs)} claims, {len(best)} distinct names (the best support per name counts)")
    return inputs, [], arithmetic


def _validation(ctx, trail, labels, s):
    from scoring.matching import match_certification
    inputs, excluded = [], []
    ref_ok = cert_ok = False
    for ref in ctx.profile.references:
        label = labels.get("reference_confirms", ref.id)
        rel = (ref.relationship or "").lower()
        row = _row("reference", f"{ref.name} ({ref.relationship or 'relationship not stated'}): {ref.statement or ''}",
                   label=label, who=[trail.who("reference_confirms", ref.id)],
                   value=1 if rel in f.VALIDATING_RELATIONSHIPS and label == "confirms_relevant_work" else 0)
        if rel not in f.VALIDATING_RELATIONSHIPS:
            excluded.append({**row, "reason": f"relationship '{ref.relationship or 'not stated'}' is not a manager or "
                                              f"supervisor"})
        elif label != "confirms_relevant_work":
            excluded.append({**row, "reason": "does not confirm relevant work" if label else "judgment failed"})
        else:
            inputs.append(row)
            ref_ok = True
    criteria = [c for c in ctx.baseline.criteria if c.kind == "certification"]
    terms = "; ".join(f"{c.name} (also: {', '.join(c.aliases) or 'none'})" for c in criteria) or "none"
    for cert in ctx.profile.certifications:
        matched = match_certification(cert.name, ctx.baseline.criteria)
        row = _row("certification", cert.name + (f" — {cert.issuer}" if cert.issuer else ""),
                   label="matched" if matched else "not matched", value=0.5 if cert.verified and matched else 0,
                   who=[{"decided_by": "rule (code): name or alias found in the certification name",
                         "label": matched or "no criterion"}], source=_source(ctx, cert),
                   factors={"verified (credential ID or link)": cert.verified, "verify link": cert.verify_url,
                            "credential ID": cert.credential_id, "baseline criteria": terms})
        if not cert.verified:
            excluded.append({**row, "reason": "no credential ID or verification link"})
        elif not matched:
            excluded.append({**row, "reason": f"no certification criterion of this role matches the name "
                                              f"(criteria: {terms})"})
        else:
            inputs.append(row)
            cert_ok = True
    arithmetic = ["manager/supervisor reference confirming relevant work → 1.000" if ref_ok else
                  "verified certification matching a criterion → 0.500" if cert_ok else
                  "neither a confirming manager/supervisor reference nor a verified matching certification → missing"]
    return inputs, excluded, arithmetic


def _consistency(ctx, trail, labels, s):
    from graph.nodes.trust import _sources
    resume, sources = _sources(ctx)
    inputs = []
    for sid, kind, summary in sources:
        label = labels.get("source_alignment", sid)
        inputs.append(_row(kind, "\n".join(summary), label=label, value=1 if label == "aligned" else 0 if label else None,
                           who=[trail.who("source_alignment", sid)], factors={"compared with": "resume summary"}))
    any_ok = any(x["label"] == "aligned" for x in inputs)
    return inputs, [], ([f"any source aligned with the resume: {'yes' if any_ok else 'no'} → {r(1.0 if any_ok else 0.0)}"]
                        if inputs else [])


def _quality(ctx, trail, labels, s):
    from graph.nodes.trust import _quality_items
    names = ["tools", "numbers", "dates", "context", "own role"]
    inputs, excluded, values = [], [], []
    for kind, item_id, text in _quality_items(ctx):
        checks = [labels.yes(f"quality_check_{i}", item_id) for i in range(1, 6)]
        row = _row(kind, text, label=" · ".join(f"{nm} {'yes' if c else 'no' if c is not None else '?'}"
                                                for nm, c in zip(names, checks)),
                   value=sum(bool(c) for c in checks) / 5 if all(c is not None for c in checks) else None,
                   who=[trail.who(f"quality_check_{i}", item_id) for i in range(1, 6)])
        if row["value"] is None:
            excluded.append({**row, "reason": "a quality check failed"})
        else:
            inputs.append(row)
            values.append((sum(bool(c) for c in checks), row["value"]))
    arithmetic = [f"average of (yes checks / 5): ({' + '.join(f'{k}/5' for k, _ in values)}) / {len(values)} = "
                  f"{r(sum(v for _, v in values) / len(values))}"] if values else []
    return inputs, excluded, arithmetic


TRACES = {
    "relevance": _relevance, "depth": _depth, "scope": _scope, "progression": _progression, "recency": _recency,
    "role_skills": _criteria("skill_evidence", f.SKILL_EVIDENCE),
    "applied_knowledge": _criteria("applied_level", f.APPLIED_LEVEL),
    "tools": _criteria("tool_evidence", f.tool_evidence_values()),
    "complexity": _complexity, "work_samples": _work_samples,
    "outcomes": _bullets(_outcome, ("outcome_metric", "outcome_direction"), "outcome"),
    "impact": _bullets(lambda lab, sid: (f.IMPACT.get(lab.get("impact_type", sid)), lab.get("impact_type", sid)),
                       ("impact_type",), "impact"),
    "ownership": _bullets(lambda lab, sid: (f.OWNERSHIP.get(lab.get("ownership", sid)), lab.get("ownership", sid)),
                          ("ownership",), "ownership"),
    "scope_of_impact": _scope_of_impact, "recognition": _recognition, "proof_density": _proof_density,
    "validation_strength": _validation, "consistency": _consistency, "evidence_quality": _quality,
}


def _status_reason(sub: f.SubScore) -> str:
    if sub.status == "scored":
        return "scored: evidence was judged and the formula applied"
    if sub.status == "missing":
        return f"missing: no qualifying evidence, counts as 0 — {sub.guidance}"
    if sub.status == "na":
        return f"N/A: {sub.details.get('reason')} — left out of the average"
    return f"couldn't check: {sub.details.get('reason')} — left out of the average"


# --- The run ---------------------------------------------------------------------------------------------------------

def computation(db_path: str, run_id: str, weights: dict | None = None) -> dict:
    """The whole computation of one run. Never calls a model and never writes."""
    from graph.pipeline import DIMENSIONS, run_scores
    conn = db.connect(db_path)
    try:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if run is None:
            raise KeyError(run_id)
        weights = weights or json.loads(run["weights_json"])
        saved = run_scores(conn, run_id)
        models = [m[0] for m in conn.execute("SELECT DISTINCT model_version FROM judgments WHERE candidate_id = ?",
                                             (run["candidate_id"],))]
        cutoff = run["finished_at"] or datetime.now(timezone.utc).isoformat(timespec="seconds")
    finally:
        conn.close()
    judge = _SavedJudge(models or ["none"])
    deps = Deps(db_path, judge, today=_run_date(run), max_parallel_calls=1)
    state = {"candidate_id": run["candidate_id"], "baseline_id": run["baseline_id"], "run_id": run_id}
    subs = {}
    with open_ctx(deps, state, with_relevance=False) as ctx:
        ctx.svc = _UntilService(ctx.conn, judge, ctx.candidate_id, run_id, cutoff=cutoff)
        trail = Trail(ctx, cutoff)
        from graph.nodes.prepare import relevance_asks
        rel = trail.add(resolve(ctx, relevance_asks(ctx), call=False))
        ctx.relevance = {a.subject_id: rel.get("relevance", a.subject_id) for a in rel.asks}
        for dim, nodes in DIMENSIONS.items():
            for node in nodes:
                labels = trail.add(resolve(ctx, node.plan(ctx), call=False))
                again = node.compute(ctx, labels)
                inputs, excluded, arithmetic = TRACES[node.name](ctx, trail, labels, again)
                row = saved.get(node.name)
                same = row is not None and row["status"] == again.status and (
                    (row["score"] is None and again.score is None) or
                    (row["score"] is not None and again.score is not None and abs(row["score"] - again.score) < 1e-9))
                subs[node.name] = {"dimension": dim, "status": row["status"] if row else again.status,
                                   "score": row["score"] if row else again.score,
                                   "recomputed": {"status": again.status, "score": again.score},
                                   "matches_saved": same, "status_reason": _status_reason(again),
                                   "inputs": inputs, "excluded": excluded, "arithmetic": arithmetic}
    return {"run_id": run_id, "logic_version": run["logic_version"], "today": _run_date(run).isoformat(),
            "subcategories": subs, **aggregate_trace(saved, weights)}


def aggregate_trace(saved: dict, weights: dict) -> dict:
    """Σ(w·s)/Σw per dimension and 100·Σ(W·dim)/ΣW, with the weights shown on the page (code: scoring/aggregate.py)."""
    w = normalize_weights(weights)
    dims, out = {}, {}
    for d in config.DIMENSIONS:
        terms, excluded, num, den = [], [], 0.0, 0.0
        for s in config.SUBCATEGORIES[d]:
            row = saved.get(s)
            status = row["status"] if row else "error"
            raw_w = weights["subcategories"][d][s]
            if status not in INCLUDED_STATUSES:
                excluded.append({"subcategory": s, "label": config.SUBCATEGORY_LABELS[s], "weight": raw_w,
                                 "reason": "N/A" if status == "na" else "couldn't check (error)" if row else "not saved"})
                continue
            score = 0.0 if status == "missing" else row["score"]
            terms.append({"subcategory": s, "label": config.SUBCATEGORY_LABELS[s], "weight": raw_w, "score": score,
                          "missing": status == "missing"})
            num += w["subcategories"][d][s] * score
            den += w["subcategories"][d][s]
        dims[d] = num / den if den else None
        total_w = sum(t["weight"] for t in terms)
        text = (f"({' + '.join(f'{n(t['weight'])}×{r(t['score'])}' for t in terms)}) / "
                f"{n(total_w)} = {r(dims[d])}") if terms and total_w else "no included subcategory"
        out[d] = {"terms": terms, "excluded": excluded, "arithmetic": text, "value": dims[d]}
    used = [d for d in config.DIMENSIONS if dims[d] is not None]
    W = {d: weights["dimensions"][d] for d in config.DIMENSIONS}
    final = (100 * sum(w["dimensions"][d] * dims[d] for d in used) / sum(w["dimensions"][d] for d in used)
             if used and sum(w["dimensions"][d] for d in used) else None)
    final_text = (f"100 × ({' + '.join(f'{n(W[d])}×{r(dims[d])}' for d in used)}) / {n(sum(W[d] for d in used))} = "
                  f"{final:.1f}") if final is not None else "no dimension could be scored"
    return {"dimensions": out, "final": {"value": final, "arithmetic": final_text, "weights": W,
                                         "excluded": [d for d in config.DIMENSIONS if dims[d] is None]}}
