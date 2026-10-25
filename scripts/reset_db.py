"""An empty database at the current schema with the reference data from the seed files only.

    python scripts/reset_db.py [--db data/demo.db] [--force]

Reference data: the role baselines in seed/baselines.json. No candidates, jobs, runs or judgments (`make reseed`
adds the synthetic profiles and demo jobs instead). No model calls. An existing database is kept unless --force; then
it is moved aside to <db>.before-reset, and its LangGraph checkpoints (<db stem>.graphs.db) are removed.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PRINT_TIMINGS", "0")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("RIGHTSTAFF_DB", str(ROOT / "data" / "demo.db")))
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    db_path = Path(a.db)
    if db_path.exists():
        if not a.force:
            sys.exit(f"{db_path} exists; --force moves it aside and starts over.")
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(db_path) + suffix)
            if p.exists():
                shutil.move(p, str(db_path) + ".before-reset" + suffix)
    graphs = db_path.with_name(db_path.stem + ".graphs.db")
    for suffix in ("", "-wal", "-shm"):
        Path(str(graphs) + suffix).unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from core import db
    conn = db.init_app_db(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    baselines = conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]
    conn.close()
    print(f"{db_path}: schema {version}, {baselines} seed baselines, nothing else.")


if __name__ == "__main__":
    main()
