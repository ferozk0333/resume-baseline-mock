"""Remove one real person's data from a database and the local files around it (deploy prep, item 3).

    python scripts/purge_person.py --db data/demo.db --name-prefix "Jane Doe" --name "Jane test 5" \
        --marker jane --github jdoe-dev [--dry-run]

Candidates: every name starting with --name-prefix, plus each exact --name. Everything linked to them goes:
documents, extractions, the profile tables, judgments, runs, scores, gap reports and summaries, applications and
their workflows, emails, messages, interviews, scorecards, activity, tasks and their events, parser runs, screening
flags, display-only items, Review memory, onboarding state and the LangGraph checkpoints of their threads
(<db stem>.graphs.db). Caches: document texts of their files, extraction-cache and HTTP-cache rows whose text names a
--marker (and GitHub responses for --github). Files: their upload folders, lines of data/live-api.log naming them,
and report files under timings/ and data/ that name them. Prints counts per table and file; --dry-run changes nothing.
Then every text column of every table is searched for the markers again and anything left is listed.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def ids(conn, sql: str, params) -> set[str]:
    params = list(params)
    if not params:
        return set()
    return {r[0] for r in conn.execute(sql.format(q=",".join("?" * len(params))), params) if r[0]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    ap.add_argument("--name-prefix", action="append", default=[])
    ap.add_argument("--name", action="append", default=[])
    ap.add_argument("--marker", action="append", default=[], help="text that identifies the person (case-insensitive)")
    ap.add_argument("--github", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    markers = [m.lower() for m in [*a.marker, *a.github] if m]
    conn = sqlite3.connect(a.db)
    removed: Counter = Counter()

    cands = set()
    for p in a.name_prefix:
        cands |= {r[0] for r in conn.execute("SELECT id FROM candidates WHERE name LIKE ? || '%'", (p,))}
    for n in a.name:
        cands |= {r[0] for r in conn.execute("SELECT id FROM candidates WHERE name = ?", (n,))}
    names = [r[0] for r in conn.execute(f"SELECT name FROM candidates WHERE id IN ({','.join('?' * len(cands))})",
                                        list(cands))] if cands else []
    print(f"candidates: {len(cands)} ({', '.join(sorted(set(names)))})")
    ev = ids(conn, "SELECT id FROM evidence_items WHERE candidate_id IN ({q})", cands)
    shas = ids(conn, "SELECT file_sha256 FROM evidence_items WHERE candidate_id IN ({q})", cands)
    files = ids(conn, "SELECT file_path FROM evidence_items WHERE candidate_id IN ({q})", cands)
    runs = ids(conn, "SELECT id FROM runs WHERE candidate_id IN ({q})", cands)
    apps = ids(conn, "SELECT id FROM applications WHERE candidate_id IN ({q})", cands)
    tasks = ids(conn, "SELECT id FROM tasks WHERE candidate_id IN ({q})", cands) | \
        ids(conn, "SELECT id FROM tasks WHERE run_id IN ({q})", runs) | \
        ids(conn, "SELECT read_task_id FROM candidate_onboarding WHERE candidate_id IN ({q})", cands) | \
        ids(conn, "SELECT score_task_id FROM candidate_onboarding WHERE candidate_id IN ({q})", cands)
    threads = ids(conn, "SELECT thread_id FROM workflows WHERE application_id IN ({q})", apps)
    interviews = ids(conn, "SELECT id FROM interviews WHERE application_id IN ({q})", apps)

    def delete(table: str, col: str, values: set) -> None:
        if not values:
            return
        vals = list(values)
        for i in range(0, len(vals), 500):
            chunk = vals[i:i + 500]
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} IN ({','.join('?' * len(chunk))})", chunk
                             ).fetchone()[0]
            if n and not a.dry_run:
                conn.execute(f"DELETE FROM {table} WHERE {col} IN ({','.join('?' * len(chunk))})", chunk)
            removed[table] += n

    plan = [  # (table, column, id set): children first
        ("task_events", "task_id", tasks), ("tasks", "id", tasks),
        ("scorecards", "application_id", apps), ("scorecards", "interview_id", interviews),
        ("interviews", "application_id", apps), ("emails", "application_id", apps),
        ("messages", "application_id", apps), ("workflow_events", "application_id", apps),
        ("workflows", "application_id", apps), ("activity", "application_id", apps),
        ("job_events", "application_id", apps), ("applications", "id", apps),
        ("generated_summaries", "run_id", runs), ("gap_reports", "run_id", runs), ("scores", "run_id", runs),
        ("scores", "candidate_id", cands), ("judgments", "candidate_id", cands), ("judgments", "run_id", runs),
        ("runs", "id", runs),
        ("bullets", "candidate_id", cands), ("claims", "candidate_id", cands), ("certifications", "candidate_id", cands),
        ("projects", "candidate_id", cands), ("roles", "candidate_id", cands), ("references_", "candidate_id", cands),
        ("media_links", "candidate_id", cands), ("extra_items", "candidate_id", cands),
        ("dropped_items", "candidate_id", cands), ("deleted_items", "candidate_id", cands),
        ("suppressed_items", "candidate_id", cands), ("parser_runs", "candidate_id", cands),
        ("prompt_guard_flags", "candidate_id", cands), ("candidate_application_details", "candidate_id", cands),
        ("candidate_job_prefs", "candidate_id", cands), ("candidate_onboarding", "candidate_id", cands),
        ("candidate_plan_done", "candidate_id", cands), ("candidate_targets", "candidate_id", cands),
        ("evidence_items", "candidate_id", cands), ("document_texts", "file_sha256", shas),
        ("candidates", "id", cands),
    ]
    for table, col, vals in plan:
        delete(table, col, vals)

    # Caches that hold their text without a candidate link
    for table, cols in (("extraction_cache", ("output_json", "key")), ("http_cache", ("url", "body", "key")),
                        ("document_texts", ("text",))):
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        cond = " OR ".join(f"lower(CAST({c} AS TEXT)) LIKE ?" for c in cols if c in have for _ in markers)
        if not cond:
            continue
        params = [f"%{m}%" for c in cols if c in have for m in markers]
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {cond}", params).fetchone()[0]
        if n and not a.dry_run:
            conn.execute(f"DELETE FROM {table} WHERE {cond}", params)
        removed[f"{table} (by text)"] += n
    if not a.dry_run:
        conn.commit()

    # Anything left that names them, in any text column of any table
    left = Counter()
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        for col, typ in [(r[1], r[2]) for r in conn.execute(f"PRAGMA table_info({t})")]:
            if typ.upper() in ("INTEGER", "REAL"):
                continue
            for m in markers:
                n = conn.execute(f"SELECT COUNT(*) FROM {t} WHERE lower(CAST({col} AS TEXT)) LIKE ?", (f"%{m}%",)
                                 ).fetchone()[0]
                if n:
                    left[f"{t}.{col}"] += n
    conn.close()

    # LangGraph checkpoints: threads named after their applications, runs or candidates
    graphs = Path(a.db).with_name(Path(a.db).stem + ".graphs.db")
    if graphs.exists():
        g = sqlite3.connect(graphs)
        keys = [*apps, *runs, *cands, *threads]
        for table in ("writes", "checkpoints"):
            rows = [r[0] for r in g.execute(f"SELECT DISTINCT thread_id FROM {table}")]
            hit = [t for t in rows if any(k and k in t for k in keys)]
            for t in hit:
                n = g.execute(f"SELECT COUNT(*) FROM {table} WHERE thread_id = ?", (t,)).fetchone()[0]
                removed[f"graphs.{table}"] += n
                if not a.dry_run:
                    g.execute(f"DELETE FROM {table} WHERE thread_id = ?", (t,))
        if not a.dry_run:
            g.commit()
            g.execute("VACUUM")
        g.close()

    # Files
    file_counts: Counter = Counter()
    for base in (ROOT / "uploads", ROOT / "data" / "demo-uploads"):
        for cid in cands:
            d = base / cid
            if d.exists():
                file_counts[str(d.relative_to(ROOT))] += sum(1 for _ in d.rglob("*") if _.is_file())
                if not a.dry_run:
                    shutil.rmtree(d)
    for f in files:
        p = Path(f)
        if p.exists() and p.is_file():
            file_counts[str(p)] += 1
            if not a.dry_run:
                p.unlink()
    log = ROOT / "data" / "live-api.log"
    if log.exists():
        keys = [*markers, *cands, *runs, *apps]
        lines = log.read_text(errors="replace").splitlines(keepends=True)
        keep = [ln for ln in lines if not any(k and k.lower() in ln.lower() for k in keys)]
        file_counts["data/live-api.log (lines)"] = len(lines) - len(keep)
        if not a.dry_run:
            log.write_text("".join(keep))
    pat = re.compile("|".join(re.escape(m) for m in [*markers, *cands]), re.I)
    for folder, globs in ((ROOT / "timings", ("*",)), (ROOT / "data", ("*.json", "*.md", "*.log", "*.txt"))):
        for gl in globs:
            for p in folder.rglob(gl):
                if not p.is_file() or p.name == "live-api.log":
                    continue
                try:
                    hit = pat.search(p.read_text(errors="replace"))
                except OSError:
                    continue
                if hit:
                    file_counts[f"{p.relative_to(ROOT)} (deleted)"] += 1
                    if not a.dry_run:
                        p.unlink()

    print("\nRows removed per table:")
    for k, v in sorted(removed.items()):
        if v:
            print(f"  {k}: {v}")
    print("\nFiles:")
    for k, v in sorted(file_counts.items()):
        print(f"  {k}: {v}")
    print("\nStill naming them after the purge (table.column: rows):", dict(left) or "none")
    out = ROOT / "timings" / "purge_report.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"candidates": len(cands), "removed": removed, "files": file_counts, "left": left,
                               "dry_run": a.dry_run}, indent=1))


if __name__ == "__main__":
    main()
