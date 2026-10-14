"""Report on the scored test profiles (each against its own role's job). No model calls.

    python scripts/report_test_profiles.py [--db data/demo.db]

Uses DESIGN.md's expectations for the report only (never as pipeline input): expected order
1 > {3, 4} > 2 > 5, plus its specific checks. Writes timings/test_profiles_report.md.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import recruiter as rc  # noqa: E402
from core import config, db  # noqa: E402
from extraction import store  # noqa: E402
from graph.pipeline import run_scores  # noqa: E402
from scoring.aggregate import aggregate  # noqa: E402

EXPECTED = "1 > {3, 4} > 2 > 5"


def profile_id(conn, cid: str) -> str | None:
    ev = next((e for e in store.list_evidence(conn, cid) if e["type"] == "resume" and e["file_path"]), None)
    m = re.search(r"/([A-Z]{2}\d(?:_padded)?)_resume", ev["file_path"]) if ev else None
    return m.group(1) if m else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("RIGHTSTAFF_DEMO_DB", str(ROOT / "data" / "demo.db")))
    conn = db.connect(ap.parse_args().db)
    out = ["# Test profiles scored with real judging (Jev + Gemini 3.5 Flash-Lite)", "",
           f"Each profile against its own role's job, default weights, no plans. Expected order (DESIGN.md): {EXPECTED}.", ""]
    rows = {}
    for job in rc.list_jobs(conn):
        apps = conn.execute("SELECT a.* FROM applications a JOIN candidates c ON c.id = a.candidate_id WHERE a.job_id = ? "
                            "AND c.data_source = 'demo_parsed' AND a.run_id IS NOT NULL", (job["id"],)).fetchall()
        table = []
        for a in apps:
            pid = profile_id(conn, a["candidate_id"])
            scores = run_scores(conn, a["run_id"])
            agg = aggregate(scores, config.default_weights())
            errors = sum(1 for v in scores.values() if v["status"] == "error")
            table.append((agg.final or 0, pid, agg, scores, errors))
            rows[pid] = (agg, scores, a["candidate_id"], a["run_id"])
        table.sort(key=lambda r: -r[0])
        order = " > ".join(re.sub(r"^[A-Z]{2}", "", r[1] or "?") for r in table)
        out += [f"## {job['title']} ({job['company']})", "", f"Ranking by ID: **{order}**  (expected {EXPECTED})", "",
                "| rank | profile | final | experience | expertise | authority | trust | errors |", "|---|---|---|---|---|---|---|---|"]
        fmt = lambda v: "—" if v is None else f"{100 * v:.0f}"  # noqa: E731
        for i, (final, pid, agg, _, errors) in enumerate(table, 1):
            d = agg.dimensions
            out.append(f"| {i} | {pid} | {final:.1f} | {fmt(d['experience'])} | {fmt(d['expertise'])} | "
                       f"{fmt(d['authority'])} | {fmt(d['trust'])} | {errors} |")
        out.append("")

    out += ["## Specific checks (DESIGN.md)", ""]
    if "DS4" in rows and "DS4_padded" in rows:
        a, b = rows["DS4"], rows["DS4_padded"]
        out.append(f"- **DS4 vs DS4_padded**: final {a[0].final:.1f} vs {b[0].final:.1f} "
                   f"(difference {b[0].final - a[0].final:+.1f}).")
        diffs = [(s, a[1][s]["score"], b[1][s]["score"]) for s in config.SUBCATEGORY_DIMENSION
                 if a[1][s]["score"] != b[1][s]["score"]]
        out += [f"  - {config.SUBCATEGORY_LABELS[s]}: {x} → {y}" for s, x, y in diffs] or ["  - every subcategory equal"]
    for pid in ("AI2", "DS3"):
        if pid in rows:
            agg, scores, cid, _ = rows[pid]
            forks = [p for p in store.load_profile(conn, cid).projects if p.is_fork and not p.own_commits]
            counted = set(scores["depth"]["details"].get("counted_project_ids", [])) | \
                {s["id"] for s in scores["work_samples"]["details"].get("samples", [])}
            leaked = [p.name for p in forks if p.id in counted]
            out.append(f"- **{pid} forks with 0 own commits not counted**: {len(forks)} fork(s); "
                       f"{'PASS' if not leaked else 'FAIL ' + str(leaked)}.")
    if "PM4" in rows:
        d = rows["PM4"][1]["validation_strength"]
        ok = d["details"].get("reference_id") is None
        out.append(f"- **PM4 peer reference gets no manager credit**: validation strength {d['status']} {d['score']}, "
                   f"level {d['details'].get('level')} → {'PASS' if ok else 'FAIL'}.")
    for pid in ("DS2", "PM2"):
        if pid in rows:
            c = rows[pid][1]["consistency"]
            aligned = [s.get("aligned") for s in c["details"].get("sources", [])]
            ok = c["status"] == "scored" and c["score"] == 0
            out.append(f"- **{pid} Consistency not aligned**: {c['status']} {c['score']} (sources aligned: {aligned}) "
                       f"→ {'PASS' if ok else 'FAIL'}.")
    for pid in ("AI5", "DS5", "PM5"):
        if pid in rows:
            missing = {s: v for s, v in rows[pid][1].items() if v["status"] == "missing"}
            without = [s for s, v in missing.items() if not v["guidance"]]
            out.append(f"- **{pid} guidance on every missing subcategory**: {len(missing)} missing, "
                       f"{'PASS' if not without else 'FAIL ' + str(without)}.")
    errors = [(pid, s) for pid, r in rows.items() for s, v in r[1].items() if v["status"] == "error"]
    out += ["", f"Subcategories with status error: {len(errors)} {errors if errors else ''}"]
    path = ROOT / "timings" / "test_profiles_report.md"
    path.write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
