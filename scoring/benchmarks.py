"""Benchmarks (specs/12): percentiles within a pool and "what higher scorers have".

Computed at read time from saved runs, so they change as soon as a new run is saved. Code only, no model.

Privacy: outputs hold counts, percentages and the fixed feature texts from peer_features.py. Nothing from
any other candidate's profile (text, quotes, names, employers, projects, skills) is read into an output.
"""
from __future__ import annotations

from dataclasses import dataclass

from core import config, db
from scoring.aggregate import aggregate
from scoring.peer_features import FEATURES

DIMENSIONS = config.DIMENSIONS
SCRIPTED_REASON = "This run used scripted test labels, so it is not compared with other candidates."


@dataclass
class Member:
    run_id: str
    candidate_id: str
    final: float | None
    dimensions: dict[str, float | None]
    features: dict[str, bool | None]


def _member(conn, run_id: str, candidate_id: str, weights: dict) -> Member:
    from graph.pipeline import run_scores
    scores = run_scores(conn, run_id)
    agg = aggregate(scores, weights)
    feats = {}
    for f in FEATURES:
        s = scores.get(f.subcategory)
        feats[f.key] = None if s is None or s["status"] == "error" else bool(f.value(s["details"] or {}))
    return Member(run_id, candidate_id, agg.final, agg.dimensions, feats)


def is_real_run(conn, run_id: str) -> bool:
    """Only runs judged by a real judge enter pools, and never a test variant. Runs recorded before judge_kind
    existed are scripted when any of their judgments came from the test judge, or the candidate is a test archetype."""
    import json
    row = conn.execute("SELECT r.models_json, c.data_source FROM runs r LEFT JOIN candidates c ON c.id = r.candidate_id "
                       "WHERE r.id = ?", (run_id,)).fetchone()
    if row is None:
        return False
    kind = json.loads(row["models_json"] or "{}").get("judge_kind")
    if row["data_source"] == "test_variant":           # a duplicate of another profile (robustness tests only)
        return False
    if kind is not None:
        return kind == "real"
    if row["data_source"] == "archetype":
        return False
    return conn.execute("SELECT 1 FROM judgments WHERE run_id = ? AND model_version LIKE 'fake-%' LIMIT 1",
                        (run_id,)).fetchone() is None


def baseline_pool(conn, baseline_id: str) -> list[Member]:
    """Latest finished run of each candidate against this baseline, same logic_version, default weights.
    Runs with scripted labels (test archetypes, FakeJudge) are left out."""
    rows = conn.execute(
        "SELECT r.id, r.candidate_id FROM runs r WHERE r.baseline_id = ? AND r.status = 'done' AND r.logic_version = ? "
        "AND r.started_at = (SELECT MAX(started_at) FROM runs x WHERE x.candidate_id = r.candidate_id "
        "AND x.baseline_id = r.baseline_id AND x.status = 'done' AND x.logic_version = r.logic_version) "
        "ORDER BY r.started_at", (baseline_id, config.LOGIC_VERSION)).fetchall()
    seen, out = set(), []
    for r in rows:
        if r["candidate_id"] not in seen and is_real_run(conn, r["id"]):
            seen.add(r["candidate_id"])
            out.append(_member(conn, r["id"], r["candidate_id"], config.default_weights()))
    return out


def job_pool(conn, job: dict) -> list[Member]:
    """The job's scored applicants, with the job's weights (recruiter view)."""
    rows = conn.execute("SELECT run_id, candidate_id FROM applications WHERE job_id = ? AND run_id IS NOT NULL",
                        (job["id"],)).fetchall()
    return [_member(conn, r["run_id"], r["candidate_id"], job["weights"]) for r in rows if is_real_run(conn, r["run_id"])]


def percentile(value: float, others: list[float]) -> float:
    """Share of the others with a lower score, ties counting half (0–100)."""
    lower = sum(1 for o in others if o < value)
    ties = sum(1 for o in others if o == value)
    return 100 * (lower + 0.5 * ties) / len(others)


def round5(x: float) -> int:
    return int(5 * round(x / 5))


def demo_pool() -> bool:
    return config.MIN_POOL < 20 or config.MIN_GROUP < 10


def percentiles(pool: list[Member], candidate_id: str) -> dict:
    """Final and per-dimension percentiles for one candidate within the pool (or why not shown)."""
    me = next((m for m in pool if m.candidate_id == candidate_id), None)
    base = {"pool_size": len(pool), "min_pool": config.MIN_POOL, "demo_pool": demo_pool()}
    if me is None:
        return {**base, "shown": False, "reason": "This run is not in the comparison pool."}
    if len(pool) < config.MIN_POOL:
        return {**base, "shown": False, "reason": "Not enough candidates for a comparison yet"}
    others = [m for m in pool if m.candidate_id != candidate_id]

    def one(get):
        mine = get(me)
        vals = [get(o) for o in others if get(o) is not None]
        if mine is None or not vals:
            return None
        return {"percentile": round(percentile(mine, vals)), "n": len(vals)}
    return {**base, "shown": True, "final": one(lambda m: m.final),
            "dimensions": {d: one(lambda m, d=d: m.dimensions.get(d)) for d in DIMENSIONS}}


def _median(values: list[float]) -> float:
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def peer_signals(pool: list[Member], candidate_id: str) -> dict:
    """Per dimension where the candidate is below the pool median: up to 3 features common among the
    candidates above the median that this candidate lacks (specs/12 §2)."""
    me = next((m for m in pool if m.candidate_id == candidate_id), None)
    out = {"min_group": config.MIN_GROUP, "demo_pool": demo_pool(), "dimensions": {}}
    if me is None or len(pool) < config.MIN_POOL:
        return out
    for d in DIMENSIONS:
        values = [m.dimensions.get(d) for m in pool if m.dimensions.get(d) is not None]
        mine = me.dimensions.get(d)
        if mine is None or not values:
            continue
        median = _median(values)
        if mine >= median:
            continue
        group = [m for m in pool if m.candidate_id != candidate_id and (m.dimensions.get(d) or 0) > median]
        if len(group) < config.MIN_GROUP:
            out["dimensions"][d] = {"suppressed": True, "group_size": None, "signals": []}
            continue
        signals = []
        for f in (f for f in FEATURES if f.dimension == d):
            known = [m.features[f.key] for m in group if m.features.get(f.key) is not None]
            if len(known) < config.MIN_GROUP or me.features.get(f.key) is not False:
                continue
            rate = round5(100 * sum(known) / len(known))
            if rate >= config.PEER_MIN_RATE:
                signals.append({"feature": f.key, "subcategory": f.subcategory, "rate": rate,
                                "text": f"Among higher-scoring candidates for this role, {rate}% {f.text}. You don't yet."})
        signals.sort(key=lambda s: -s["rate"])
        out["dimensions"][d] = {"suppressed": False, "group_size": len(group), "signals": signals[:3]}
    return out


def for_run(conn, run_id: str) -> dict:
    run = conn.execute("SELECT candidate_id, baseline_id FROM runs WHERE id = ?", (run_id,)).fetchone()
    pool = baseline_pool(conn, run["baseline_id"])
    baseline = db.get_baseline(conn, run["baseline_id"])
    pct = percentiles(pool, run["candidate_id"])
    if not is_real_run(conn, run_id):
        pct.update(shown=False, reason=SCRIPTED_REASON)
    return {"baseline_title": baseline.title if baseline else None, "percentiles": pct,
            "peer_signals": peer_signals(pool, run["candidate_id"]) if is_real_run(conn, run_id)
            else {"min_group": config.MIN_GROUP, "demo_pool": demo_pool(), "dimensions": {}}}


def plan_lines(conn, run_id: str) -> dict[str, list[str]]:
    """subcategory → "what higher scorers have" lines, given to the plan model with the matching gap."""
    out: dict[str, list[str]] = {}
    for v in for_run(conn, run_id)["peer_signals"]["dimensions"].values():
        for s in v["signals"]:
            out.setdefault(s["subcategory"], []).append(s["text"])
    return out
