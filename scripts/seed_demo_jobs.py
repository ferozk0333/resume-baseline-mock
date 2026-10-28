"""Seed the fictional demo jobs for the candidate Home (specs/17 §9). No model calls. Idempotent.

    .venv/bin/python scripts/seed_demo_jobs.py [--db data/demo.db]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import demo_jobs  # noqa: E402
from core import db  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/demo.db")
    args = ap.parse_args()
    conn = db.init_app_db(args.db)
    print(demo_jobs.seed(conn))


if __name__ == "__main__":
    main()
