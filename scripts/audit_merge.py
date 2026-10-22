"""Audit round, items 2f and 4: re-merge saved profiles with the new merge rules and repair stored links.

    python scripts/audit_merge.py estimate        # offline, on a copy: what merges, and the questions to re-judge
    LIVE=1 LLM_PROVIDER=gemini RIGHTSTAFF_DB=data/demo.db python scripts/audit_merge.py run    # synthetic only
    python scripts/audit_merge.py apply           # demo.db (back it up first): repair, re-merge, replay re-score
    python scripts/audit_merge.py scored-on       # runs.scored_on for runs saved before schema 28

Links (no model calls): pymupdf4llm's "%0x28" / "%0x29" escapes are decoded in every stored text and URL, and a
certificate or publication read by the code-first parser without its link gets the link written on its own line of
the source text (the parser used to drop it).
Scope of the re-score: the 16 test profiles (synthetic) against their own role, and the latest real profile
(REAL_PROFILE, env) against its scored roles, replay only: a real profile is never sent to a model here.
Questions whose input changed because of the merge are the only ones asked live, synthetic profiles only.
Writes timings/audit_merge.json (timings/ is not in git).
"""
from __future__ import annotations

import argparse
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

from core import config, db  # noqa: E402
from core.links import decode_pdf_escapes, find_urls  # noqa: E402
from extraction import store  # noqa: E402

DEMO = ROOT / "data" / "demo.db"
ESTIMATE_DB = ROOT / "data" / "audit-estimate.db"
WORK = ROOT / "data" / "audit-live.db"
OUT = ROOT / "timings" / "audit_merge.json"
REAL_NAME = os.getenv("REAL_PROFILE", "")
SYNTHETIC = ("demo_parsed", "test_variant")


# --- Links ------------------------------------------------------------------------------------------------------

def repair_links(conn) -> dict[str, int]:
    """Decode "%0x28" escapes everywhere a link is stored; give parser-read certificates and publications the link
    written next to them in the source. Code only."""
    counts: dict[str, int] = {}
    targets = {"evidence_items": ("raw_text", "extracted_json"), "document_texts": ("text",),
               "projects": ("url", "description", "quote"), "certifications": ("verify_url", "name"),
               "claims": ("quote", "name_raw"), "bullets": ("text",), "dropped_items": ("text", "payload")}
    with conn:
        for table, cols in targets.items():
            key = "file_sha256" if table == "document_texts" else "id"
            for col in cols:
                rows = conn.execute(f"SELECT {key}, {col} FROM {table} WHERE {col} LIKE '%\\%0x%' ESCAPE '\\'"
                                    ).fetchall()
                for k, v in rows:
                    fixed = decode_pdf_escapes(v)
                    if fixed != v:
                        conn.execute(f"UPDATE {table} SET {col} = ? WHERE {key} = ?", (fixed, k))
                        counts[f"{table}.{col}"] = counts.get(f"{table}.{col}", 0) + 1
        texts = {r[0]: r[1] or "" for r in conn.execute("SELECT id, raw_text FROM evidence_items")}

        def link_for(evidence_id, name):
            for line in texts.get(evidence_id, "").splitlines():
                if name and name[:40] in line:
                    urls = find_urls(line)
                    if urls:
                        return urls[0][2]
            return None
        for cid, eid, name in conn.execute("SELECT id, evidence_id, name FROM certifications WHERE "
                                           "COALESCE(verify_url, '') = '' AND method = 'parser'").fetchall():
            url = link_for(eid, name)
            if url:
                conn.execute("UPDATE certifications SET verify_url = ?, verified = 1 WHERE id = ?", (url, cid))
                counts["certifications.verify_url (from the source line)"] = \
                    counts.get("certifications.verify_url (from the source line)", 0) + 1
        for pid, eid, name in conn.execute("SELECT id, evidence_id, name FROM projects WHERE kind = 'publication' AND "
                                           "COALESCE(url, '') = '' AND method = 'parser'").fetchall():
            url = link_for(eid, name)
            if url:
                conn.execute("UPDATE projects SET url = ? WHERE id = ?", (url, pid))
                counts["projects.url (publication, from the source line)"] = \
                    counts.get("projects.url (publication, from the source line)", 0) + 1
    return counts


# --- Scope -------------------------------------------------------------------------------------------------------

def targets(conn) -> list[dict]:
    from score_test_profiles import targets as test_targets
    from report_test_profiles import profile_id
    out = []
    for a in test_targets(conn):
        if a["run_id"]:
            run = conn.execute("SELECT started_at, baseline_id FROM runs WHERE id = ?", (a["run_id"],)).fetchone()
            out.append({"key": profile_id(conn, a["candidate_id"]), "cid": a["candidate_id"], "run": a["run_id"],
                        "app": a["id"], "baseline": run[1], "today": run[0][:10], "real": False})
    if REAL_NAME:
        row = conn.execute("SELECT id FROM candidates WHERE name = ? AND data_source IS NULL ORDER BY created_at DESC "
                           "LIMIT 1", (REAL_NAME,)).fetchone()
        if row:
            for b, run_id, started in conn.execute(
                    "SELECT baseline_id, id, MAX(started_at) FROM runs WHERE candidate_id = ? AND status = 'done' "
                    "GROUP BY baseline_id", (row[0],)):
                out.append({"key": f"{REAL_NAME} · {b}", "cid": row[0], "run": run_id, "baseline": b, "app": None,
                            "today": started[:10], "real": True})
    for t in out:
        t["before"] = _final(conn, t["run"])
        t["recognition_before"] = _sub(conn, t["run"], "recognition")
    return out


def _final(conn, run_id):
    from parser_gates import final_of
    return final_of(conn, run_id)[0].final


def _sub(conn, run_id, name):
    row = conn.execute("SELECT status, score FROM scores WHERE run_id = ? AND subcategory = ? ORDER BY computed_at DESC "
                       "LIMIT 1", (run_id, name)).fetchone()
    return [row[0], row[1]] if row else None


def remerge_all(conn) -> dict:
    """Every saved profile except the scripted archetypes. Returns what changed per candidate."""
    out = {}
    for cid, name, ds in conn.execute("SELECT id, name, data_source FROM candidates WHERE "
                                      "COALESCE(data_source, '') != 'archetype'").fetchall():
        res = store.remerge(conn, cid)
        changed = {k: v for k, v in [("merged_away", res["merged_away"]), ("unused_bullets", res["unused_bullets"]),
                                     ("placements", res["placements"]), ("education", res["education"])] if v}
        if changed:
            out[cid] = {"name": name, "data_source": ds, **changed}
    return out


def _copy(dst: Path) -> None:
    from parser_gates import db_copy
    db_copy(DEMO, dst)


# --- Steps ---------------------------------------------------------------------------------------------------------

def estimate() -> None:
    from parser_gates import score
    _copy(ESTIMATE_DB)
    conn = db.connect(str(ESTIMATE_DB))
    links = repair_links(conn)
    rows = targets(conn)
    merged = remerge_all(conn)
    conn.close()
    per_cid: dict[str, set] = {}
    for t in rows:
        res, j = score(str(ESTIMATE_DB), t["cid"], t["baseline"], dt.date.fromisoformat(t["today"]), (t["cid"],))
        per_cid.setdefault(t["cid"], set()).update(j.missed_inputs)
        t["missed"] = len(j.missed_inputs)
        t["missed_questions"] = sorted({q for q, _, _ in j.missed_inputs})
    synthetic = sum(len(v) for cid, v in per_cid.items() if not any(t["real"] for t in rows if t["cid"] == cid))
    real = sum(len(v) for cid, v in per_cid.items() if any(t["real"] for t in rows if t["cid"] == cid))
    state = {"links": links, "merged": merged, "targets": {t["key"]: t for t in rows},
             "estimate": {"synthetic_questions": synthetic, "real_questions": real}}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(state, indent=1, default=str))
    print("Links repaired:", links)
    for cid, m in merged.items():
        print(f"  {m['name']} ({m['data_source'] or 'uploaded'}): " +
              "; ".join(f"{k} {len(v)}" for k, v in m.items() if k not in ("name", "data_source")))
    for t in rows:
        if t["missed"]:
            print(f"  {t['key']}: {t['missed']} unrecorded ({', '.join(t['missed_questions'])})")
    print(f"\nQuestions to ask live (unique inputs): synthetic {synthetic}; real profile {real} (never asked here)")
    os.remove(ESTIMATE_DB)


def run() -> None:
    if not config.LIVE:
        sys.exit("Live: set LIVE=1 LLM_PROVIDER=gemini RIGHTSTAFF_DB=data/demo.db.")
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.replay_judge import ReplayJudge
    from judges.service import get_judge
    from readme_live_gate import Hybrid
    state = json.loads(OUT.read_text())
    if not WORK.exists():
        _copy(WORK)
        conn = db.connect(str(WORK))
        repair_links(conn)
        remerge_all(conn)
        conn.close()
    conn = db.connect(str(WORK))
    live = get_judge()
    state.setdefault("live", {})
    for key, t in state["targets"].items():
        if t["real"] or key in state["live"]:
            continue                                   # a real profile is never sent to a model here
        judge = Hybrid(ReplayJudge(conn, (t["cid"],)), live)
        res = Pipeline(Deps(str(WORK), judge, today=dt.date.fromisoformat(t["today"]), planner=None),
                       print_timings=False).score(t["cid"], t["baseline"], plan=False)
        if res.get("paused") or not res.get("run_id"):
            print(f"Stopped while scoring {key}: {res.get('paused') or res}. Run again later.")
            break
        state["live"][key] = {"asked": dict(judge.asked)}
        OUT.write_text(json.dumps(state, indent=1, default=str))
        print(f"{key}: asked live {sum(judge.asked.values())}", flush=True)
    conn.close()


def apply() -> None:
    from parser_gates import score
    assert any(p.name.startswith("demo.db.bak-audit-") for p in (ROOT / "data").iterdir()), "back up demo.db first"
    state = json.loads(OUT.read_text())
    copied = 0
    if WORK.exists():
        a = sqlite3.connect(str(DEMO))
        a.execute("ATTACH DATABASE ? AS live", (str(WORK),))
        cols = ", ".join(r[1] for r in a.execute("PRAGMA main.table_info(judgments)"))
        before = a.execute("SELECT COUNT(*) FROM main.judgments").fetchone()[0]
        with a:
            a.execute(f"INSERT OR IGNORE INTO main.judgments ({cols}) SELECT {cols} FROM live.judgments")
        copied = a.execute("SELECT COUNT(*) FROM main.judgments").fetchone()[0] - before
        a.execute("DETACH DATABASE live")
        a.close()
    conn = db.connect(str(DEMO))
    rows = targets(conn)                               # before values from demo.db as it is now
    links = repair_links(conn)
    merged = remerge_all(conn)
    conn.close()
    results = {}
    for t in rows:
        res, judge = score(str(DEMO), t["cid"], t["baseline"], dt.date.fromisoformat(t["today"]), (t["cid"],))
        conn = db.connect(str(DEMO))
        if judge.missed_inputs or not res.get("run_id"):
            results[t["key"]] = {"before": t["before"], "kept_run": t["run"],
                                 "unrecorded": sorted({q for q, _, _ in judge.missed_inputs})}
            print(f"{t['key']}: {len(judge.missed_inputs)} unrecorded question(s); kept run {t['run']}", flush=True)
            conn.close()
            continue
        if t["app"]:
            with conn:
                conn.execute("UPDATE applications SET run_id = ? WHERE id = ?", (res["run_id"], t["app"]))
        after = _final(conn, res["run_id"])
        results[t["key"]] = {"before": t["before"], "after": after, "run": res["run_id"],
                             "recognition": [t["recognition_before"], _sub(conn, res["run_id"], "recognition")],
                             "moved": _moves(conn, t["run"], res["run_id"])}
        conn.close()
        print(f"{t['key']}: {t['before']:.2f} → {after:.2f}", flush=True)
    state["applied"] = {"judgments_copied": copied, "links": links, "merged": merged, "scores": results}
    OUT.write_text(json.dumps(state, indent=1, default=str))
    print(f"\njudgments copied {copied}; links {links}; profiles changed {len(merged)}")


def backfill_scored_on() -> None:
    """runs.scored_on (schema 28) for runs saved before it: the archetypes' fixed scoring date, else the day the run
    started (local time, as config.today() gave it)."""
    from tests.fixtures.archetypes import TODAY
    conn = db.connect(str(DEMO))
    with conn:
        n = conn.execute("UPDATE runs SET scored_on = ? WHERE scored_on IS NULL AND candidate_id IN "
                         "(SELECT id FROM candidates WHERE data_source = 'archetype')", (TODAY.isoformat(),)).rowcount
        m = 0
        for rid, started in conn.execute("SELECT id, started_at FROM runs WHERE scored_on IS NULL").fetchall():
            day = dt.datetime.fromisoformat(started.replace("Z", "+00:00")).astimezone().date()
            conn.execute("UPDATE runs SET scored_on = ? WHERE id = ?", (day.isoformat(), rid))
            m += 1
    print(f"scored_on: {n} archetype runs set to {TODAY}; {m} other runs set to their start day")


def _moves(conn, a_run, b_run) -> dict[str, list]:
    from graph.pipeline import run_scores
    a, b = run_scores(conn, a_run), run_scores(conn, b_run)
    return {s: [a.get(s, {}).get("score"), b.get(s, {}).get("score")] for s in sorted(set(a) | set(b))
            if (a.get(s, {}).get("status"), a.get(s, {}).get("score")) != (b.get(s, {}).get("status"),
                                                                           b.get(s, {}).get("score"))}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=("estimate", "run", "apply", "scored-on"))
    {"estimate": estimate, "run": run, "apply": apply, "scored-on": backfill_scored_on}[ap.parse_args().step]()
