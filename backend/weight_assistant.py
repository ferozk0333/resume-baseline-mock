"""Recruiter weight assistant (specs/21): describe what you're looking for → suggested weights.

The model suggests, the recruiter decides, code does the math. One WEIGHTS_MODEL call per suggestion (the plan
route's models in order); its output is checked, bounded, normalized and rounded here. Nothing changes until the
recruiter clicks Apply; Undo restores the previous weights. The prompt never contains candidate or applicant data:
only the framework, the job's title, level and required years, its weights and the recruiter's text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from core import config, db
from core.db import new_id, now_iso
from providers.llm import LLMError
from core.text import wrap_tag

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
SCREEN = ROOT / "config" / "weight_text_screen.yaml"
RECRUITER = "demo-recruiter"            # the demo has no sign-in

UNAVAILABLE = "Suggestions are unavailable right now. Use a preset or the sliders."
INVALID = "Couldn't produce a suggestion. Try rephrasing or use a preset."
IGNORED_NOTICE = "Some parts of your description can't be used for ranking and were ignored:"

# What each weight measures and what raising it does to ranking (shared, model-neutral prompt; specs/21 §2).
DEFINITIONS = {
    "experience": "Work history that matches the role. Raising it ranks applicants with more, longer and more recent relevant work higher.",
    "expertise": "Skills and tools shown in practice. Raising it ranks applicants who demonstrate the role's skills, tools and complex work higher.",
    "authority": "Results and ownership. Raising it ranks applicants with measurable outcomes, business impact and wider reach higher.",
    "trust": "How well claims are backed. Raising it ranks applicants whose claims are supported by artifacts, references, certifications and consistent sources higher.",
    "relevance": "Share of work time in roles related to this one.",
    "depth": "Years of relevant work against the required years (projects count at entry level).",
    "scope": "Whether the most recent relevant role matches the expected scope of responsibility.",
    "progression": "Whether later roles carry more responsibility than earlier ones.",
    "recency": "How recently the applicant did relevant work.",
    "role_skills": "Evidence for each required and preferred skill of the role.",
    "applied_knowledge": "Skills applied to a task with a stated result, not just mentioned.",
    "tools": "Evidence of using the role's tools and technologies.",
    "complexity": "The hardest relevant problem the applicant worked on, against the role's complexity bar.",
    "work_samples": "A relevant repository, portfolio, presentation or publication to look at.",
    "outcomes": "Bullets with a measurable result (a number and a direction).",
    "impact": "Work with business or operational effect.",
    "ownership": "Work the applicant led rather than contributed to.",
    "scope_of_impact": "How far the work reached: own work, team, other teams, the organization, outside.",
    "recognition": "Relevant awards and publications.",
    "proof_density": "Share of claimed skills backed by an artifact or a bullet.",
    "validation_strength": "A manager reference confirming relevant work, or a verified relevant certification.",
    "consistency": "Whether LinkedIn, GitHub or a portfolio agree with the resume.",
    "evidence_quality": "How specific the entries are: tools, numbers, dates, context and own role.",
}


# --- Output schema ------------------------------------------------------------------------------------------------

class DimWeight(BaseModel):
    key: str
    weight: float


class SubWeight(BaseModel):
    dimension: str
    key: str
    weight: float


class Reason(BaseModel):
    key: str
    reason: str


class WeightsOut(BaseModel):
    dimensions: list[DimWeight]
    subcategories: list[SubWeight]
    reasons: list[Reason] = Field(default_factory=list)
    summary: str = ""
    ignored: list[str] = Field(default_factory=list)


class InvalidSuggestion(ValueError):
    pass


# --- Weights math (code only) ----------------------------------------------------------------------------------------

def normalized(weights: dict) -> dict:
    """Each group scaled to sum to 100 (an all-zero group stays as it is)."""
    def norm(g: dict) -> dict:
        t = sum(g.values())
        return {k: (100 * v / t if t else v) for k, v in g.items()}
    return {"dimensions": norm(weights["dimensions"]),
            "subcategories": {d: norm(weights["subcategories"][d]) for d in config.DIMENSIONS}}


def _bounded(vals: dict[str, float], lo: float, hi: float) -> dict[str, float]:
    """Scale to 100 with every value in [lo, hi]: values outside are pinned to the bound, the rest rescaled."""
    fixed: dict[str, float] = {}
    while True:
        free = {k: v for k, v in vals.items() if k not in fixed}
        rest = 100 - sum(fixed.values())
        total = sum(free.values())
        scaled = {k: (v * rest / total if total else rest / len(free)) for k, v in free.items()}
        out = {k: (lo if x < lo else hi) for k, x in scaled.items() if x < lo - 1e-9 or x > hi + 1e-9}
        if not out:
            return {**fixed, **scaled}
        fixed.update(out)


def _rounded(vals: dict[str, float], step: float, hi: float = 100) -> dict[str, float]:
    """Steps of `step` summing to exactly 100: floors first, the remaining steps to the largest remainders."""
    steps = {k: math.floor(v / step + 1e-9) for k, v in vals.items()}
    left = round(100 / step) - sum(steps.values())
    order = sorted(vals, key=lambda k: (-(vals[k] / step - steps[k]), k))
    i = 0
    while left > 0 and i < 10 * len(order):
        k = order[i % len(order)]
        if (steps[k] + 1) * step <= hi:
            steps[k] += 1
            left -= 1
        i += 1
    return {k: float(s * step) for k, s in steps.items()}


def _group(suggested: dict[str, float], current: dict[str, float], lo: float, hi: float) -> dict[str, float]:
    """A group the suggestion leaves alone (every weight within half a step) keeps its current values; otherwise
    it is bounded, normalized and rounded to WEIGHT_STEP."""
    step = config.WEIGHT_STEP
    bounded = _bounded(suggested, lo, hi)
    in_bounds = all(lo - 1e-9 <= v <= hi + 1e-9 for v in current.values())
    if in_bounds and all(abs(bounded[k] - current[k]) < step / 2 for k in current):
        return dict(current)
    return _rounded(bounded, step, hi)


def validate(out: WeightsOut, current: dict) -> dict:
    """Known keys only; numbers must be finite, ≥ 0 and ≤ 100; missing keys keep their current values; a group may
    not add up to 0. Returns the bounded, normalized, rounded weights. Raises InvalidSuggestion."""
    cur = normalized(current)
    dims = dict(cur["dimensions"])
    subs = {d: dict(cur["subcategories"][d]) for d in config.DIMENSIONS}
    owner = {s: d for d in config.DIMENSIONS for s in config.SUBCATEGORIES[d]}

    def number(x, what):
        if not isinstance(x, (int, float)) or not math.isfinite(x) or x < 0 or x > 100:
            raise InvalidSuggestion(f"{what}: {x!r} is not a weight between 0 and 100")
        return float(x)
    raw_dims = {w.key: number(w.weight, w.key) for w in out.dimensions if w.key in dims}
    raw_subs: dict[str, dict[str, float]] = {}
    for w in out.subcategories:
        if w.key in owner:
            raw_subs.setdefault(owner[w.key], {})[w.key] = number(w.weight, w.key)
    dims = {k: raw_dims.get(k, v) for k, v in dims.items()}               # a missing key keeps its current value
    for d, given in raw_subs.items():
        subs[d] = {k: given.get(k, v) for k, v in subs[d].items()}
    for name, group in [("dimensions", dims), *subs.items()]:
        if sum(group.values()) <= 0:
            raise InvalidSuggestion(f"{name}: every weight is 0")
    n = normalized({"dimensions": dims, "subcategories": subs})
    return {"dimensions": _group(n["dimensions"], cur["dimensions"], config.WEIGHT_DIM_MIN, config.WEIGHT_DIM_MAX),
            "subcategories": {d: _group(n["subcategories"][d], cur["subcategories"][d], 0, 100)
                              for d in config.DIMENSIONS}}


def _words(text: str, n: int) -> str:
    w = (text or "").split()
    return " ".join(w[:n]) + ("…" if len(w) > n else "")


# --- Fairness screen ---------------------------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _screen() -> list[tuple[str, re.Pattern]]:
    data = yaml.safe_load(SCREEN.read_text())
    return [(cat, re.compile(p, re.I)) for cat, pats in data["categories"].items() for p in pats]


def screen(text: str) -> list[str]:
    """Phrases of the recruiter's text the reviewed keyword list says are not job-related (exact as written)."""
    found = []
    for _, pat in _screen():
        for m in pat.finditer(text or ""):
            found.append(m.group(0))
    return list(dict.fromkeys(found))


def merge_ignored(model: list[str], code: list[str], text: str) -> list[str]:
    """The model's list (only phrases that occur in the text) plus every keyword hit it missed."""
    low = (text or "").lower()
    out = [p.strip() for p in model if p and p.strip() and p.strip().lower() in low]
    for hit in code:
        if not any(hit.lower() in p.lower() for p in out):
            out.append(hit)
    return list(dict.fromkeys(out))


# --- The prompt ---------------------------------------------------------------------------------------------------

def _table(weights: dict) -> str:
    w = normalized(weights)
    lines = [f"- {d}: {w['dimensions'][d]:.0f}" for d in config.DIMENSIONS]
    for d in config.DIMENSIONS:
        lines.append(f"  {d} subcategories: " + ", ".join(f"{s} {v:.0f}" for s, v in w["subcategories"][d].items()))
    return "\n".join(lines)


def build_user(job: dict, baseline, current: dict, text: str) -> str:
    """Everything the model sees. Never any candidate or applicant data (specs/21 §2)."""
    parts = ["The EEAT framework: applicants are scored on four dimensions, each the weighted average of its "
             "subcategories; the final score is the weighted average of the dimensions.", "", "Dimensions and subcategories (key: what it measures):"]
    for d in config.DIMENSIONS:
        parts.append(f"- {d}: {DEFINITIONS[d]}")
        parts += [f"  - {s}: {DEFINITIONS[s]}" for s in config.SUBCATEGORIES[d]]
    parts += ["", "Current weights of this job (each group out of 100):", _table(current), "",
              "Team default weights:", _table(config.default_weights()), "",
              f"Job: {job['title']} · level {baseline.level} · required years {baseline.required_years:g}", "",
              wrap_tag("recruiter_text", text)]
    return "\n".join(parts)


# --- Suggest --------------------------------------------------------------------------------------------------------

def _cache_key(text: str, current: dict, baseline, model: str) -> str:
    cur = normalized(current)
    cur = {"dimensions": {k: round(v, 4) for k, v in cur["dimensions"].items()},
           "subcategories": {d: {k: round(v, 4) for k, v in g.items()} for d, g in cur["subcategories"].items()}}
    payload = json.dumps([" ".join((text or "").lower().split()), cur, baseline.content_hash(), model], sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _ask(llm, user: str) -> tuple[WeightsOut | None, str | None, str]:
    """(output, model, status) over the route's models in order. An unavailable model fails over as usual; an invalid
    answer (schema or code checks) is retried once on the next model, then status "invalid"."""
    from pydantic import ValidationError
    from providers.llm import DailyLimitReached
    from providers.routing import Overloaded, Throttled, Unusable, models_of
    from extraction.extract import load_prompt
    busy = (Overloaded, Throttled, Unusable, DailyLimitReached)
    invalid = 0
    for m in models_of(config.WEIGHTS_MODEL):
        try:
            return llm.parse(model=m, system=load_prompt("weights_system"), user=user, output_type=WeightsOut), m, "ok"
        except (ValidationError, InvalidSuggestion) as exc:
            reason = exc
        except Exception as exc:                         # noqa: BLE001 - the router's error says why this model failed
            if isinstance(exc, busy) or isinstance(exc.__cause__, busy) or not isinstance(exc, LLMError):
                log.warning("weights: %s unavailable (%s)", m, str(exc)[:200])
                continue
            reason = exc
        invalid += 1
        log.warning("weights: %s gave no valid answer (%s)", m, str(reason)[:200])
        if invalid >= 2:
            return None, None, "invalid"
    return None, None, "invalid" if invalid else "unavailable"


def suggest(conn, llm, job: dict, text: str, *, fresh: bool = False) -> dict:
    """One suggestion: cached by (normalized text, current weights, baseline hash, model) unless `fresh`."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Describe what you're looking for first.")
    if len(text) > config.WEIGHT_TEXT_MAX:
        raise ValueError(f"Keep the description under {config.WEIGHT_TEXT_MAX} characters.")
    baseline = db.get_baseline(conn, job["baseline_id"])
    current = job["weights"]
    key = _cache_key(text, current, baseline, config.WEIGHTS_MODEL)
    hit = None if fresh else conn.execute(
        "SELECT * FROM weight_suggestions WHERE cache_key = ? AND weights_json IS NOT NULL ORDER BY created_at DESC, "
        "rowid DESC LIMIT 1", (key,)).fetchone()
    if hit is not None:
        row = {"weights": json.loads(hit["weights_json"]), "reasons": json.loads(hit["reasons_json"]),
               "summary": hit["summary"], "ignored": json.loads(hit["ignored_json"]), "model": hit["model"]}
        cached = 1
    else:
        if llm is None:
            return {"status": "unavailable", "message": UNAVAILABLE}
        user = build_user(job, baseline, current, text)
        out, model, status = _ask(_Checked(llm, current), user)
        if status != "ok":
            return {"status": status, "message": UNAVAILABLE if status == "unavailable" else INVALID}
        weights = validate(out, current)
        reasons = {}
        for r in out.reasons:
            if r.key in config.SUBCATEGORY_DIMENSION or r.key in config.DIMENSIONS:
                reasons[r.key] = _words(r.reason, 20)
        row = {"weights": weights, "reasons": reasons, "summary": _words(out.summary, 40),
               "ignored": merge_ignored(out.ignored, screen(text), text), "model": model}
        cached = 0
    sid = new_id()
    with conn:
        conn.execute("INSERT INTO weight_suggestions (id, job_id, recruiter, text, model, cache_key, cached, weights_json, "
                     "reasons_json, summary, ignored_json, current_weights_json, created_at) VALUES "
                     "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (sid, job["id"], RECRUITER, text, row["model"], key, cached, json.dumps(row["weights"]),
                      json.dumps(row["reasons"]), row["summary"], json.dumps(row["ignored"]), json.dumps(current),
                      now_iso()))
    return {"status": "ok", "id": sid}


class _Checked:
    """Runs the code validation right after each model answers, so an invalid answer is retried on the next model."""

    def __init__(self, llm, current: dict):
        self.llm, self.current = llm, current

    def parse(self, **kw):
        out = self.llm.parse(**kw)
        validate(out, self.current)
        return out


# --- Showing a suggestion ------------------------------------------------------------------------------------------

def get(conn, job_id: str, sid: str) -> dict | None:
    row = conn.execute("SELECT * FROM weight_suggestions WHERE id = ? AND job_id = ?", (sid, job_id)).fetchone()
    return dict(row) if row else None


def view(conn, job: dict, row: dict) -> dict:
    """The change table against the job's weights now, the ranking preview and the ignored notice."""
    weights = json.loads(row["weights_json"])
    reasons = json.loads(row["reasons_json"] or "{}")
    cur = normalized(job["weights"])
    changes, unchanged = [], []

    def item(key, label, dimension, before, after):
        entry = {"key": key, "label": label, "dimension": dimension, "current": round(before, 1), "suggested": after}
        if abs(after - before) >= 0.05:
            entry["reason"] = reasons.get(key) or "Adjusted so the group adds up to 100."
            changes.append(entry)
        else:
            unchanged.append(entry)
    for d in config.DIMENSIONS:
        item(d, d.capitalize(), None, cur["dimensions"][d], weights["dimensions"][d])
    for d in config.DIMENSIONS:
        for s in config.SUBCATEGORIES[d]:
            item(s, config.SUBCATEGORY_LABELS[s], d, cur["subcategories"][d][s], weights["subcategories"][d][s])
    ignored = json.loads(row["ignored_json"] or "[]")
    return {"id": row["id"], "text": row["text"], "created_at": row["created_at"], "summary": row["summary"],
            "label": "Suggested by AI · you decide", "weights": weights, "changes": changes, "unchanged": unchanged,
            "ignored": ignored, "ignored_notice": f"{IGNORED_NOTICE} {', '.join(f'“{p}”' for p in ignored)}." if ignored
            else None, "applied": bool(row["applied"]), "applied_at": row["applied_at"], "undone_at": row["undone_at"],
            "preview": preview(conn, job, weights)}


def _ranked(conn, job: dict, weights: dict) -> list[dict]:
    """Scored applicants ranked exactly as the applicant list ranks them with these weights (no model call)."""
    from graph.pipeline import rescore
    rows = []
    for a in conn.execute("SELECT a.id, a.run_id, c.name FROM applications a JOIN candidates c ON c.id = a.candidate_id "
                          "WHERE a.job_id = ? AND a.run_id IS NOT NULL", (job["id"],)).fetchall():
        agg = rescore(conn, a["run_id"], weights)
        if agg.final is not None:
            rows.append({"application_id": a["id"], "name": a["name"], "match": agg.final})
    rows.sort(key=lambda r: (-r["match"], r["application_id"]))
    return rows


def preview(conn, job: dict, weights: dict) -> dict:
    """Top 10 before → after with movement, and how many move up, down or stay (saved scores only)."""
    before = _ranked(conn, job, job["weights"])
    after = _ranked(conn, job, weights)
    if len(after) < 2:
        return {"available": False, "message": "No applicants to preview yet"}
    rank_before = {r["application_id"]: i + 1 for i, r in enumerate(before)}
    match_before = {r["application_id"]: r["match"] for r in before}
    rows = []
    for i, r in enumerate(after):
        was = rank_before[r["application_id"]]
        rows.append({**r, "rank": i + 1, "was": was, "move": was - (i + 1), "match_before": match_before[r["application_id"]]})
    return {"available": True, "top": rows[:10], "up": sum(r["move"] > 0 for r in rows),
            "down": sum(r["move"] < 0 for r in rows), "same": sum(r["move"] == 0 for r in rows), "total": len(rows)}


# --- Apply, undo, history -------------------------------------------------------------------------------------------

def apply(conn, job: dict, row: dict) -> dict:
    """The job's weights become the suggestion (re-aggregation only, no model call); the previous ones are kept."""
    from backend import recruiter as rc
    previous = job["weights"]
    rc.update_job(conn, job["id"], weights=json.loads(row["weights_json"]))
    with conn:
        conn.execute("UPDATE weight_suggestions SET applied = 1, applied_at = ?, previous_weights_json = ?, "
                     "undone_at = NULL WHERE id = ?", (now_iso(), json.dumps(previous), row["id"]))
    return rc.get_job(conn, job["id"])


def undo(conn, job: dict, row: dict) -> dict:
    from backend import recruiter as rc
    if not row["applied"] or not row["previous_weights_json"]:
        raise ValueError("This suggestion was not applied.")
    rc.update_job(conn, job["id"], weights=json.loads(row["previous_weights_json"]))
    with conn:
        conn.execute("UPDATE weight_suggestions SET applied = 0, undone_at = ? WHERE id = ?", (now_iso(), row["id"]))
    return rc.get_job(conn, job["id"])


def history(conn, job_id: str, n: int = 5) -> list[dict]:
    return [{"id": r["id"], "created_at": r["created_at"], "text": _words(r["text"], 12), "applied": bool(r["applied"]),
             "undone": bool(r["undone_at"])}
            for r in conn.execute("SELECT * FROM weight_suggestions WHERE job_id = ? ORDER BY created_at DESC, rowid DESC "
                                  "LIMIT ?", (job_id, n))]
