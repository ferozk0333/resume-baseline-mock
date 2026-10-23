"""Phase C live run (specs/19 item 19, review GO): true after-scores once duplicates are merged.

    python scripts/dedupe_live.py --estimate                  # offline: calls per model, before anything is asked
    LIVE=1 LLM_PROVIDER=gemini RIGHTSTAFF_DB=data/demo.db python scripts/dedupe_live.py --run
    python scripts/dedupe_live.py --report                    # offline: rebuild the report from the saved results

Scope (the owner's go): the 16 test profiles (synthetic) against their own role, the live profile named by
LIVE_PROFILE against its own role, and the demo-job scores of the profile named by JOBS_PROFILE (env; both optional). Works on a copy of
data/demo.db (data/dedupe-live.db, kept between runs so the run is resumable). Each candidate is re-merged with
extraction.store.remerge, exactly as step 4 does on demo.db, then scored: a question with a saved answer for the same
input reuses it (this candidate's own first), anything else is asked live through the usual judges and routes. At a
provider's daily limit the run stops cleanly; run it again on a later day to finish. Daily usage is shared with the
live API (provider_usage in RIGHTSTAFF_DB).
Writes timings/dedupe_live.json and timings/dedupe_live.md (timings/ is not in git: personal data).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

from core import config, db  # noqa: E402
from extraction import store  # noqa: E402

WORK = ROOT / "data" / "dedupe-live.db"
ESTIMATE_DB = ROOT / "data" / "dedupe-estimate.db"
OUT = ROOT / "timings" / "dedupe_live.json"
LIVE_NAME = os.getenv("LIVE_PROFILE", "")       # a live profile scored against its own role (optional)
JOBS_NAME = os.getenv("JOBS_PROFILE", "")       # a live profile whose demo-job scores are included (optional)
STUFFER_IDS = ("AI2", "DS2", "PM2", "DS4_padded")
CAUSE = {   # what a merge changes in each subcategory (specs/19 Phase C; formulas unchanged)
    "depth": "fewer double-counted projects (a resume project and its repository are one item)",
    "recency": "fewer double-counted projects",
    "complexity": "re-judged on the merged project text",
    "work_samples": "re-judged on the merged project (repository and resume project together)",
    "proof_density": "merged claims (one claim per name; the average is over fewer claims)",
    "tools": "skills list without duplicate names; tool questions re-asked on the new context",
    "role_skills": "skills list without duplicate names; skill questions re-asked on the new context",
    "applied_knowledge": "skills list without duplicate names; re-asked on the new context",
    "validation_strength": "merged certifications (credential ID or link kept from either copy)",
    "consistency": "re-asked on the merged profile",
    "evidence_quality": "re-asked on the merged profile",
    "outcomes": "bullets of merged items re-judged together",
    "ownership": "bullets of merged items re-judged together",
    "reach": "bullets of merged items re-judged together",
    "relevance": "re-judged on merged items",
    "recognition": "re-judged on merged items",
}


def targets(conn) -> list[dict]:
    """One row per (candidate, score): the test profiles' own-role runs, LIVE_PROFILE's own role, JOBS_PROFILE's jobs."""
    from parser_gates import final_of
    from report_test_profiles import profile_id
    from score_test_profiles import targets as test_targets
    out = []
    for a in test_targets(conn):
        if a["run_id"]:
            run = conn.execute("SELECT started_at, baseline_id FROM runs WHERE id = ?", (a["run_id"],)).fetchone()
            out.append({"key": profile_id(conn, a["candidate_id"]), "cid": a["candidate_id"], "run": a["run_id"],
                        "app": a["id"],
                        "baseline": run[1], "today": run[0][:10], "weights": None, "kind": "test",
                        "before": final_of(conn, a["run_id"])[0].final})
    row = conn.execute("SELECT id FROM candidates WHERE name = ? ORDER BY created_at DESC LIMIT 1", (LIVE_NAME,)).fetchone()
    if row:
        for b, run_id, started in conn.execute(
                "SELECT baseline_id, id, MAX(started_at) FROM runs WHERE candidate_id = ? AND status = 'done' "
                "GROUP BY baseline_id", (row[0],)):
            out.append({"key": f"{LIVE_NAME} · {b}", "cid": row[0], "run": run_id, "baseline": b, "app": None,
                        "today": started[:10], "weights": None, "kind": "live",
                        "before": final_of(conn, run_id)[0].final})
    for r in conn.execute("SELECT a.run_id, a.candidate_id, j.title, j.baseline_id, j.weights_json, ru.started_at, a.id "
                          "FROM applications a JOIN jobs j ON j.id = a.job_id JOIN candidates c ON c.id = a.candidate_id "
                          "JOIN runs ru ON ru.id = a.run_id WHERE c.name = ?", (JOBS_NAME,)):
        w = json.loads(r[4]) if r[4] else None
        out.append({"key": f"{JOBS_NAME} · job: {r[2]}", "cid": r[1], "run": r[0], "baseline": r[3], "app": r[6],
                    "today": r[5][:10], "weights": w, "kind": "job", "before": _final(conn, r[0], w)})
    return out


def _final(conn, run_id: str, weights) -> float | None:
    from backend.recruiter import rescore
    from parser_gates import final_of
    if weights:
        agg = rescore(conn, run_id, weights)
        return agg.final if agg else None
    return final_of(conn, run_id)[0].final


def _copy(dst: Path) -> None:
    from parser_gates import db_copy
    db_copy(ROOT / "data" / "demo.db", dst)


# --- Estimate -----------------------------------------------------------------------------------------------------

def estimate() -> None:
    from parser_gates import score
    _copy(ESTIMATE_DB)
    conn = db.connect(str(ESTIMATE_DB))
    rows = targets(conn)
    merged_cids, per_cid, merges = set(), {}, {}
    for t in rows:
        if t["cid"] not in merged_cids:
            merges[t["cid"]] = store.remerge(conn, t["cid"])["merged_away"]
            merged_cids.add(t["cid"])
    conn.close()
    for t in rows:
        res, j = score(str(ESTIMATE_DB), t["cid"], t["baseline"], dt.date.fromisoformat(t["today"]), (t["cid"],))
        per_cid.setdefault(t["cid"], set()).update(j.missed_inputs)
        t["missed"] = len(j.missed_inputs)
    union = set().union(*per_cid.values()) if per_cid else set()
    jev_q = Counter(k for k, _, r in union if not r and k != "complexity")
    route_q = Counter(k for k, _, r in union if r or k == "complexity")
    conn = db.connect(str(ESTIMATE_DB))
    cids = list(per_cid)
    q = ",".join("?" * len(cids))
    low, total = conn.execute(f"SELECT SUM(used = 0), COUNT(*) FROM judgments WHERE method = 'jev' AND candidate_id IN ({q})",
                              cids).fetchone()
    share = (low or 0) / max(1, total or 0)
    conn.close()
    n_jev, n_route = sum(jev_q.values()), sum(route_q.values())
    fallbacks = round(n_jev * share)
    print(f"{len(rows)} scores to redo ({sum(1 for t in rows if t['kind'] == 'test')} test profiles, "
          f"{sum(1 for t in rows if t['kind'] == 'live')} for {LIVE_NAME}, {sum(1 for t in rows if t['kind'] == 'job')} "
          f"demo-job scores of {JOBS_NAME}).\n")
    for t in rows:
        print(f"  {t['key']}: {t['missed']} new question(s)")
    print(f"\nUnique new questions (answers shared across a candidate's scores counted once): {len(union)}")
    print(f"  Jev (paid, the usual judge): {n_jev} question(s) {dict(sorted(jev_q.items()))}")
    print(f"  route judge_single: {n_route} question(s) {dict(sorted(route_q.items()))}")
    print(f"  + Jev answers below the confidence threshold go to the route: {share:.0%} of past Jev answers ≈ "
          f"{fallbacks} more")
    from core.routing import LIMITER
    print("\nPer model (route judge_single, in order; free-tier daily limits; used today from provider_usage):")
    need = n_route + fallbacks
    for m in config.ROUTES["judge_single"]:
        lim = config.model_limits(m)
        used = LIMITER.used_today(m)[0]
        left = max(0, lim.rpd - used) if lim.rpd else None
        take = min(need, left) if left is not None else need
        print(f"  {m}: limit {lim.rpd or '—'}/day, used today {used}, left {left if left is not None else '—'} → "
              f"expected up to {take}")
        need -= take
    print(f"  Not covered today: {need}" if need > 0 else "  All fits within today's free limits.")
    print("  (Route calls may be fewer: all-bullets asks for one question are batched into one call.)")
    print("\nMerges per candidate (what remerge removes):")
    names = {t["cid"]: t["key"].split(" · ")[0] for t in rows}
    for cid, m in merges.items():
        print(f"  {names[cid]}: " + ("; ".join(f"{k}: {', '.join(v)}" for k, v in m.items()) or "nothing"))


# --- Run ----------------------------------------------------------------------------------------------------------

def run() -> None:
    if not config.LIVE:
        sys.exit("Live: set LIVE=1 LLM_PROVIDER=gemini RIGHTSTAFF_DB=data/demo.db, and only after the go.")
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.replay_judge import ReplayJudge
    from judges.service import get_judge
    from readme_live_gate import Hybrid
    if not WORK.exists():
        _copy(WORK)
    state = json.loads(OUT.read_text()) if OUT.exists() else {"merged": {}, "scores": {}}
    conn = db.connect(str(WORK))
    rows = [t for t in targets(conn) if t["key"] not in state["scores"]]
    if "targets" not in state:                      # the "before" values, taken once from the untouched copy
        state["targets"] = {t["key"]: t for t in targets(conn)}
    live = get_judge()
    for t in rows:
        t = state["targets"][t["key"]]
        if t["cid"] not in state["merged"]:
            state["merged"][t["cid"]] = store.remerge(conn, t["cid"])["merged_away"]
            OUT.write_text(json.dumps(state, indent=1, default=str))
        judge = Hybrid(ReplayJudge(conn, (t["cid"],)), live)
        pipe = Pipeline(Deps(str(WORK), judge, today=dt.date.fromisoformat(t["today"]), planner=None),
                        print_timings=False)
        res = pipe.score(t["cid"], t["baseline"], plan=False)
        if res.get("paused") or not res.get("run_id"):
            print(f"Stopped at a daily limit while scoring {t['key']}: {res.get('paused') or res}. Run again later.")
            break
        after = _final(conn, res["run_id"], t["weights"])
        state["scores"][t["key"]] = {"after": after, "after_run": res["run_id"], "asked": dict(judge.asked)}
        OUT.write_text(json.dumps(state, indent=1, default=str))
        print(f"{t['key']}: {t['before']:.2f} → {after:.2f} (asked live {sum(judge.asked.values())})", flush=True)
    conn.close()
    report()


# --- Report -------------------------------------------------------------------------------------------------------

def _moves(conn, before_run: str, after_run: str, weights) -> list[tuple[str, float, float, float]]:
    """(subcategory, before, after, points of the final) for every subcategory that moved."""
    from graph.pipeline import run_scores
    from backend.recruiter import rescore
    from parser_gates import final_of
    a, b = run_scores(conn, before_run), run_scores(conn, after_run)
    ea = (rescore(conn, before_run, weights) if weights else final_of(conn, before_run)[0]).effective_weights
    eb = (rescore(conn, after_run, weights) if weights else final_of(conn, after_run)[0]).effective_weights
    out = []
    for s in sorted(set(a) | set(b)):
        x, y = (a.get(s) or {}).get("score"), (b.get(s) or {}).get("score")
        if x != y:
            pts = 100 * ((y or 0) * eb.get(s, 0) - (x or 0) * ea.get(s, 0))
            out.append((s, x, y, pts))
    return sorted(out, key=lambda r: -abs(r[3]))


def report() -> None:
    from parser_gates import EXPECTED, design_checks, fmt
    state = json.loads(OUT.read_text())
    conn = db.connect(str(WORK))
    lines = ["# Phase C live run (specs/19 item 19)", "",
             "Before = the recorded score. After = re-merged (extraction.store.remerge) and scored again; saved answers "
             "reused for unchanged inputs, the rest asked live. Formulas, weights and logic_version unchanged.", "",
             "| score | before | after | Δ | asked live | merged |", "|---|---|---|---|---|---|"]
    done = {k: v for k, v in state["scores"].items()}
    for key, t in state["targets"].items():
        s = done.get(key)
        merged = state["merged"].get(t["cid"], {})
        m = "; ".join(f"{k}: {len(v)}" for k, v in merged.items()) or "—"
        if s is None:
            lines.append(f"| {key} | {fmt(t['before'], 2)} | not yet | | | {m} |")
            continue
        d = s["after"] - t["before"]
        lines.append(f"| {key} | {t['before']:.2f} | {s['after']:.2f} | {d:+.2f} | {sum(s['asked'].values())} | {m} |")
    lines += ["", "## Changes above 1 point", ""]
    for key, t in state["targets"].items():
        s = done.get(key)
        if not s or abs(s["after"] - t["before"]) <= 1:
            continue
        lines.append(f"**{key}** ({t['before']:.2f} → {s['after']:.2f})")
        merged = state["merged"].get(t["cid"], {})
        lines.append("- merged: " + ("; ".join(f"{k}: {', '.join(v)}" for k, v in merged.items()) or "nothing"))
        for sub, x, y, pts in _moves(conn, t["run"], s["after_run"], t["weights"]):
            if abs(pts) >= 0.05:
                lines.append(f"- {sub}: {fmt(x, 3)} → {fmt(y, 3)} ({pts:+.2f} pts): {CAUSE.get(sub, 're-asked')}")
        lines.append("")
    tests = {t["key"]: (t, done.get(t["key"])) for t in state["targets"].values() if t["kind"] == "test"}
    lines += ["## Gates", "", "**Ranking per role family** (expected " + EXPECTED + "):", ""]
    for role in ("DS", "AI", "PM"):
        for col in ("before", "after"):
            ids = [((t["before"] if col == "before" else s["after"]), pid) for pid, (t, s) in tests.items()
                   if pid.startswith(role) and "_" not in pid and (col == "before" or s)]
            order = [pid[2:] for _, pid in sorted(ids, reverse=True)]
            ok = len(order) == 5 and order[0] == "1" and set(order[1:3]) == {"3", "4"} and order[3:] == ["2", "5"]
            lines.append(f"- {role} {col}: {' > '.join(order)} → {'PASS' if ok else 'FAIL' if len(order) == 5 else 'not all scored'}")
    results = {pid: {"control": t["before"], "parser": s["after"], "run": s["after_run"], "cid": t["cid"]}
               for pid, (t, s) in tests.items() if s}
    lines += [""] + [ln for ln in design_checks(conn, results) if "No gain without new evidence" not in ln]
    lines += ["", "**Keyword-stuffer and padded profiles** (a gain must come only from merged duplicates):", ""]
    for pid in STUFFER_IDS:
        t, s = tests.get(pid, (None, None))
        if not s:
            continue
        gain = s["after"] - t["before"]
        merged = state["merged"].get(t["cid"], {})
        listed = "; ".join(f"{k}: {', '.join(v)}" for k, v in merged.items())
        ok = gain <= 1e-9 or bool(merged)
        why = "no gain" if gain <= 1e-9 else f"from merged duplicates: {listed}" if merged else "gained with nothing merged"
        lines.append(f"- {pid}: {gain:+.2f} ({why}) → {'PASS' if ok else 'FAIL'}")
    lines += ["", "**Project-only grads (ID 3), stop if a drop is above 5 points:**", ""]
    for pid in ("AI3", "DS3", "PM3"):
        t, s = tests.get(pid, (None, None))
        if s:
            d = s["after"] - t["before"]
            lines.append(f"- {pid}: {d:+.2f} → {'STOP for decision' if d < -5 else 'ok'}")
    asked = Counter()
    for s in done.values():
        asked.update(s["asked"])
    lines += ["", f"Asked live in all: {sum(asked.values())} {dict(sorted(asked.items()))}"]
    OUT.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estimate", action="store_true")
    g.add_argument("--run", action="store_true")
    g.add_argument("--report", action="store_true")
    a = ap.parse_args()
    estimate() if a.estimate else run() if a.run else report()
