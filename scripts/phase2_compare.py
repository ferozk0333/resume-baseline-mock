"""specs/18 Phase 2 comparison: each flag on vs off on the 16 test profiles (and the latest live profile for REPO_CAP).

    python scripts/phase2_compare.py --estimate                 # offline: calls per flag, before any run
    LIVE=1 python scripts/phase2_compare.py --run FLAG          # live, only after the go (states its calls first)

--run scores every test profile with the flag off (replayed: today's answers) and on (saved answers for an unchanged
input are reused; only what the flag changes is asked live), then reports per profile: score change, label agreement
per question family, average seconds per call per model, the ranking 1 > {3,4} > 2 > 5 and the DESIGN checks.
Writes timings/phase2_<flag>.md.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

from core import config, db  # noqa: E402

FLAGS = ("GROUPED_ITEM_JUDGING", "REPO_CAP", "GEMMA_JUDGE", "GROQ_JUDGE")
WORK = ROOT / "data" / "phase2.db"


def profiles(conn) -> list[dict]:
    from parser_gates import final_of
    from report_test_profiles import profile_id
    from score_test_profiles import targets
    out = []
    for a in targets(conn):
        if a["run_id"]:
            run = conn.execute("SELECT started_at, baseline_id FROM runs WHERE id = ?", (a["run_id"],)).fetchone()
            out.append({"pid": profile_id(conn, a["candidate_id"]), "cid": a["candidate_id"], "run": a["run_id"],
                        "baseline": run[1], "today": dt.date.fromisoformat(run[0][:10]),
                        "recorded": final_of(conn, a["run_id"])[0].final})
    return out


def _asks(conn, p: dict):
    """Every ask scoring would make for this profile and role (saved relevance labels; no call)."""
    from graph.context import Deps, open_ctx
    from graph.pipeline import DIMENSIONS
    from judges.replay_judge import ReplayJudge
    deps = Deps(str(WORK), ReplayJudge(conn), today=p["today"], planner=None)
    with open_ctx(deps, {"candidate_id": p["cid"], "baseline_id": p["baseline"]}) as ctx:
        return [a for subs in DIMENSIONS.values() for s in subs for a in s.plan(ctx)], ctx


def estimate() -> None:
    from parser_gates import db_copy
    db_copy(ROOT / "data" / "demo.db", WORK)
    conn = db.connect(str(WORK))
    rows = profiles(conn)
    print(f"{len(rows)} test profiles (each against its own role).\n")
    grouped, routine = Counter(), Counter()
    for p in rows:
        asks, _ = _asks(conn, p)
        live = [a for a in asks if a.rule_label is None]
        c = sum(1 for a in live if a.key == "complexity")
        w = sum(1 for a in live if a.key == "work_sample_relevant")
        grouped["complexity_single"] += c
        grouped["work_sample_jev"] += w
        grouped["batches_on"] += (1 if c >= 2 else c)
        routine["route_single"] += sum(1 for a in live if a.route == "claude" or a.key == "complexity")
    print("GROUPED_ITEM_JUDGING")
    print(f"  off: {grouped['complexity_single']} complexity calls (route judge_single, one per item) and "
          f"{grouped['work_sample_jev']} work-sample questions to Jev (grouped per item)")
    print(f"  on:  {grouped['batches_on']} batched complexity calls, plus one retry per item whose quote fails; "
          "work samples unchanged (Jev)")
    print(f"  a live comparison asks every complexity item once more: {grouped['batches_on']} batched calls\n")
    from parser_gates import latest_live_candidate
    live_cid = latest_live_candidate(conn)
    repos = conn.execute("SELECT COUNT(*) FROM projects WHERE candidate_id = ? AND own_commits IS NOT NULL AND "
                         "NOT (is_fork = 1 AND own_commits = 0)", (live_cid,)).fetchone()[0] if live_cid else 0
    print("REPO_CAP (MAX_REPOS 8)")
    print(f"  test profiles: at most 4 repositories each, so no change (0 calls)")
    print(f"  latest live profile: {repos} counted repositories → {max(0, repos - 8)} fewer README reads "
          f"(LLM, route extract_web) and their judgments skipped; the live comparison re-scores both roles, asking "
          "only items whose text changed")
    print("\nGEMMA_JUDGE / GROQ_JUDGE")
    print(f"  same number of calls; {routine['route_single']} route judge_single calls over the 16 profiles would take "
          "turns across Flash-Lite and the allowed family (Groq: single items only). A live comparison re-asks those "
          "calls on the new models; Jev answers are reused.")


def estimate_combo(flags: list[str], pids: list[str] | None = None) -> dict:
    """Replay with the flags on: every ask with no recording is a live call. Batched route asks are grouped as the
    pipeline groups them (per question and source with PARTITIONED_BATCHES)."""
    from extraction.structured import load_profile_file
    from judges.replay_judge import ReplayJudge
    from parser_gates import db_copy, score
    db_copy(ROOT / "data" / "demo.db", WORK)
    conn = db.connect(str(WORK))
    for f in flags:
        setattr(config, f, True)
    per, total = {}, Counter()
    try:
        for p in profiles(conn):
            if pids and p["pid"] not in pids:
                continue
            path = next((ROOT / "rightstaff-test-profiles" / "profiles").glob(f"*/{p['pid']}.md"))
            cid = load_profile_file(conn, path)
            res, judge = score(str(WORK), cid, p["baseline"], p["today"], (p["cid"],))
            route = [(k, sid) for k, sid, r in judge.missed_subjects if r]
            batches = {(k, sid.split("#all@", 1)[1] if "#all@" in sid else "") for k, sid in route}
            jev = sum(1 for _, _, r in judge.missed_subjects if not r)
            per[p["pid"]] = {"route_asks": len(route), "batch_calls": len(batches), "jev": jev}
            total.update(per[p["pid"]])
    finally:
        for f in flags:
            setattr(config, f, False)
    return {"per_profile": per, "total": dict(total)}


class _Fresh:
    """A replay that never answers these questions (so the flag's way of asking them is what gets measured)."""

    def __init__(self, replay, keys: set[str]):
        self.replay, self.keys = replay, keys

    def __getattr__(self, name):
        return getattr(self.replay, name)

    def _get(self, key, context, route=False):
        from judges.base import JudgeError
        if key in self.keys:
            raise JudgeError("asked fresh under the flag")
        return self.replay._get(key, context, route)


def _criteria(scores: dict) -> dict:
    """(question, criterion name) → (best label, quote that earned it) from the skills/tools subcategories."""
    from graph.nodes.expertise import best_label
    out = {}
    for sub in ("role_skills", "applied_knowledge", "tools", "complexity"):
        for j in (scores.get(sub, {}).get("details") or {}).get("judgments", []):
            name = (j.get("subject") or "").split(":")[0][:60]
            k = (j["question"], name)
            have = out.get(k)
            if have is None or (j["label"] and best_label(j["question"], have[0], j["label"]) == j["label"]
                                and j["label"] != have[0]):
                out[k] = (j["label"], j.get("quote"))
    return out


def run_combo(flags: list[str], fresh: set[str] | None = None, pids: list[str] | None = None, tag: str = "") -> dict:
    """Flags off (today's answers, replayed) vs on (live for what the flags change), per test profile."""
    import json
    import time
    if not config.LIVE:
        sys.exit("Live comparison: set LIVE=1, and only after the go.")
    from extraction.structured import load_profile_file
    from graph.context import Deps
    from graph.pipeline import Pipeline, run_scores
    from judges.replay_judge import ReplayJudge
    from judges.service import get_judge
    from parser_gates import db_copy, design_checks, final_of, ranking
    from readme_live_gate import Hybrid
    work = ROOT / "data" / f"phase2_{tag or '_'.join(flags).lower()}.db"
    if not work.exists():
        db_copy(ROOT / "data" / "demo.db", work)
    conn = db.connect(str(work))
    live = get_judge()
    out_path = ROOT / "timings" / f"phase2_{tag or '_'.join(flags).lower()}.json"
    done = json.loads(out_path.read_text()) if out_path.exists() else {}
    for p in profiles(conn):
        if (pids and p["pid"] not in pids) or p["pid"] in done:
            continue
        path = next((ROOT / "rightstaff-test-profiles" / "profiles").glob(f"*/{p['pid']}.md"))
        off_cid = load_profile_file(conn, path)
        off = Pipeline(Deps(str(work), ReplayJudge(conn, (p["cid"],)), today=p["today"], planner=None),
                       print_timings=False).score(off_cid, p["baseline"], plan=False)
        for f in flags:
            setattr(config, f, True)
        try:
            on_cid = load_profile_file(conn, path)
            replay = ReplayJudge(conn, (p["cid"],))
            judge = Hybrid(_Fresh(replay, fresh or set()), live)
            pipe = Pipeline(Deps(str(work), judge, today=p["today"], planner=None), print_timings=False)
            t0 = time.time()
            on = pipe.score(on_cid, p["baseline"], plan=False)
            secs = time.time() - t0
            summary = next(iter(pipe.recorders.values())).summary() if pipe.recorders else {}
        finally:
            for f in flags:
                setattr(config, f, False)
        entry = {"recorded": p["recorded"], "off": off.get("final"), "on": on.get("final"), "seconds": round(secs, 1),
                 "asked": sum(judge.asked.values()), "calls_by_task": summary.get("calls_by_task"),
                 "model_calls": summary.get("model_calls"), "failovers": summary.get("failovers"),
                 "rate_limits": summary.get("rate_limits"), "paused": on.get("paused"),
                 "off_run": off.get("run_id"), "on_run": on.get("run_id"), "off_cid": off_cid, "on_cid": on_cid}
        if off.get("run_id") and on.get("run_id"):
            c0, c1 = _criteria(run_scores(conn, off["run_id"])), _criteria(run_scores(conn, on["run_id"]))
            entry["label_changes"] = [{"question": k[0], "criterion": k[1], "off": c0[k][0], "on": c1[k][0],
                                       "off_quote": c0[k][1], "on_quote": c1[k][1]}
                                      for k in sorted(c0.keys() & c1.keys()) if c0[k][0] != c1[k][0]]
        done[p["pid"]] = entry
        out_path.write_text(json.dumps(done, indent=1, default=str))
        print(f"{p['pid']}: off {entry['off']} on {entry['on']} asked {entry['asked']} {secs:.0f}s", flush=True)
        if on.get("paused"):
            print(f"Stopped at the daily limit: {on['paused']}")
            break
    results = {pid: {"control": e["off"], "parser": e["on"], "run": e["on_run"], "cid": e["on_cid"],
                     "control_cid": e.get("off_cid")} for pid, e in done.items() if e.get("on_run")}
    report = ranking(results) + [""] + design_checks(conn, results)
    (out_path.with_suffix(".md")).write_text("\n".join(report) + "\n")
    print("\n".join(report))
    return done


def run(flag: str) -> None:
    if not config.LIVE:
        sys.exit("Live comparison: set LIVE=1, and only after the go.")
    import time
    from graph.context import Deps
    from graph.pipeline import Pipeline, run_scores
    from judges.replay_judge import ReplayJudge
    from judges.service import get_judge
    from parser_gates import db_copy, design_checks, ranking
    from readme_live_gate import Hybrid
    from extraction.structured import load_profile_file
    db_copy(ROOT / "data" / "demo.db", WORK)
    conn = db.connect(str(WORK))
    rows = profiles(conn)
    live = get_judge()
    results, agree, seconds = {}, defaultdict(Counter), defaultdict(list)
    for p in rows:
        path = next((ROOT / "rightstaff-test-profiles" / "profiles").glob(f"*/{p['pid']}.md"))
        out = {}
        for on in (False, True):
            setattr(config, flag, on)
            cid = load_profile_file(conn, path)
            judge = Hybrid(ReplayJudge(conn, (p["cid"],)), live)
            pipe = Pipeline(Deps(str(WORK), judge, today=p["today"], planner=None), print_timings=False)
            t0 = time.time()
            res = pipe.score(cid, p["baseline"], plan=False)
            summary = next(iter(pipe.recorders.values())).summary() if pipe.recorders else {}
            for m, tk in (summary.get("tokens_by_model") or {}).items():
                if tk.get("calls"):
                    seconds[m].append((time.time() - t0) / tk["calls"])
            out[on] = {"run": res.get("run_id"), "cid": cid, "final": res.get("final")}
        setattr(config, flag, False)
        s0, s1 = run_scores(conn, out[False]["run"]), run_scores(conn, out[True]["run"])
        for sub in s0:
            j0 = {(j["question"], j["subject"]): j["label"] for j in s0[sub]["details"].get("judgments", [])}
            j1 = {(j["question"], j["subject"]): j["label"] for j in s1[sub]["details"].get("judgments", [])}
            for k in j0.keys() & j1.keys():
                agree[k[0]]["same" if j0[k] == j1[k] else "different"] += 1
        results[p["pid"]] = {"control": out[False]["final"], "parser": out[True]["final"], "run": out[True]["run"],
                             "cid": out[True]["cid"], "control_cid": out[False]["cid"]}
    lines = [f"# {flag}: on vs off on the test profiles", "", "| profile | off | on | Δ |", "|---|---|---|---|"]
    lines += [f"| {pid} | {r['control']:.1f} | {r['parser']:.1f} | {r['parser'] - r['control']:+.2f} |"
              for pid, r in results.items() if r["control"] is not None and r["parser"] is not None]
    lines += ["", "Label agreement per question family: " + "; ".join(
        f"{q} {c['same']}/{c['same'] + c['different']}" for q, c in sorted(agree.items()))]
    lines += ["", "Average seconds per call per model: " + "; ".join(
        f"{m} {sum(v) / len(v):.1f}" for m, v in sorted(seconds.items()))]
    lines += ["", *ranking(results), "", *design_checks(conn, results)]
    path = ROOT / "timings" / f"phase2_{flag.lower()}.md"
    path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estimate", action="store_true")
    g.add_argument("--run", choices=FLAGS)
    g.add_argument("--estimate-combo", help="flags joined by +, e.g. PARTITIONED_BATCHES+REPO_CAP")
    g.add_argument("--run-combo", help="flags joined by +; resumable (finished profiles are kept)")
    ap.add_argument("--profiles", help="comma-separated profile ids, e.g. DS1,DS2")
    ap.add_argument("--fresh", default="", help="questions never replayed on the 'on' side (e.g. complexity)")
    args = ap.parse_args()
    if args.run_combo:
        run_combo(args.run_combo.split("+"), set(filter(None, args.fresh.split(","))),
                  args.profiles.split(",") if args.profiles else None)
    elif args.estimate_combo:
        import json
        print(json.dumps(estimate_combo(args.estimate_combo.split("+")), indent=1))
    else:
        estimate() if args.estimate else run(args.run)


if __name__ == "__main__":
    main()
