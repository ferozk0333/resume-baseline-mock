"""The judgment archive (deploy): saved judgments of the synthetic demo profiles only, so a fresh server can score
them without model calls (scripts/reseed.py replays them).

    python scripts/judgment_archive.py export [--db data/demo.db] [--out data/judgment-archive.db]
    python scripts/judgment_archive.py check [--archive data/judgment-archive.db]

Only rows of candidates whose data_source is demo_parsed, test_variant or archetype are exported (the 15 test
profiles, the padded variant and the archetypes); live candidates (real people) are never in it. Each row keeps its
answer and the input hash it was given for; candidate_id is replaced by "<data_source>:<name>" so a reseed can prefer
the same profile's own answers. `check` lists the profiles and refuses an archive that holds anything else.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SYNTHETIC = ("demo_parsed", "test_variant", "archetype")
COLS = ("id", "candidate_id", "baseline_id", "baseline_hash", "subject_type", "subject_id", "question", "label",
        "probabilities", "quote", "reasoning", "method", "model_version", "used", "confidence", "score", "input_hash",
        "created_at")


def export(db_path: str, out: str) -> None:
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    keys = {cid: f"{ds}:{name}" for cid, ds, name in src.execute(
        f"SELECT id, data_source, name FROM candidates WHERE data_source IN ({','.join('?' * len(SYNTHETIC))})",
        SYNTHETIC)}
    rows = src.execute(f"SELECT {', '.join(COLS)} FROM judgments WHERE used = 1 AND input_hash IS NOT NULL AND "
                       f"candidate_id IN ({','.join('?' * len(keys))})", list(keys)).fetchall()
    src.close()
    Path(out).unlink(missing_ok=True)
    dst = sqlite3.connect(out)
    dst.execute(f"CREATE TABLE judgments ({', '.join(COLS)}, run_id TEXT)")
    dst.execute("CREATE INDEX idx_archive_q ON judgments(question, input_hash)")
    i = COLS.index("candidate_id")
    with dst:
        dst.executemany(f"INSERT INTO judgments ({', '.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                        [(*r[:i], keys[r[i]], *r[i + 1:]) for r in rows])
    dst.execute("PRAGMA user_version = 1")
    dst.execute("VACUUM")
    dst.close()
    print(f"{len(rows)} judgments of {len({keys[r[i]] for r in rows})} synthetic profiles → {out}")


def check(archive: str) -> int:
    a = sqlite3.connect(f"file:{archive}?mode=ro", uri=True)
    profiles = [r[0] for r in a.execute("SELECT DISTINCT candidate_id FROM judgments ORDER BY 1")]
    a.close()
    bad = [p for p in profiles if p.split(":", 1)[0] not in SYNTHETIC]
    print(f"{len(profiles)} profiles: " + ", ".join(p.split(":", 1)[1] for p in profiles))
    if bad:
        print(f"NOT synthetic: {bad}")
        return 1
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("export", "check"))
    ap.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    ap.add_argument("--out", default=str(ROOT / "data" / "judgment-archive.db"))
    ap.add_argument("--archive", default=str(ROOT / "data" / "judgment-archive.db"))
    a = ap.parse_args()
    sys.exit(export(a.db, a.out) or 0 if a.action == "export" else check(a.archive))
