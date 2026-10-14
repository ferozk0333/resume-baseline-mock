"""Seed the recruiter demo (specs/11 §2): three jobs and their applicants. No model calls.

    python scripts/seed_recruiter_demo.py [--db rightstaff.db] [--reset]

- One job per seed baseline (fictional companies). Must-haves default to the baseline's first four required
  skills and tools.
- The 16 structured test profiles are parsed (no model, exact quotes) and apply to their role's job. They
  are not scored until real judging runs (later, with approval).
- The 7 archetypes (6 + the empty profile) apply to the Data Scientist job and are scored with their scripted FakeJudge labels.
Both kinds are labelled as demo data in the UI.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

JOBS = {"baseline-data-scientist-entry": ("Data Scientist", "Northwind Analytics"),
        "baseline-ai-engineer-entry": ("AI Engineer", "Quarry Labs"),
        "baseline-product-manager-entry": ("Associate Product Manager", "Fernway Health")}


def seed(db_path: str, reset: bool = False) -> dict:
    sys.path.insert(0, str(ROOT))
    from backend import recruiter as rc
    from core import db
    from extraction.structured import DEMO_SOURCE, load_all, read_profile_file
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from tests.fixtures.archetypes import ARCHETYPES, TODAY, empty_profile

    conn = db.init_app_db(db_path)
    if reset:
        demo = [r[0] for r in conn.execute("SELECT id FROM candidates WHERE data_source IN (?, 'archetype')", (DEMO_SOURCE,))]
        with conn:
            conn.execute("DELETE FROM applications")
            conn.execute("DELETE FROM jobs")
            for cid in demo:
                for table in ("roles", "projects", "bullets", "claims", "certifications", "references_", "evidence_items",
                              "dropped_items", "judgments", "scores", "runs"):
                    conn.execute(f"DELETE FROM {table} WHERE candidate_id = ?", (cid,))
                conn.execute("DELETE FROM candidates WHERE id = ?", (cid,))
    elif conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]:
        sys.exit("Jobs already exist; use --reset to rebuild the demo.")

    jobs = {}
    for baseline_id, (title, company) in JOBS.items():
        baseline = db.get_baseline(conn, baseline_id)
        must = [c.id for c in baseline.criteria if c.priority == "required" and c.kind in ("skill", "tool")][:4]
        jobs[baseline_id] = rc.create_job(conn, baseline_id, title, company, must)

    by_role = {"Data Scientist": "baseline-data-scientist-entry", "AI Engineer": "baseline-ai-engineer-entry",
               "Product Manager": "baseline-product-manager-entry"}
    profiles_dir = ROOT / "rightstaff-test-profiles" / "profiles"
    ids = load_all(conn, profiles_dir, ROOT / "tests" / "fixtures" / "profiles" / "rendered")
    for pid, cid in ids.items():
        role = read_profile_file(next(profiles_dir.glob(f"*/{pid}.md"))).role
        rc.add_application(conn, jobs[by_role[role]], cid)

    ds = "baseline-data-scientist-entry"
    archetypes = {**ARCHETYPES, "empty": empty_profile}
    for name, build in archetypes.items():
        cid, judge = build(conn)
        with conn:
            conn.execute("UPDATE candidates SET data_source = 'archetype' WHERE id = ?", (cid,))
        res = Pipeline(Deps(db_path, judge, today=TODAY), print_timings=False).score(cid, ds)
        rc.add_application(conn, jobs[ds], cid, res["run_id"])
    conn.close()
    return {"jobs": len(jobs), "parsed_profiles": len(ids), "archetypes": len(archetypes)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("RIGHTSTAFF_DB", str(ROOT / "rightstaff.db")))
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    print(seed(args.db, args.reset))


if __name__ == "__main__":
    main()
