"""Score the parsed test profiles with real judging, each against its own role's job only. Live: model calls.

    python scripts/score_test_profiles.py [--db data/demo.db] [--dry-run]

Jev is the main judge; the Claude roles go to Gemini (LLM_PROVIDER=gemini, models per role from config).
No improvement plans for these runs. Each run is attached to the applicant in the recruiter view.

Resumable: an applicant whose run finished without errors is skipped (--rescore scores all again). When a Gemini model's daily limit is
reached the script stops with the reset time; judgments already saved are reused on the next run.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("RIGHTSTAFF_DEMO_DB", str(ROOT / "data" / "demo.db")))
    ap.add_argument("--dry-run", action="store_true", help="list what would be scored; no model calls")
    ap.add_argument("--rescore", action="store_true",
                    help="score every profile again (saved judgments with the same input are reused)")
    args = ap.parse_args()
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("LLM_PROVIDER", "gemini")
    sys.path.insert(0, str(ROOT))

    from core import config, db
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.service import get_judge

    conn = db.init_app_db(args.db)
    todo = []
    for a in targets(conn, args.rescore):
        done = a["run_id"] and conn.execute("SELECT status FROM runs WHERE id = ?", (a["run_id"],)).fetchone()
        errors = a["run_id"] and conn.execute("SELECT COUNT(*) FROM scores WHERE run_id = ? AND status = 'error'",
                                              (a["run_id"],)).fetchone()[0]
        if not done or errors or args.rescore:
            todo.append(a)
    print(f"{len(todo)} applicant(s) to score with judge={config.JUDGE}, fallback={config.JUDGE_FALLBACK_MODEL}, "
          f"complexity={config.COMPLEXITY_MODEL}; no plans")
    if args.dry_run or not todo:
        return
    pipe = Pipeline(Deps(args.db, get_judge(), planner=None), print_timings=False)
    began = time.time()
    try:
        _score_all(conn, pipe, todo)
    finally:
        from collections import Counter
        calls, limits = Counter(), Counter()
        for rec in pipe.recorders.values():
            s = rec.summary()
            calls.update(s["model_calls"])
            limits.update(s.get("rate_limits") or {})
            for m, tk in s["tokens_by_model"].items():
                calls[f"  {m}"] += tk["calls"]
        print(f"\nTotal {time.time() - began:.0f} s; model HTTP calls: {dict(calls)}; rate limits: {dict(limits) or 'none'}")


ROLE_BASELINES = {"Data Scientist": "baseline-data-scientist-entry", "AI Engineer": "baseline-ai-engineer-entry",
                  "Product Manager": "baseline-product-manager-entry"}


def targets(conn, rescore: bool = False) -> list[dict]:
    """Applicants from the test profiles, plus test variants (no application; scored for robustness checks)."""
    out = [dict(a) for a in conn.execute(
        "SELECT a.id, a.candidate_id, a.run_id, j.baseline_id FROM applications a JOIN jobs j ON j.id = a.job_id "
        "JOIN candidates c ON c.id = a.candidate_id WHERE c.data_source = 'demo_parsed'")]
    for c in conn.execute("SELECT id, target_role FROM candidates WHERE data_source = 'test_variant'"):
        baseline = ROLE_BASELINES[c["target_role"]]
        run = conn.execute("SELECT id FROM runs WHERE candidate_id = ? AND baseline_id = ? AND status = 'done' "
                           "ORDER BY started_at DESC LIMIT 1", (c["id"], baseline)).fetchone()
        out.append({"id": None, "candidate_id": c["id"], "run_id": run[0] if run else None, "baseline_id": baseline})
    return out


def _score_all(conn, pipe, todo) -> None:
    for i, a in enumerate(todo, 1):
        start = time.time()
        res = pipe.score(a["candidate_id"], a["baseline_id"])
        if a["id"]:
            with conn:
                conn.execute("UPDATE applications SET run_id = ? WHERE id = ?", (res.get("run_id"), a["id"]))
        name = conn.execute("SELECT name FROM candidates WHERE id = ?", (a["candidate_id"],)).fetchone()[0]
        print(f"[{i}/{len(todo)}] {name}: final {res.get('final')}, errors {len(res.get('errors') or [])}, "
              f"{time.time() - start:.0f} s", flush=True)
        if res.get("paused"):
            print(f"\nStopped: {res['paused']}")
            return


if __name__ == "__main__":
    main()
