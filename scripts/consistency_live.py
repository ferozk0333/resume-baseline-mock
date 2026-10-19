"""Open question 45 live fix: the Consistency (source-alignment) input lists awards apart from skills.

    python scripts/consistency_live.py --estimate             # offline: questions and calls per model
    LIVE=1 LLM_PROVIDER=gemini RIGHTSTAFF_DB=data/demo.db python scripts/consistency_live.py --run
    RIGHTSTAFF_DB=data/demo.db python scripts/consistency_live.py --apply   # demo.db, replay only, no calls

Scope: every candidate with an award claim in a read document (only the owner's live profiles), each score they hold: the
latest run per baseline and every application. Scored on a copy of demo.db (data/consistency-live.db, resumable):
saved answers for the same input are reused, the rest is asked live; a daily limit stops cleanly. --apply copies the
new judgments into demo.db, re-scores the same scores with recorded answers only (a score that would need a new
question is left as it was and reported) and points applications at their new runs.
Writes timings/consistency_live.json / .md (timings/ is not in git: personal data).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

from core import config, db  # noqa: E402

DEMO = ROOT / "data" / "demo.db"
WORK = ROOT / "data" / "consistency-live.db"
ESTIMATE_DB = ROOT / "data" / "consistency-estimate.db"
OUT = ROOT / "timings" / "consistency_live.json"


def affected(conn) -> list[tuple[str, str]]:
    """(candidate id, name) with an award claim in any read document."""
    out = {}
    for cid, name, raw in conn.execute("SELECT c.id, c.name, e.extracted_json FROM evidence_items e JOIN candidates c "
                                       "ON c.id = e.candidate_id WHERE e.extracted_json IS NOT NULL"):
        if any(x.get("kind") == "award" for x in json.loads(raw).get("claims", [])):
            out[cid] = name
    return sorted(out.items(), key=lambda x: x[1] or "")


def targets(conn) -> list[dict]:
    from dedupe_live import _final
    rows, seen = [], set()
    for cid, name in affected(conn):
        for app_id, run_id, title, baseline, weights, started in conn.execute(
                "SELECT a.id, a.run_id, j.title, j.baseline_id, j.weights_json, r.started_at FROM applications a "
                "JOIN jobs j ON j.id = a.job_id JOIN runs r ON r.id = a.run_id WHERE a.candidate_id = ? AND "
                "r.status = 'done'", (cid,)):
            w = json.loads(weights) if weights else None
            rows.append({"key": f"{name} · job: {title}", "cid": cid, "run": run_id, "baseline": baseline, "app": app_id,
                         "today": started[:10], "weights": w, "before": _final(conn, run_id, w)})
            seen.add(run_id)
        for baseline, run_id, started in conn.execute(
                "SELECT r.baseline_id, r.id, r.started_at FROM runs r WHERE r.candidate_id = ? AND r.status = 'done' "
                "AND r.started_at = (SELECT MAX(x.started_at) FROM runs x WHERE x.candidate_id = r.candidate_id AND "
                "x.baseline_id = r.baseline_id AND x.status = 'done')", (cid,)):
            if run_id in seen:
                continue                                   # the same run as an application: covered above
            rows.append({"key": f"{name} · {baseline}", "cid": cid, "run": run_id, "baseline": baseline, "app": None,
                         "today": started[:10], "weights": None, "before": _final(conn, run_id, None)})
    return rows


def _copy(dst: Path) -> None:
    from parser_gates import db_copy
    db_copy(DEMO, dst)


def estimate() -> None:
    from parser_gates import score
    _copy(ESTIMATE_DB)
    conn = db.connect(str(ESTIMATE_DB))
    rows = targets(conn)
    conn.close()
    per_cid: dict[str, set] = {}
    for t in rows:
        _, j = score(str(ESTIMATE_DB), t["cid"], t["baseline"], dt.date.fromisoformat(t["today"]), (t["cid"],))
        per_cid.setdefault(t["cid"], set()).update(j.missed_inputs)
        t["missed"] = len(j.missed_inputs)
        t["questions"] = dict(Counter(k for k, _, _ in j.missed_inputs))
    union = set().union(*per_cid.values()) if per_cid else set()
    jev_q = Counter(k for k, _, r in union if not r and k != "complexity")
    route_q = Counter(k for k, _, r in union if r or k == "complexity")
    print(f"{len(rows)} scores to redo for {len(per_cid)} candidates:\n")
    for t in rows:
        print(f"  {t['key']}: {t['missed']} new question(s) {t['questions']}")
    conn = db.connect(str(ESTIMATE_DB))
    q = ",".join("?" * len(per_cid))
    low, total = conn.execute(f"SELECT SUM(used = 0), COUNT(*) FROM judgments WHERE method = 'jev' AND candidate_id IN "
                              f"({q})", list(per_cid)).fetchone()
    conn.close()
    share = (low or 0) / max(1, total or 0)
    n_route, fallbacks = sum(route_q.values()), round(sum(jev_q.values()) * share)
    print(f"\nUnique new questions: {len(union)}")
    print(f"  Jev: {sum(jev_q.values())} {dict(sorted(jev_q.items()))}")
    print(f"  route judge_single: {n_route} {dict(sorted(route_q.items()))} + Jev answers below the threshold ≈ {fallbacks}")
    from core.routing import LIMITER
    need = n_route + fallbacks
    print("\nPer model (route judge_single, in order; used today from provider_usage):")
    for m in config.ROUTES["judge_single"]:
        lim, used = config.model_limits(m), LIMITER.used_today(m)[0]
        left = max(0, lim.rpd - used) if lim.rpd else None
        take = min(need, left) if left is not None else need
        print(f"  {m}: limit {lim.rpd or '—'}/day, used today {used}, left {left if left is not None else '—'} → up to {take}")
        need -= take
    print(f"  Not covered today: {need}" if need > 0 else "  All fits within today's free limits.")


def run() -> None:
    if not config.LIVE:
        sys.exit("Live: set LIVE=1 LLM_PROVIDER=gemini RIGHTSTAFF_DB=data/demo.db.")
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.replay_judge import ReplayJudge
    from judges.service import get_judge
    from readme_live_gate import Hybrid
    from dedupe_live import _final
    if not WORK.exists():
        _copy(WORK)
    conn = db.connect(str(WORK))
    state = json.loads(OUT.read_text()) if OUT.exists() else {"targets": {t["key"]: t for t in targets(conn)}, "scores": {}}
    OUT.write_text(json.dumps(state, indent=1, default=str))
    live = get_judge()
    for key, t in state["targets"].items():
        if key in state["scores"]:
            continue
        judge = Hybrid(ReplayJudge(conn, (t["cid"],)), live)
        res = Pipeline(Deps(str(WORK), judge, today=dt.date.fromisoformat(t["today"]), planner=None),
                       print_timings=False).score(t["cid"], t["baseline"], plan=False)
        if res.get("paused") or not res.get("run_id"):
            print(f"Stopped at a daily limit while scoring {key}: {res.get('paused') or res}. Run again later.")
            break
        after = _final(conn, res["run_id"], t["weights"])
        state["scores"][key] = {"after": after, "after_run": res["run_id"], "asked": dict(judge.asked)}
        OUT.write_text(json.dumps(state, indent=1, default=str))
        print(f"{key}: {t['before']:.2f} → {after:.2f} (asked live {sum(judge.asked.values())})", flush=True)
    conn.close()
    report()


def report() -> None:
    from dedupe_live import _moves
    from parser_gates import fmt
    state = json.loads(OUT.read_text())
    conn = db.connect(str(WORK))
    lines = ["# Open question 45 live fix: awards apart from skills in the Consistency input", "",
             "| score | before | after | Δ | asked live |", "|---|---|---|---|---|"]
    for key, t in state["targets"].items():
        s = state["scores"].get(key)
        if not s:
            lines.append(f"| {key} | {fmt(t['before'], 2)} | not yet | | |")
            continue
        lines.append(f"| {key} | {t['before']:.2f} | {s['after']:.2f} | {s['after'] - t['before']:+.2f} | "
                     f"{sum(s['asked'].values())} |")
    lines += ["", "## What moved", ""]
    for key, t in state["targets"].items():
        s = state["scores"].get(key)
        if not s:
            continue
        moves = [m for m in _moves(conn, t["run"], s["after_run"], t["weights"]) if abs(m[3]) >= 0.05]
        if moves:
            lines.append(f"**{key}** ({t['before']:.2f} → {s['after']:.2f})")
            lines += [f"- {sub}: {fmt(x, 3)} → {fmt(y, 3)} ({pts:+.2f} pts)" for sub, x, y, pts in moves]
            lines.append("")
    asked = Counter()
    for s in state["scores"].values():
        asked.update(s["asked"])
    lines += [f"Asked live in all: {sum(asked.values())} {dict(sorted(asked.items()))}"]
    OUT.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    conn.close()


def apply() -> None:
    from dedupe_live import _final
    from parser_gates import score
    assert any(p.name.startswith("demo.db.bak-") for p in (ROOT / "data").iterdir()), "back up demo.db first"
    a = sqlite3.connect(str(DEMO))
    a.execute("ATTACH DATABASE ? AS live", (str(WORK),))
    cols = ", ".join(r[1] for r in a.execute("PRAGMA main.table_info(judgments)"))
    n0 = a.execute("SELECT COUNT(*) FROM main.judgments").fetchone()[0]
    with a:
        a.execute(f"INSERT OR IGNORE INTO main.judgments ({cols}) SELECT {cols} FROM live.judgments")
    copied = a.execute("SELECT COUNT(*) FROM main.judgments").fetchone()[0] - n0
    a.execute("DETACH DATABASE live")
    a.close()
    state = json.loads(OUT.read_text())
    out = {"judgments_copied": copied, "rescored": {}, "skipped": {}}
    for key, t in state["targets"].items():
        res, judge = score(str(DEMO), t["cid"], t["baseline"], dt.date.fromisoformat(t["today"]), (t["cid"],))
        if judge.missed_inputs or not res.get("run_id"):
            out["skipped"][key] = f"{len(judge.missed_inputs)} unrecorded question(s); kept run {t['run']}"
            continue
        conn = db.connect(str(DEMO))
        after = _final(conn, res["run_id"], t["weights"])
        if t.get("app"):
            with conn:
                conn.execute("UPDATE applications SET run_id = ? WHERE id = ?", (res["run_id"], t["app"]))
        conn.close()
        live_after = (state["scores"].get(key) or {}).get("after")
        out["rescored"][key] = {"before": t["before"], "after": after, "live_run": live_after, "run": res["run_id"]}
        print(f"{key}: {t['before']:.2f} → {after:.2f} (live run: {live_after})", flush=True)
    (ROOT / "timings" / "consistency_apply.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\njudgments copied {copied}; re-scored {len(out['rescored'])}; skipped {len(out['skipped'])}")
    for k, v in out["skipped"].items():
        print(f"  skipped {k}: {v}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    for f in ("estimate", "run", "apply", "report"):
        g.add_argument(f"--{f}", action="store_true")
    x = ap.parse_args()
    estimate() if x.estimate else run() if x.run else apply() if x.apply else report()
