"""Rebuild a role's Market version from cached extractions after name merges were approved (no model calls),
publish it when it differs materially, and print Manual vs Market.

    .venv/bin/python scripts/rebuild_market_version.py --role ai_engineer [--db data/demo.db] [--no-publish]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import config, db  # noqa: E402
from market import baseline as mb  # noqa: E402
from market import normalize, sources  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--role", default="ai_engineer")
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--no-publish", action="store_true")
    args = ap.parse_args()
    conn = db.connect(args.db)
    role = sources.load()[args.role]
    print(f"Pending name merges: {len(normalize.suggestions(conn))}")
    v = mb.recompute(conn, role, test=True)                 # cached / hand-labelled extractions only
    print(f"Draft v{v['version']}: {v['postings']} postings, {v['companies']} companies, model calls {v['model_calls']}")
    print("Changes:", v["content"]["material"] or "none")
    if v["content"]["material"] and not args.no_publish:
        try:
            v = mb.publish(conn, v["id"])
            print(f"Published v{v['version']} ({'test version' if v['test_version'] else 'full'})")
        except ValueError as exc:
            print("Not published:", exc)
    s = mb.side_by_side(conn, role, v["id"])
    print(f"\nManual vs Market (v{v['version']}) for {role.title}")
    print(f"  {'minimum years':42} manual={s['manual']['required_years']}  market={(s['market'] or {}).get('required_years')}")
    for r in s["rows"]:
        print(f"  {r['name']:42} manual={r['manual'] or '—':9} market={r['market'] or '—':9} share={r['share'] if r['share'] is not None else '—'}")


if __name__ == "__main__":
    main()
