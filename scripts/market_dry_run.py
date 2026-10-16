"""Market baseline dry run (specs/15 §10): postings per source, new postings, model calls and days at the
configured daily limit. Never calls a model. `--fetch` first reads the configured public job boards (network,
no model); placeholders are skipped.

    .venv/bin/python scripts/market_dry_run.py [--db data/demo.db] [--fetch] [--rpd 20]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config, db  # noqa: E402
from market import pipeline, sources  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--fetch", action="store_true", help="read the public job boards first (no model calls)")
    ap.add_argument("--rpd", type=int, default=None, help="requests per day for MARKET_EXTRACT_MODEL")
    args = ap.parse_args()
    conn = db.init_app_db(args.db)
    roles = sources.load()
    if args.fetch:
        for r in roles.values():
            print(f"fetch {r.key}:", json.dumps(pipeline.fetch_role(conn, r)))
    out = pipeline.dry_run(conn, roles, args.rpd)
    print(f"Model: {out['model']} · limit {out['requests_per_day'] or 'n/a'} requests/day")
    for key, r in out["roles"].items():
        srcs = ", ".join(f"{s}: {v['companies']} companies ({v['placeholders']} placeholders), {v.get('postings', 0)} postings"
                         for s, v in r["sources"].items())
        print(f"\n{key}: {r['postings_in_window']} postings in the window from {r['companies_with_postings']} companies; "
              f"{r['new_postings']} new\n  sources: {srcs}\n  model calls: {r['model_calls']}")
    print(f"\nName-merge proposals: {out['merge_batches']} batch(es) of up to {config.MARKET_MERGE_BATCH} names")
    print(f"Total model calls: {out['total_model_calls']} · days at the limit: {out['days_at_limit']}")


if __name__ == "__main__":
    main()
