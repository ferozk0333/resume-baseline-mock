"""Phase C before/after report (specs/19 item 19), offline: no provider is called.

    python scripts/dedupe_report.py [--db data/demo.db] [--candidate "<live profile name>"]

Works on a scratch copy of the demo database. Judging is replayed (judges/replay_judge.py): a question gets the answer
recorded for exactly the same input; a question never asked before is counted as "unrecorded" (a live run would ask
it), never guessed, and its subcategory counts as an error here.

1. The latest live profile: scored as saved (before), then re-merged with today's rules (extraction.store.remerge,
   which only merges duplicates) and scored again (after), per target role.
2. The 16 test profiles: each rebuilt twice from its file, with the old merge (DEDUPE_NAMES off) and the new one.
3. Gates: ranking per role, the keyword-stuffer/padded rule (a gain must come only from merged duplicates, listed by
   name), and near-miss name pairs that were NOT merged (suggestions for config/claim_aliases.json).
Writes timings/dedupe_report.md and .json (personal data stays out of git: timings/ is ignored).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

from core import config, db  # noqa: E402
from extraction import dedupe, store  # noqa: E402
from parser_gates import EXPECTED, PROFILES, STUFFER, db_copy, final_of, fmt, score  # noqa: E402

WORK = ROOT / "data" / "dedupe-report.db"
METRICS = ("proof_density", "tools", "validation_strength")


def counts(conn, cid: str) -> dict:
    p = store.load_profile(conn, cid)
    return {"claims": len([c for c in p.claims if c.kind != "award"]), "awards": len([c for c in p.claims if c.kind == "award"]),
            "certifications": len(p.certifications), "projects": len(p.projects), "roles": len(p.roles)}


def names(conn, cid: str) -> dict[str, list[str]]:
    p = store.load_profile(conn, cid)
    return {"claims": sorted(c.name_raw for c in p.claims), "certifications": sorted(c.name for c in p.certifications),
            "projects": sorted(x.name for x in p.projects)}


def scored(work: str, cid: str, baseline: str, today: dt.date, prefer: tuple[str, ...]) -> dict:
    res, judge = score(work, cid, baseline, today, prefer)
    conn = db.connect(work)
    out = {"final": res.get("final"), "unrecorded": sorted(set(judge.misses + judge.route_misses)),
           "unrecorded_n": len(judge.missed_inputs), "run": res.get("run_id")}
    if res.get("run_id"):
        _, scores = final_of(conn, res["run_id"])
        out.update({m: scores[m]["score"] for m in METRICS})
    conn.close()
    return out


def suggestions(conn, cid: str) -> list[str]:
    p = store.load_profile(conn, cid)
    pairs = dedupe.suggestions([c.name_raw for c in p.claims]) + dedupe.suggestions([c.name for c in p.certifications])
    return [f"{a} / {b} ({why})" for a, b, why in pairs]


def removed(before: dict, after: dict) -> dict[str, list[str]]:
    out = {}
    for k in before:
        left = list(after[k])
        gone = []
        for n in before[k]:
            if n in left:
                left.remove(n)
            else:
                gone.append(n)
        if gone:
            out[k] = gone
    return out


# --- 1. The latest live profile ----------------------------------------------------------------------------------

def live_section(work: str, name: str) -> tuple[list[str], dict]:
    conn = db.connect(work)
    row = conn.execute("SELECT id FROM candidates WHERE name = ? ORDER BY created_at DESC LIMIT 1", (name,)).fetchone()
    if row is None:
        return [f"## 1. {name}", "", "Not found in this database."], {}
    cid = row[0]
    runs = conn.execute("SELECT baseline_id, MAX(started_at) FROM runs WHERE candidate_id = ? AND status = 'done' "
                        "GROUP BY baseline_id", (cid,)).fetchall()
    before_counts, before_names, sugg = counts(conn, cid), names(conn, cid), suggestions(conn, cid)
    conn.close()
    before = {b: scored(work, cid, b, dt.date.fromisoformat(t[:10]), (cid,)) for b, t in runs}
    conn = db.connect(work)
    merged = store.remerge(conn, cid)
    after_counts = counts(conn, cid)
    conn.close()
    after = {b: scored(work, cid, b, dt.date.fromisoformat(t[:10]), (cid,)) for b, t in runs}
    out = [f"## 1. {name} (the latest live profile)", "",
           "| | before | after |", "|---|---|---|"]
    for k in ("claims", "awards", "certifications", "projects", "roles"):
        out.append(f"| {k} | {before_counts[k]} | {after_counts[k]} |")
    out += ["", "| role | | final | proof density | Tools | Validation strength | unrecorded judgments |",
            "|---|---|---|---|---|---|---|"]
    for b in before:
        for label, r in (("before", before[b]), ("after", after[b])):
            out.append(f"| {b} | {label} | {fmt(r['final'], 2)} | {fmt(r.get('proof_density'), 3)} | "
                       f"{fmt(r.get('tools'), 3)} | {fmt(r.get('validation_strength'), 2)} | {r['unrecorded_n']} |")
    out += ["", "**Merged away** (each merged into the item that stayed):", ""]
    for t, ns in merged["merged_away"].items():
        out.append(f"- {t}: {', '.join(ns)}")
    out += ["", "**Near misses, not merged** (add to config/claim_aliases.json only if they are the same thing):", ""]
    out += [f"- {s}" for s in sugg] or ["- none"]
    if any(r["unrecorded"] for r in after.values()):
        out += ["", "Unrecorded questions after the merge: " +
                ", ".join(sorted({q for r in after.values() for q in r["unrecorded"]}))]
    return out, {"cid": cid, "before": before, "after": after, "counts": [before_counts, after_counts],
                 "merged": merged["merged_away"], "suggestions": sugg, "names_before": before_names}


# --- 2. The 16 test profiles ----------------------------------------------------------------------------------------

def profiles_section(work: str) -> tuple[list[str], dict]:
    from extraction.structured import load_profile_file, read_profile_file
    from report_test_profiles import profile_id
    from score_test_profiles import targets
    conn = db.connect(work)
    rows = {}
    for a in targets(conn):
        if not a["run_id"]:
            continue
        pid = profile_id(conn, a["candidate_id"])
        run = conn.execute("SELECT started_at, baseline_id FROM runs WHERE id = ?", (a["run_id"],)).fetchone()
        rec, _ = final_of(conn, a["run_id"])
        rows[pid] = {"baseline": run[1], "today": dt.date.fromisoformat(run[0][:10]), "recorded": rec.final,
                     "original": a["candidate_id"]}
    out = ["## 2. The 16 test profiles", "",
           "Each profile is rebuilt from its file twice: with the old merge (*before*) and with the Phase C merge "
           "(*after*). Both are scored with replayed judgments; *before* must reproduce the recorded score.", "",
           "| profile | recorded | before | after | Δ | claims | certs | projects | proof density | Tools | "
           "Validation | unrecorded | merged |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    results = {}
    for path in sorted(PROFILES.glob("*/*.md")):
        pf = read_profile_file(path)
        if pf.id not in rows:
            continue
        info = rows[pf.id]
        built = {}
        for label, on in (("before", False), ("after", True)):
            config.DEDUPE_NAMES = on
            cid = load_profile_file(conn, path)
            built[label] = {"cid": cid, "counts": counts(conn, cid), "names": names(conn, cid),
                            **scored(work, cid, info["baseline"], info["today"], (info["original"],))}
        config.DEDUPE_NAMES = True
        b, a = built["before"], built["after"]
        merged = removed(b["names"], a["names"])
        delta = a["final"] - b["final"] if a["final"] is not None and b["final"] is not None else None
        results[pf.id] = {"control": b["final"], "parser": a["final"], "control_cid": b["cid"], "cid": a["cid"],
                          "run": a["run"], "recorded": info["recorded"], "merged": merged,
                          "suggestions": suggestions(conn, a["cid"]), "before": b, "after": a}
        bc, ac = b["counts"], a["counts"]

        def pair(k, d=3):
            return f"{fmt(b.get(k), d)} → {fmt(a.get(k), d)}" if b.get(k) != a.get(k) else fmt(a.get(k), d)
        out.append(f"| {pf.id} | {fmt(info['recorded'])} | {fmt(b['final'], 2)} | {fmt(a['final'], 2)} | "
                   f"{fmt(delta, 2) if delta is not None else '—'} | {bc['claims']} → {ac['claims']} | "
                   f"{bc['certifications']} → {ac['certifications']} | {bc['projects']} → {ac['projects']} | "
                   f"{pair('proof_density')} | {pair('tools')} | {pair('validation_strength', 2)} | "
                   f"{b['unrecorded_n']} / {a['unrecorded_n']} | "
                   f"{'; '.join(f'{k}: {len(v)}' for k, v in merged.items()) or '—'} |")
    conn.close()
    return out, results


def ranking(results: dict) -> list[str]:
    out = ["**Ranking per role** (expected " + EXPECTED + "):", ""]
    for role in ("DS", "AI", "PM"):
        for col, label in (("control", "before"), ("parser", "after")):
            ids = [(r[col], pid) for pid, r in results.items() if pid.startswith(role) and "_" not in pid
                   and r.get(col) is not None]
            order = [pid[2:] for _, pid in sorted(ids, reverse=True)]
            ok = len(order) == 5 and order[0] == "1" and set(order[1:3]) == {"3", "4"} and order[3:] == ["2", "5"]
            out.append(f"- {role} {label}: {' > '.join(order)} → {'PASS' if ok else 'FAIL'}")
    return out


def stuffer_gate(results: dict) -> list[str]:
    """specs/19 item 19: any gain for an ID-2 or padded profile must come only from merged duplicates, by name."""
    out = ["**Keyword-stuffer and padded profiles** (a gain must come only from merged duplicates):", ""]
    for pid, r in sorted(results.items()):
        if not STUFFER.search(pid) or r["control"] is None or r["parser"] is None:
            continue
        gain = r["parser"] - r["control"]
        listed = "; ".join(f"{k}: {', '.join(v)}" for k, v in r["merged"].items())
        ok = gain <= 1e-9 or bool(r["merged"])
        why = "no gain" if gain <= 1e-9 else f"merged duplicates: {listed}" if r["merged"] else "gained with nothing merged"
        out.append(f"- {pid}: {gain:+.2f} ({why}) → {'PASS' if ok else 'FAIL'}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    ap.add_argument("--candidate", default=os.getenv("LIVE_PROFILE", ""))
    a = ap.parse_args()
    db_copy(Path(a.db), WORK)
    work = str(WORK)
    live, live_data = live_section(work, a.candidate)
    prof, results = profiles_section(work)
    near = sorted({s for r in results.values() for s in r["suggestions"]})
    lines = ["# Phase C before/after (specs/19 item 19)", "",
             f"Offline, replayed judgments, {dt.date.today().isoformat()}. Scoring formulas, weights and logic_version "
             "unchanged. A question with no recording is never guessed: it is counted as unrecorded.", "",
             *live, "", *prof, "", *ranking(results), "", *stuffer_gate(results), "",
             "**Near misses in the test profiles, not merged:**", "", *([f"- {s}" for s in near] or ["- none"]), ""]
    misses = sum(r["after"]["unrecorded_n"] for r in results.values()) + \
        sum(r["unrecorded_n"] for r in live_data.get("after", {}).values())
    lines += [f"New model calls this re-scoring would need (unrecorded unique questions after the merge): **{misses}**."]
    (ROOT / "timings").mkdir(exist_ok=True)
    (ROOT / "timings" / "dedupe_report.md").write_text("\n".join(lines) + "\n")
    (ROOT / "timings" / "dedupe_report.json").write_text(json.dumps(
        {"live": live_data, "profiles": {k: {x: y for x, y in v.items() if x not in ("before", "after")} | {
            "before": {x: y for x, y in v["before"].items() if x != "names"},
            "after": {x: y for x, y in v["after"].items() if x != "names"}} for k, v in results.items()}},
        indent=1, default=str))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
