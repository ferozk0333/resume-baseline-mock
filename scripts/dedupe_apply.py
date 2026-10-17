"""Phase C step 4 (spec 19 review): apply the dedupe to data/demo.db. No model calls.

    python scripts/dedupe_apply.py            # after scripts/dedupe_live.py --run finished and its gates passed

1. Copies the judgments step 3 recorded (data/dedupe-live.db) into demo.db.
2. Re-merges every saved profile (extraction.store.remerge), so duplicates the old merge kept become one.
3. Re-scores the scores step 3 covered, replay only (a question with no saved answer is never asked: that score is
   left as it was and reported), and points their applications at the new runs.
Other candidates that merged something keep their current runs (re-scoring them would need new judgments).
Back up demo.db first (done: data/demo.db.bak-spec19-*). Writes timings/dedupe_apply.json.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

from core import db  # noqa: E402
from extraction import store  # noqa: E402

DEMO = ROOT / "data" / "demo.db"
LIVE = ROOT / "data" / "dedupe-live.db"


def copy_judgments() -> int:
    a = sqlite3.connect(str(DEMO))
    a.execute("ATTACH DATABASE ? AS live", (str(LIVE),))
    cols = [r[1] for r in a.execute("PRAGMA main.table_info(judgments)")]
    names = ", ".join(cols)
    before = a.execute("SELECT COUNT(*) FROM main.judgments").fetchone()[0]
    with a:
        a.execute(f"INSERT OR IGNORE INTO main.judgments ({names}) SELECT {names} FROM live.judgments")
    n = a.execute("SELECT COUNT(*) FROM main.judgments").fetchone()[0] - before
    a.execute("DETACH DATABASE live")
    a.close()
    return n


def main() -> None:
    from dedupe_live import _final, targets
    from parser_gates import score
    assert any(p.name.startswith("demo.db.bak-spec19-") for p in (ROOT / "data").iterdir()), "back up demo.db first"
    live_state = json.loads((ROOT / "timings" / "dedupe_live.json").read_text())
    copied = copy_judgments()
    conn = db.connect(str(DEMO))
    todo = targets(conn)                                   # before the merge: the runs and applications to redo
    merged = {}
    for (cid, name) in conn.execute("SELECT id, name FROM candidates").fetchall():
        m = store.remerge(conn, cid)["merged_away"]
        if m:
            merged[name or cid] = m
    conn.close()
    out = {"judgments_copied": copied, "merged": merged, "rescored": {}, "skipped": {}}
    for t in todo:
        res, judge = score(str(DEMO), t["cid"], t["baseline"], dt.date.fromisoformat(t["today"]), (t["cid"],))
        if judge.missed_inputs or not res.get("run_id"):
            out["skipped"][t["key"]] = f"{len(judge.missed_inputs)} unrecorded question(s); kept run {t['run']}"
            continue
        conn = db.connect(str(DEMO))
        after = _final(conn, res["run_id"], t["weights"])
        if t.get("app"):
            with conn:
                conn.execute("UPDATE applications SET run_id = ? WHERE id = ?", (res["run_id"], t["app"]))
        conn.close()
        step3 = (live_state["scores"].get(t["key"]) or {}).get("after")
        out["rescored"][t["key"]] = {"before": t["before"], "after": after, "step3": step3, "run": res["run_id"]}
        print(f"{t['key']}: {t['before']:.2f} → {after:.2f} (step 3: {step3})", flush=True)
    (ROOT / "timings" / "dedupe_apply.json").write_text(json.dumps(out, indent=1, default=str))
    print(f"\njudgments copied {copied}; candidates merged {len(merged)}; re-scored {len(out['rescored'])}; "
          f"skipped {len(out['skipped'])}")
    for k, v in out["skipped"].items():
        print(f"  skipped {k}: {v}")
    for k, v in merged.items():
        print(f"  merged {k}: " + "; ".join(f"{t}: {len(n)}" for t, n in v.items()))


if __name__ == "__main__":
    main()
