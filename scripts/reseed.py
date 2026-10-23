"""A clean demo database for the prototype server (deploy): synthetic profiles and demo jobs only.

    python scripts/reseed.py [--db data/demo.db] [--archive data/judgment-archive.db] [--force]

1. Renders the synthetic resume PDFs if they are missing (code only; the Resume tab shows them).
2. Seeds the recruiter demo (jobs, the 15 test profiles as applicants, the padded variant, the archetypes scored with
   their scripted labels) and the candidate-side demo jobs.
3. Scores each test profile against its role with the judgment archive replayed: an answer recorded for exactly the
   same input is reused. Nothing is ever asked live here; a question with no recording is listed, and that profile's
   affected parts show as not scored until it is scored live in the app.
An existing database is kept unless --force (then it is moved aside to <db>.before-reseed).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("RIGHTSTAFF_DB", str(ROOT / "data" / "demo.db")))
    ap.add_argument("--archive", default=str(ROOT / "data" / "judgment-archive.db"))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    db_path = Path(a.db)
    if db_path.exists():
        if not a.force:
            sys.exit(f"{db_path} exists; --force moves it aside and starts over.")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                shutil.move(p, str(db_path) + ".before-reseed" + suffix)
    graphs = db_path.with_name(db_path.stem + ".graphs.db")
    for suffix in ("", "-wal", "-shm"):
        Path(str(graphs) + suffix).unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not Path(a.archive).exists():
        sys.exit(f"No judgment archive at {a.archive} (copy it from your machine: docs/deploy.md).")
    from judgment_archive import check
    if check(a.archive):
        sys.exit("The archive holds a profile that is not synthetic; refusing to use it.")

    rendered = ROOT / "tests" / "fixtures" / "profiles" / "rendered"
    if not any(rendered.glob("*_resume.pdf")):
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "render_test_profiles.py")], check=True)

    import seed_recruiter_demo
    print(seed_recruiter_demo.seed(str(db_path), reset=True))
    from backend import demo_jobs
    from core import config, db
    conn = db.init_app_db(str(db_path))
    print(demo_jobs.seed(conn))

    import sqlite3
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.replay_judge import ReplayJudge
    from score_test_profiles import targets
    archive = sqlite3.connect(f"file:{a.archive}?mode=ro", uri=True)
    missed_total = 0
    for t in targets(conn, rescore=True):
        ds, name = conn.execute("SELECT data_source, name FROM candidates WHERE id = ?", (t["candidate_id"],)).fetchone()
        judge = ReplayJudge(archive, (f"{ds}:{name}",))
        res = Pipeline(Deps(str(db_path), judge, planner=None), print_timings=False).score(
            t["candidate_id"], t["baseline_id"], plan=False)
        if t["id"]:
            with conn:
                conn.execute("UPDATE applications SET run_id = ? WHERE id = ?", (res.get("run_id"), t["id"]))
        missed = len(judge.missed_inputs)
        missed_total += missed
        print(f"{name} ({ds}): final {res.get('final')}" + (f", {missed} unrecorded question(s)" if missed else ""),
              flush=True)
    archive.close()
    conn.close()
    print(f"\nDone: {db_path}. Unrecorded questions: {missed_total} (never asked here; LIVE={int(config.LIVE)}).")


if __name__ == "__main__":
    main()
