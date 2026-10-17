"""Acceptance gates for code-first extraction (specs/18 §4.4), offline: no provider is called.

    python scripts/parser_gates.py [--db data/demo.db] [--feroz-candidate ID]

Works on a scratch copy of the demo database. Judging is replayed (judges/replay_judge.py): a question gets the answer
recorded for exactly the same input; a question never asked before is counted as "unrecorded" (it would need a live
call), never guessed. Jev for parsing is not available offline: every line or name the parsers would send to Jev is
counted ("would ask") and treated as unanswered, so the gates below are the parsers' worst case.

1. Reference (item 26): the LinkedIn parser and the resume shadow parser on the real resume / LinkedIn PDF (the
   originals of tests/extraction_reference/), against the saved reference and against today's LLM extraction.
   READMEs: the README parser on every repo of the most recent live run, against that run's LLM extraction.
2. 16 test profiles (item 27): each profile rebuilt as a new candidate (structured parse = today's demo profile),
   control run first (must reproduce the recorded score), then with the LinkedIn section read by the LinkedIn parser
   from a LinkedIn-style export; final within ±1, ranking 1 > {3,4} > 2 > 5, DESIGN checks.
3. Layout stress set (item 28): the resume shadow parser on 6 layouts × 16 profiles, and the LinkedIn parser on the
   13 exports: parsed correctly / fell back / wrong, against the structured parse.
4. Timing and calls per source (item 29).
Writes timings/parser_gates.md (personal data stays out of git: timings/ is ignored).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

from core import config, db  # noqa: E402
from extraction import store  # noqa: E402
from extraction.parse.claims import vocabulary  # noqa: E402
from extraction.parse.compare import KINDS, compare_profiles, no_fewer_and_dates_ok  # noqa: E402
from extraction.parse.labeller import FakeLabeller  # noqa: E402
from extraction.parse.linkedin import parse_linkedin  # noqa: E402
from extraction.parse.readme import parse_readme, repo_from_text  # noqa: E402
from extraction.parse.resume import parse_resume  # noqa: E402
from extraction.validate import SourceDoc, validate_extraction  # noqa: E402
from ingest.documents import normalize_document  # noqa: E402

REF = ROOT / "tests" / "extraction_reference"
STRESS = ROOT / "tests" / "fixtures" / "profiles" / "rendered" / "stress"
PROFILES = ROOT / "rightstaff-test-profiles" / "profiles"
EXPECTED = "1 > {3, 4} > 2 > 5"


def db_copy(src: Path, dst: Path) -> None:
    """A consistent copy of a live database (the running server's recent writes may still be in its WAL file)."""
    import sqlite3
    if dst.exists():
        dst.unlink()
    a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    with b:
        a.backup(b)
    a.close()
    b.close()


def counting():
    """No Jev offline: answers nothing, counts the requests and questions a live run would send."""
    return FakeLabeller(lambda a: None)


def fmt(v, d=1):
    return "—" if v is None else f"{v:.{d}f}"


# --- Rebuilding profiles from saved dicts ------------------------------------------------------------------------

def score(db_path: str, cid: str, baseline_id: str, today: dt.date, prefer: tuple[str, ...] = ()):
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.replay_judge import ReplayJudge
    conn = db.connect(db_path)
    judge = ReplayJudge(conn, prefer)
    conn.close()
    res = Pipeline(Deps(db_path, judge, planner=None, today=today), print_timings=False).score(cid, baseline_id,
                                                                                             plan=False)
    return res, judge


def final_of(conn, run_id: str):
    from graph.pipeline import run_scores
    from scoring.aggregate import aggregate
    scores = run_scores(conn, run_id)
    return aggregate(scores, config.default_weights()), scores


# --- 1. Reference ---------------------------------------------------------------------------------------------

def structural_pd(p: dict):
    from compare_extraction import structural_proof_density
    return structural_proof_density(p)[0]


def table(c: dict) -> list[str]:
    out = ["| item | reference | parser | found | missing | extra |", "|---|---|---|---|---|---|"]
    for k in KINDS:
        x = c["counts"][k]
        out.append(f"| {k} | {x['reference']} | {x['parser']} | {x['found']} | {x['missing']} | {x['extra']} |")
    d = c["dates"]
    out.append("")
    out.append(f"Dates: {d['identical']} of {d['compared']} identical, {d['more_precise']} more precise, "
               f"{len(d['different'])} different.")
    return out


def reference_section(conn, live_cid: str | None, uploads: dict[str, Path]) -> tuple[list[str], dict]:
    vocab = vocabulary(conn)
    out, gates = ["## 1. Reference: the real resume and LinkedIn PDF", ""], {}
    live = {}
    if live_cid:
        for e in store.list_evidence(conn, live_cid):
            if e["extracted_json"] and e["type"] in ("resume", "linkedin_pdf"):
                live[e["type"]] = json.loads(e["extracted_json"])
    for doc_type, parser in (("linkedin_pdf", "LinkedIn parser (PARSE_LINKEDIN)"), ("resume", "resume shadow parser")):
        ref = json.loads((REF / f"{doc_type}.json").read_text())
        path = uploads[doc_type]
        lab = counting()
        t0 = time.perf_counter()
        if doc_type == "linkedin_pdf":
            res = parse_linkedin(path.read_bytes(), ref["text"], vocab, lab)
        else:
            res = parse_resume(path.name, path.read_bytes(), ref["text"], vocab, lab)
        secs = time.perf_counter() - t0
        out += [f"### {parser}", "",
                f"Outcome: **{res.report.outcome}** {'; '.join(res.report.reasons)} · {secs * 1000:.0f} ms · "
                f"Jev would be asked {lab.counts.questions} question(s) in {lab.counts.requests} request(s) · "
                f"LLM calls 0.", ""]
        if res.extract is None:
            gates[doc_type] = False
            continue
        doc = SourceDoc("ref", doc_type, ref["text"], doc_type)
        got, rep = validate_extraction(doc, res.extract, method_of=res.method_of)
        g = json.loads(json.dumps(asdict(got), default=str))
        out.append(f"Quote check: kept {rep.kept}, dropped {rep.dropped}.")
        for name, other in (("saved reference (Claude Haiku, older prompt)", ref["profile"]),
                            ("today's LLM extraction (live run, current prompt)", live.get(doc_type))):
            if other is None:
                continue
            c = compare_profiles(other, g)
            pa, pb = structural_pd(other), structural_pd(g)
            ok = no_fewer_and_dates_ok(c) and pa is not None and pb is not None and abs(pb - pa) <= 0.01
            gates[f"{doc_type}:{name[:5]}"] = ok
            out += ["", f"**Against the {name}**: gate 26 {'PASS' if ok else 'FAIL'}", "", *table(c), "",
                    f"Proof density (structural): {fmt(pa, 3)} → {fmt(pb, 3)}"
                    + (f" ({pb - pa:+.3f})" if pa is not None and pb is not None else "")]
            for k in KINDS:
                if c["missing"][k]:
                    out.append(f"- missing {k}: " + "; ".join(c["missing"][k]))
                if c["extra"][k]:
                    out.append(f"- extra {k} (verbatim in the source): " + "; ".join(c["extra"][k]))
            if c["dates"]["different"]:
                out.append("- dates: " + "; ".join(c["dates"]["different"]))
        out.append("")
    return out, gates


def readme_section(conn, live_cid: str | None) -> tuple[list[str], dict]:
    out = ["## 1b. READMEs of the most recent live run (PARSE_README)", ""]
    if not live_cid:
        return out + ["No live run found."], {}
    vocab = vocabulary(conn)
    rows = [e for e in store.list_evidence(conn, live_cid) if e["type"] == "github_repo"]
    stats = {"parsed": 0, "fallback": 0, "would_ask": 0, "requests": 0, "seconds": [], "llm_claims": 0,
             "parser_claims": 0, "llm_bullets": 0, "llm_calls_saved": 0}
    lines = ["| repo | outcome | blocks kept / all | unclear | Jev would ask | claims parser / LLM | description chars |",
             "|---|---|---|---|---|---|---|"]
    for e in rows:
        repo = repo_from_text(e["raw_text"])
        if not repo.readme or not repo.counted:
            continue
        lab = counting()
        res = parse_readme(repo, vocab, lab)
        r = res.report
        stats[r.outcome] += 1
        stats["would_ask"] += lab.counts.questions
        stats["requests"] += lab.counts.requests
        stats["seconds"].append(r.seconds)
        llm = json.loads(e["extracted_json"]) if e["extracted_json"] else {"claims": [], "projects": []}
        llm_claims = len(llm.get("claims", []))
        stats["llm_claims"] += llm_claims
        stats["llm_bullets"] += sum(len(p.get("bullets", [])) for p in llm.get("projects", []))
        if r.ok:
            stats["llm_calls_saved"] += 1
            stats["parser_claims"] += len(res.extract.claims)
            stats["parser_bullets"] = stats.get("parser_bullets", 0) + len(res.extract.projects[0].bullets)
            desc = res.extract.projects[0].description or ""
            lines.append(f"| {repo.name[:38]} | parsed | {r.items.get('kept_blocks')} / {r.items.get('blocks')} | "
                         f"{r.unclear} | {lab.counts.questions} | {len(res.extract.claims)} / {llm_claims} | {len(desc)} |")
        else:
            lines.append(f"| {repo.name[:38]} | fallback: {'; '.join(r.reasons)} | | {r.unclear} | "
                         f"{lab.counts.questions} | — / {llm_claims} | |")
    n = stats["parsed"] + stats["fallback"]
    out += [f"{n} READMEs: **{stats['parsed']} parsed, {stats['fallback']} fell back to the LLM** (no Jev offline: "
            f"every unclear block counts as unanswered). Parser time median "
            f"{statistics.median(stats['seconds']) * 1000:.0f} ms. LLM calls saved: {stats['llm_calls_saved']}. "
            f"With Jev live: {stats['would_ask']} question(s) in {stats['requests']} request(s).", "",
            f"Claims: parser {stats['parser_claims']} (languages from the API; vocabulary names need Jev) vs LLM "
            f"{stats['llm_claims']}. Bullets: LLM {stats['llm_bullets']}, parser {stats.get('parser_bullets', 0)} (one per "
            "sentence, list item or table row of the kept result blocks; 'what was built' blocks go to the description).", "", *lines, ""]
    return out, stats


# --- 2. 16 profiles ---------------------------------------------------------------------------------------------

def profiles_section(work_db: str) -> tuple[list[str], dict]:
    from extraction.structured import read_profile_file
    from report_test_profiles import profile_id
    from score_test_profiles import targets
    conn = db.connect(work_db)
    vocab = vocabulary(conn)
    rows = {}
    for a in targets(conn):
        if not a["run_id"]:
            continue
        pid = profile_id(conn, a["candidate_id"])
        run = conn.execute("SELECT started_at, baseline_id FROM runs WHERE id = ?", (a["run_id"],)).fetchone()
        rec, _ = final_of(conn, a["run_id"])
        rows[pid] = {"baseline": run[1], "today": dt.date.fromisoformat(run[0][:10]), "recorded": rec.final,
                     "original": a["candidate_id"]}
    out = ["## 2. The 16 test profiles (PARSE_LINKEDIN)", "",
           "Each profile is rebuilt as a new candidate and scored with replayed judgments. *Control*: today's demo "
           "profile (structured parse) — must reproduce the recorded score. *Parser*: the same, with the LinkedIn "
           "section read by the LinkedIn parser from a LinkedIn-style export of it. Unrecorded = judgments never "
           "asked before (a live run would ask them); those subcategories count as errors here.", "",
           "| profile | recorded | control | unrecorded | LinkedIn parser | Δ | unrecorded | LinkedIn outcome |",
           "|---|---|---|---|---|---|---|---|"]
    results = {}
    readme_rows = ["| profile | control | README parser (replayed) | unrecorded judgments | READMEs |",
                   "|---|---|---|---|---|"]
    from extraction.structured import load_profile_file
    for path in sorted(PROFILES.glob("*/*.md")):
        pf = read_profile_file(path)
        if pf.id not in rows:
            continue
        info = rows[pf.id]
        cid = load_profile_file(conn, path)
        res, judge = score(work_db, cid, info["baseline"], info["today"], (info["original"],))
        control, control_miss = res.get("final"), len(judge.misses)
        li_out, li_final, li_miss, delta = "no LinkedIn", control, 0, 0.0
        export = STRESS / f"{pf.id}_linkedin_export.pdf"
        entry = {"control": control, "parser": control, "cid": cid, "run": res.get("run_id"), "misses": [],
                 "control_cid": cid}
        if pf.sections.get("LinkedIn") and export.exists():
            data = export.read_bytes()
            text = normalize_document(export.name, data)
            lab = counting()
            parsed = parse_linkedin(data, text, vocab, lab)
            li_out = parsed.report.outcome + (f" ({'; '.join(parsed.report.reasons)})" if parsed.report.reasons else "")
            if parsed.extract is not None:
                def read(eid, parsed=parsed, text=text):
                    li, _ = validate_extraction(SourceDoc(eid, "linkedin_pdf", text, "LinkedIn"), parsed.extract,
                                                info["today"], parsed.method_of)
                    return li, text
                cid2 = load_profile_file(conn, path, linkedin=read)
                res2, judge2 = score(work_db, cid2, info["baseline"], info["today"], (info["original"],))
                li_final, li_miss = res2.get("final"), len(judge2.misses)
                delta = (li_final - control) if li_final is not None and control is not None else None
                entry = {"control": control, "parser": li_final, "cid": cid2, "run": res2.get("run_id"),
                         "misses": judge2.misses, "control_cid": cid}
            else:
                li_final, delta = None, None
        rd_final, rd_miss = None, None
        if pf.github_username:
            stats_rd = {"parsed": 0, "fallback": 0}

            def rd(repo, stats_rd=stats_rd):
                r = parse_readme(repo, vocab, counting())
                stats_rd[r.report.outcome] += 1
                return r
            cid3 = load_profile_file(conn, path, readme=rd)
            res3, judge3 = score(work_db, cid3, info["baseline"], info["today"], (info["original"],))
            rd_final, rd_miss = res3.get("final"), len(judge3.misses)
            readme_rows.append(f"| {pf.id} | {fmt(control)} | {fmt(rd_final)} | {len(judge3.misses)} | "
                               f"{stats_rd['parsed']} parsed, {stats_rd['fallback']} fell back |")
        results[pf.id] = {**entry, "recorded": info["recorded"], "control_misses": control_miss,
                          "baseline": info["baseline"]}
        out.append(f"| {pf.id} | {fmt(info['recorded'])} | {fmt(control)} | {control_miss} | {fmt(li_final)} | "
                   f"{fmt(delta, 2) if delta is not None else '—'} | {li_miss} | {li_out} |")
    out += ["", *ranking(results), "", *design_checks(conn, results), "",
            "### PARSE_README on the test profiles", "",
            "Today's demo profiles never read READMEs (repositories count from API metadata only), so there is no "
            "LLM README extraction to compare with. With the README parser the kept README text becomes each "
            "repository's description: those are new judge inputs. They are not recorded, so the score below counts "
            "them as errors; it is NOT the score a live run would give. Gate 27 for PARSE_README therefore needs one "
            "live run (README parser on vs the LLM README extraction).", "", *readme_rows, ""]
    within = [abs(r["parser"] - r["control"]) <= 1 for r in results.values()
              if r.get("parser") is not None and r.get("control") is not None]
    out.insert(4, f"Final within ±1 of the control: **{sum(within)} of {len(within)}**.")
    out.insert(5, "")
    conn.close()
    return out, results


def ranking(results: dict) -> list[str]:
    out = ["**Ranking per role** (expected " + EXPECTED + "):", ""]
    for role in ("DS", "AI", "PM"):
        for col in ("control", "parser"):
            ids = [(r[col], pid) for pid, r in results.items() if pid.startswith(role) and "_" not in pid
                   and r.get(col) is not None]
            order = [pid[2:] for _, pid in sorted(ids, reverse=True)]
            ok = len(order) == 5 and order[0] == "1" and set(order[1:3]) == {"3", "4"} and order[3:] == ["2", "5"]
            out.append(f"- {role} {col}: {' > '.join(order)} → {'PASS' if ok else 'FAIL'}")
    return out


STUFFER = re.compile(r"^[A-Z]{2}2$|_padded$")      # the keyword stuffer (ID 2) and padded variants


def evidence_texts(conn, cid: str) -> set[str]:
    """Everything a judge can read about a candidate: roles, bullets, projects, claims, certifications, references."""
    p = store.load_profile(conn, cid)
    out = {f"role:{r.title}|{r.employer}" for r in p.roles} | {f"bullet:{b.text}" for b in p.all_bullets()}
    out |= {f"project:{x.name}|{x.description or ''}" for x in p.projects}
    out |= {f"claim:{c.name_raw}" for c in p.claims} | {f"cert:{c.name}" for c in p.certifications}
    return out | {f"ref:{r.relationship}|{r.statement}" for r in p.references}


def no_gain_gate(conn, results: dict) -> list[str]:
    """Permanent acceptance gate (spec 18 wrap-up): a keyword-stuffer (ID 2) or padded profile must not gain from a
    change unless its own evidence text changed. results: pid → {control, parser, control_cid, cid}."""
    out = []
    for pid, r in sorted(results.items()):
        if not STUFFER.search(pid) or r.get("control") is None or r.get("parser") is None:
            continue
        gain = r["parser"] - r["control"]
        changed = bool(r.get("control_cid")) and evidence_texts(conn, r["control_cid"]) != evidence_texts(conn, r["cid"])
        ok = gain <= 1e-9 or changed
        why = "its evidence text changed" if changed and gain > 1e-9 else "no gain" if gain <= 1e-9 else \
            "gained with the same evidence text"
        out.append(f"- No gain without new evidence, {pid}: {gain:+.2f} ({why}) → {'PASS' if ok else 'FAIL'}")
    return out


def design_checks(conn, results: dict) -> list[str]:
    from graph.pipeline import run_scores
    out = ["**DESIGN checks** (parser runs):", "", *no_gain_gate(conn, results)]
    scores = {pid: run_scores(conn, r["run"]) for pid, r in results.items() if r.get("run")}
    if "DS4" in results and "DS4_padded" in results:
        out.append(f"- DS4 vs DS4_padded: {fmt(results['DS4']['parser'])} vs {fmt(results['DS4_padded']['parser'])}")
    for pid in ("AI2", "DS3"):
        if pid in scores:
            prof = store.load_profile(conn, results[pid]["cid"])
            forks = [p for p in prof.projects if p.is_fork and not p.own_commits]
            counted = set(scores[pid]["depth"]["details"].get("counted_project_ids", [])) | \
                {s["id"] for s in scores[pid]["work_samples"]["details"].get("samples", [])}
            leaked = [p.name for p in forks if p.id in counted]
            out.append(f"- {pid} forks with 0 own commits not counted: {len(forks)} fork(s) → "
                       f"{'PASS' if not leaked else 'FAIL ' + str(leaked)}")
    if "PM4" in scores:
        d = scores["PM4"]["validation_strength"]
        out.append(f"- PM4 peer reference gets no manager credit: level {d['details'].get('level')} → "
                   f"{'PASS' if d['details'].get('reference_id') is None else 'FAIL'}")
    for pid in ("DS2", "PM2"):
        if pid in scores:
            c = scores[pid]["consistency"]
            out.append(f"- {pid} Consistency not aligned: {c['status']} {c['score']} → "
                       f"{'PASS' if c['status'] == 'scored' and c['score'] == 0 else 'FAIL'}")
    for pid in ("AI5", "DS5", "PM5"):
        if pid in scores:
            missing = {s: v for s, v in scores[pid].items() if v["status"] == "missing"}
            without = [s for s, v in missing.items() if not v["guidance"]]
            out.append(f"- {pid} guidance on every missing subcategory: {len(missing)} missing → "
                       f"{'PASS' if not without else 'FAIL ' + str(without)}")
    return out


# --- 3. Layout stress set -----------------------------------------------------------------------------------------

def _correct(c: dict) -> bool:
    """Parsed correctly: nothing the structured parse has is missing, dates match, and no extra roles, projects or
    certifications (extra claims and bullets are verbatim and reported, not wrong)."""
    return (all(c["counts"][k]["missing"] == 0 for k in KINDS) and not c["dates"]["different"]
            and all(c["counts"][k]["extra"] == 0 for k in ("role", "project", "certification")))


def stress_section() -> tuple[list[str], dict]:
    from extraction.structured import linkedin_profile, read_profile_file, resume_profile
    vocab = vocabulary()
    layouts = ["1col.pdf", "1col.docx", "2col.pdf", "2col.docx", "table.pdf", "table.docx"]
    tally = {lay: {"correct": 0, "fallback": 0, "wrong": 0} for lay in [*layouts, "linkedin_export.pdf"]}
    wrong_notes, reasons, secs, asks = [], {}, [], {}
    for path in sorted(PROFILES.glob("*/*.md")):
        pf = read_profile_file(path)
        truth_resume = json.loads(json.dumps(asdict(resume_profile(pf.sections["Resume"], "x")), default=str))
        truth_li = json.loads(json.dumps(asdict(linkedin_profile(pf.sections["LinkedIn"], "x")), default=str)) \
            if pf.sections.get("LinkedIn") else None
        for lay in [*layouts, "linkedin_export.pdf"]:
            f = STRESS / f"{pf.id}_{lay}"
            if not f.exists():
                continue
            data = f.read_bytes()
            text = normalize_document(f.name, data)
            lab = counting()
            if lay == "linkedin_export.pdf":
                res, truth, typ = parse_linkedin(data, text, vocab, lab), truth_li, "linkedin_pdf"
            else:
                res, truth, typ = parse_resume(f.name, data, text, vocab, lab), truth_resume, "resume"
            secs.append(res.report.seconds)
            asks.setdefault(lay, []).append(lab.counts.questions)
            if res.extract is None:
                tally[lay]["fallback"] += 1
                for r in res.report.reasons:
                    reasons[re.sub(r"'[^']*'", "'…'", r)] = reasons.get(re.sub(r"'[^']*'", "'…'", r), 0) + 1
                continue
            got, rep = validate_extraction(SourceDoc("x", typ, text, typ), res.extract)
            c = compare_profiles(truth, json.loads(json.dumps(asdict(got), default=str)))
            if _correct(c) and not rep.dropped:
                tally[lay]["correct"] += 1
            else:
                tally[lay]["wrong"] += 1
                miss = {k: v for k, v in c["missing"].items() if v}
                extra = {k: v for k, v in c["extra"].items() if v and k in ("role", "project", "certification")}
                wrong_notes.append(f"- {pf.id} {lay}: missing {miss or '—'}; extra {extra or '—'}; dates "
                                   f"{c['dates']['different'] or '—'}; quote check dropped {rep.dropped}")
    out = ["## 3. Layout stress set (synthetic only)", "",
           "Resume shadow parser on six layouts of each of the 16 profiles, and the LinkedIn parser on the 13 "
           "LinkedIn-style exports, against the structured parse of the same profile. *Correct* = nothing missing, "
           "dates identical, no extra role/project/certification, nothing dropped by the quote check. *Wrong* = it "
           "parsed (the gate passed) but differs: the gate should have fallen back.", "",
           "| layout | correct | fell back | wrong | Jev questions per document (median) |", "|---|---|---|---|---|"]
    for lay, t in tally.items():
        if sum(t.values()):
            out.append(f"| {lay} | {t['correct']} | {t['fallback']} | {t['wrong']} | "
                       f"{statistics.median(asks.get(lay, [0])):.0f} |")
    out += ["", "Fallback reasons: " + ("; ".join(f"{k} ×{v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1]))
                                         or "none"), ""]
    if wrong_notes:
        out += ["**Wrong (parsed but different)**", "", *wrong_notes, ""]
    out.append(f"Parser time per document: median {statistics.median(secs) * 1000:.0f} ms, max {max(secs) * 1000:.0f} ms.")
    return out, tally


# --- Main ---------------------------------------------------------------------------------------------------------

def latest_live_candidate(conn) -> str | None:
    row = conn.execute("SELECT c.id FROM candidates c JOIN runs r ON r.candidate_id = c.id WHERE c.data_source IS NULL "
                       "AND r.status = 'done' AND EXISTS (SELECT 1 FROM evidence_items e WHERE e.candidate_id = c.id "
                       "AND e.type = 'linkedin_pdf' AND e.status = 'validated') ORDER BY r.started_at DESC LIMIT 1"
                       ).fetchone()
    return row[0] if row else None


def find_uploads(conn) -> dict[str, Path]:
    """The original files of the saved reference: the upload whose normalized text equals the reference text."""
    out = {}
    paths = {r[0]: r[1] for r in conn.execute("SELECT DISTINCT file_sha256, file_path FROM evidence_items WHERE type IN "
                                              "('resume', 'linkedin_pdf') AND file_path IS NOT NULL")}
    seen = {}
    for folder in (ROOT / "uploads", ROOT / "data" / "demo-uploads"):     # uploads made with other databases too
        for f in sorted(folder.glob("*/*.pdf")):
            seen.setdefault(f.name, str(f))
    candidates = list(dict.fromkeys([*paths.values(), *seen.values()]))
    for doc_type in ("resume", "linkedin_pdf"):
        ref = json.loads((REF / f"{doc_type}.json").read_text())
        for fp in candidates:
            try:
                if Path(fp).exists() and normalize_document(fp, Path(fp).read_bytes()) == ref["text"]:
                    out[doc_type] = Path(fp)
                    break
            except Exception:  # noqa: BLE001 - an unreadable upload is simply not the reference
                continue
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    ap.add_argument("--live-candidate", help="candidate id of the live run to compare with (default: the latest)")
    ap.add_argument("--skip-profiles", action="store_true")
    args = ap.parse_args()
    work = Path(tempfile.mkdtemp(prefix="gates-")) / "gates.db"
    db_copy(Path(args.db), work)
    conn = db.connect(str(work))
    live = args.live_candidate or latest_live_candidate(conn)
    uploads = find_uploads(conn)
    sections = ["# Code-first extraction: acceptance gates (offline)", "",
                f"Generated {dt.datetime.now():%Y-%m-%d %H:%M}. No provider called. Jev threshold "
                f"{config.JEV_CONFIDENCE_THRESHOLD}, PARSER_MAX_UNCLEAR {config.PARSER_MAX_UNCLEAR}.", ""]
    ref, gates = reference_section(conn, live, uploads)
    sections += ref
    rd, _ = readme_section(conn, live)
    sections += rd
    conn.close()
    if not args.skip_profiles:
        pr, _ = profiles_section(str(work))
        sections += pr
    st, _ = stress_section()
    sections += st
    path = ROOT / "timings" / "parser_gates.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("\n".join(sections) + "\n")
    print("\n".join(sections))
    print(f"\nwritten to {path}")


if __name__ == "__main__":
    main()
