"""PARSE_README gate on the candidate's saved profile (Phase 3 review item 3). No document upload.

    python scripts/readme_live_gate.py --estimate            # offline: the calls a live run would make
    LIVE=1 python scripts/readme_live_gate.py --run          # live: only after the go

The profile is rebuilt from what was saved for each source of the latest live run (resume, LinkedIn and each
repository's LLM README extraction, plus the form entries) on a scratch copy of the database. Control: rebuilt as
saved, must reproduce the current scores. Parser: each README read by the README parser instead (Jev labels the
unclear blocks and confirms vocabulary names). Judging reuses every saved answer for the same input (preferring this
candidate's own); only items whose text changed are judged again, through the normal judge (Jev first; complexity and
quote-needing asks on route judge_single, Flash-Lite first). Jev answers for parsing are recorded to
data/readme_gate_jev.json, so the parse can be replayed offline afterwards.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

from core import config, db  # noqa: E402
from core.text import normalize_name  # noqa: E402
from extraction import store  # noqa: E402
from extraction.deletions import record  # noqa: E402
from extraction.parse.claims import vocabulary  # noqa: E402
from extraction.parse.labeller import FakeLabeller, JevLabeller, RecordedLabeller  # noqa: E402
from extraction.parse.readme import parse_readme, repo_from_text  # noqa: E402
from extraction.records import Profile, new_id  # noqa: E402
from extraction.validate import SourceDoc, merge_profiles, repo_profile  # noqa: E402

WORK = ROOT / "data" / "readme-gate.db"
JEV_RECORD = ROOT / "data" / "readme_gate_jev.json"
ROLES = {"AI Engineer (role baseline)": "baseline-ai-engineer-entry",
         "Associate Product Manager, Payments (demo job)": "baseline-demo-larkspur-apm-payments"}


def profile_from_json(d: dict) -> Profile:
    p = Profile()
    for table, kind in (("roles", "role"), ("projects", "project"), ("claims", "claim"),
                        ("certifications", "certification")):
        setattr(p, table, [record(kind, json.dumps(x)) for x in d.get(table, [])])
    for x, rec in zip(d.get("claims", []), p.claims):
        rec.bullet_id = x.get("bullet_id")
    for x, rec in zip(d.get("projects", []), p.projects):
        rec.role_id = x.get("role_id")
    return p


def fresh(p: Profile, evidence: dict[str, str]) -> None:
    """New ids for the copy; links between items and evidence ids follow."""
    remap = {}
    for x in [*p.roles, *p.projects, *p.all_bullets(), *p.claims, *p.certifications, *p.references]:
        remap[x.id] = x.id = new_id()
    for x in [*p.roles, *p.projects, *p.all_bullets(), *p.claims, *p.certifications]:
        x.evidence_id = evidence.get(x.evidence_id, x.evidence_id)
    for pr in p.projects:
        pr.role_id = remap.get(pr.role_id, pr.role_id)
    for c in p.claims:
        c.bullet_id = remap.get(c.bullet_id, c.bullet_id)


def db_copy(src: Path, dst: Path) -> None:
    from parser_gates import db_copy as copy_db
    copy_db(src, dst)


def latest_live(conn) -> str:
    """The latest real profile with a finished run for every gate role (the profile the review names)."""
    q = " AND ".join(f"EXISTS (SELECT 1 FROM runs r WHERE r.candidate_id = c.id AND r.baseline_id = '{b}' "
                     "AND r.status = 'done')" for b in ROLES.values())
    row = conn.execute(f"SELECT c.id FROM candidates c WHERE c.data_source IS NULL AND {q} "
                       "ORDER BY c.created_at DESC LIMIT 1").fetchone()
    if row is None:
        sys.exit("No profile with finished runs for every gate role.")
    return row[0]


def sources(conn, cid: str, readme=None):
    """(forms, per-source profiles, evidence rows, readme results) in the usual merge order."""
    ev = store.list_evidence(conn, cid)
    saved = store.load_profile(conn, cid)
    form_eids = {e["id"] for e in ev if e["type"] in ("certification", "reference")}
    forms = Profile(references=copy.deepcopy(saved.references),
                    certifications=[copy.deepcopy(c) for c in saved.certifications if c.evidence_id in form_eids])
    profiles, parsed = [], {}
    for e in ev:
        if e["status"] != "validated" or e["type"] in ("certification", "reference"):
            continue
        if e["type"] == "github_repo":
            repo = repo_from_text(e["raw_text"])
            doc = SourceDoc(e["id"], "github_repo", e["raw_text"], repo.name, repo=repo)
            res = readme(repo) if readme is not None and repo.readme and repo.counted else None
            if res is not None:
                parsed[repo.name] = res
            if res is not None and res.extract is not None:
                profiles.append(repo_profile(doc, res.extract, method_of=res.method_of)[0])
                continue
        if e["extracted_json"]:
            profiles.append(profile_from_json(json.loads(e["extracted_json"])))
    return forms, profiles, ev, parsed


def build(conn, name: str, cid_from: str, readme=None):
    forms, profiles, ev, parsed = sources(conn, cid_from, readme)
    cid = store.create_candidate(conn, name, None, None, None)
    with conn:
        conn.execute("UPDATE candidates SET data_source = 'test_variant' WHERE id = ?", (cid,))
    mapping = {}
    for e in ev:
        mapping[e["id"]] = store.add_evidence(conn, cid, e["type"], e["source"], raw_text=e["raw_text"],
                                              file_path=e["file_path"], file_sha256=e["file_sha256"],
                                              status=e["status"], error=e["error"])
        with conn:                                    # Consistency reads each source's own extraction
            conn.execute("UPDATE evidence_items SET extracted_json = ?, items_extracted = ?, items_dropped = ? "
                         "WHERE id = ?", (e["extracted_json"], e["items_extracted"], e["items_dropped"],
                                          mapping[e["id"]]))
    merged, _ = merge_profiles([forms, *profiles])
    fresh(merged, mapping)
    store.save_profile(conn, cid, merged)
    return cid, merged, parsed


def run_date(conn, run_id: str) -> dt.date:
    return dt.date.fromisoformat(conn.execute("SELECT started_at FROM runs WHERE id = ?", (run_id,)).fetchone()[0][:10])


def current_runs(conn, cid: str) -> dict[str, str]:
    out = {}
    for label, bid in ROLES.items():
        row = conn.execute("SELECT id FROM runs WHERE candidate_id = ? AND baseline_id = ? AND status = 'done' "
                           "ORDER BY started_at DESC LIMIT 1", (cid, bid)).fetchone()
        if row:
            out[label] = row[0]
    return out


def score(path: str, cid: str, baseline: str, today: dt.date, judge):
    from graph.context import Deps
    from graph.pipeline import Pipeline
    return Pipeline(Deps(path, judge, planner=None, today=today), print_timings=False).score(cid, baseline, plan=False)


def labeller_for(scenario: str):
    """Offline stand-ins for Jev, to bound the estimate: 'max' keeps every unclear block (as results: they become
    bullets, the most judging) and confirms every name; 'min' answers nothing (the offline parse)."""
    if scenario == "max":
        return FakeLabeller(lambda a: ("result", 0.99) if a.kind == "choice" else ("yes", 0.99))
    return FakeLabeller(lambda a: None)


def estimate() -> None:
    from judges.replay_judge import ReplayJudge
    WORK.parent.mkdir(exist_ok=True)
    db_copy(ROOT / "data" / "demo.db", WORK)
    conn = db.connect(str(WORK))
    orig = latest_live(conn)
    runs = current_runs(conn, orig)
    vocab = vocabulary(conn)
    jev = conn.execute("SELECT SUM(used = 0), COUNT(*) FROM judgments WHERE candidate_id = ? AND method = 'jev'",
                       (orig,)).fetchone()
    share = (jev[0] or 0) / max(1, jev[1])
    print(f"Candidate {orig[:8]}…; current runs: { {k: v[:8] for k, v in runs.items()} }")
    print(f"Jev answers below the threshold in that run: {share:.0%} (they go to route judge_single).\n")
    cid0, _, _ = build(conn, "readme gate control", orig)
    for label, run_id in runs.items():
        from parser_gates import final_of
        rec = final_of(conn, run_id)[0].final
        j = ReplayJudge(conn, (orig,))
        res = score(str(WORK), cid0, ROLES[label], run_date(conn, run_id), j)
        print(f"Control {label}: current {rec:.2f}, rebuilt {res.get('final') or 0:.2f}, unrecorded "
              f"{len(j.misses) + len(j.route_misses)}")
    for scenario in ("min", "max"):
        lab = labeller_for(scenario)
        counting = {"requests": 0, "questions": 0}

        def readme(repo, lab=lab):
            r0, q0 = lab.counts.requests, lab.counts.questions
            out = parse_readme(repo, vocab, lab)
            counting["requests"] += lab.counts.requests - r0
            counting["questions"] += lab.counts.questions - q0
            return out
        cid, merged, parsed = build(conn, f"readme gate {scenario}", orig, readme)
        bullets = sum(len(r.extract.projects[0].bullets) for r in parsed.values() if r.extract is not None)
        ok = sum(1 for r in parsed.values() if r.extract is not None)
        print(f"\nScenario {scenario}: {ok} of {len(parsed)} READMEs parsed, {bullets} bullets from result blocks; Jev for "
              f"parsing: {counting['requests']} request(s), {counting['questions']} question(s)")
        union: set = set()
        for label, run_id in runs.items():
            j = ReplayJudge(conn, (orig,))
            score(str(WORK), cid, ROLES[label], run_date(conn, run_id), j)
            union |= j.missed_inputs
            print(f"  {label} alone: {len(j.missed_inputs)} new judgment(s)")
        jev_q = Counter(k for k, _, r in union if not r and k != "complexity")
        route = Counter(k for k, _, r in union if r or k == "complexity")
        n_jev = sum(jev_q.values())
        print(f"  Both roles (answers shared across roles counted once): {n_jev} Jev judgment question(s) "
              f"{dict(sorted(jev_q.items()))}")
        print(f"  route judge_single, Flash-Lite first: {sum(route.values())} {dict(sorted(route.items()))} "
              f"+ expected Jev→route fallbacks ≈ {round(n_jev * share)}")
    conn.close()


class Hybrid:
    """Saved answer for the same input first (this candidate's own preferred), else the live judge."""

    def __init__(self, replay, live):
        self.replay, self.live, self.asked = replay, live, Counter()
        self.fallback = _HybridRoute(self)
        self.CLAUDE_ONLY = getattr(live, "CLAUDE_ONLY", set())

    def group(self, keys, context):
        """Several questions about one item: saved answers first, the rest in one live (Jev) request."""
        from judges.base import JudgeError
        out, rest = {}, []
        for k in keys:
            try:
                out[k] = self.replay._get(k, context)
            except JudgeError:
                rest.append(k)
        if rest:
            self.asked.update(rest)
            out.update(self.live.group(rest, context))
        return out

    def models_for(self, key):
        return sorted(set(self.replay.models_for(key)) | set(self.live.models_for(key)))

    def _ask(self, key, context, ask_live):
        from judges.base import JudgeError
        try:
            return self.replay._get(key, context)
        except JudgeError:
            self.asked[key] += 1
            return ask_live()

    def yes_no(self, key, context):
        return self._ask(key, context, lambda: self.live.yes_no(key, context))

    def choice(self, key, options, context):
        return self._ask(key, context, lambda: self.live.choice(key, options, context))


class _HybridRoute:
    def __init__(self, h):
        self.h = h

    def models_for(self, key):
        return self.h.models_for(key)

    def yes_no(self, key, context):
        return self.h._ask(key, context, lambda: self.h.live.fallback.yes_no(key, context))

    def choice(self, key, options, context):
        return self.h._ask(key, context, lambda: self.h.live.fallback.choice(key, options, context))


def run() -> None:
    if not config.LIVE:
        sys.exit("Live run: set LIVE=1 (and only after the go).")
    import time
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.replay_judge import ReplayJudge
    from judges.service import get_judge
    from parser_gates import final_of
    from extraction.validate import validate_extraction  # noqa: F401
    started = time.time()
    db_copy(ROOT / "data" / "demo.db", WORK)
    conn = db.connect(str(WORK))
    orig = latest_live(conn)
    runs = current_runs(conn, orig)
    vocab = vocabulary(conn)
    lab = RecordedLabeller(JEV_RECORD, JevLabeller())
    t0 = time.time()
    cid, merged, parsed = build(conn, "readme gate live", orig, lambda repo: parse_readme(repo, vocab, lab))
    parse_seconds = time.time() - t0
    jev_parse = {"requests": lab.live_labeller.counts.requests, "questions": lab.live_labeller.counts.questions,
                 "unanswered": lab.counts.unanswered}
    rep = {"candidate": orig, "parse_seconds": round(parse_seconds, 1), "jev_parse": jev_parse, "readmes": {},
           "roles": {}}
    llm = {}
    for e in store.list_evidence(conn, orig):
        if e["type"] == "github_repo" and e["extracted_json"]:
            llm[repo_from_text(e["raw_text"]).name] = json.loads(e["extracted_json"])
    for name, r in parsed.items():
        old = llm.get(name, {"projects": [], "claims": []})
        rep["readmes"][name] = {
            "outcome": r.report.outcome, "reasons": r.report.reasons, "notes": r.report.notes,
            "parser_claims": [c.name for c in r.extract.claims] if r.extract else None,
            "llm_claims": [c["name_raw"] for c in old.get("claims", [])],
            "parser_bullets": [b.text for b in r.extract.projects[0].bullets] if r.extract else None,
            "llm_bullets": [b["text"] for p in old.get("projects", []) for b in p.get("bullets", [])]}
    judge = None
    for label, run_id in runs.items():
        judge = Hybrid(ReplayJudge(conn, (orig,)), judge.live if judge else get_judge())
        pipe = Pipeline(Deps(str(WORK), judge, planner=None, today=run_date(conn, run_id)), print_timings=False)
        t1 = time.time()
        res = pipe.score(cid, ROLES[label], plan=False)
        seconds = time.time() - t1
        summary = next(iter(pipe.recorders.values())).summary() if pipe.recorders else {}
        entry = {"seconds": round(seconds, 1), "asked": dict(judge.asked), "paused": res.get("paused"),
                 "errors": len(res.get("errors") or []), "calls_by_task": summary.get("calls_by_task"),
                 "model_calls": summary.get("model_calls"), "failovers": summary.get("failovers"),
                 "rate_limits": summary.get("rate_limits")}
        if res.get("run_id"):
            before, s0 = final_of(conn, run_id)
            after, s1 = final_of(conn, res["run_id"])
            entry.update(current=before.final, parser=after.final, run_id=res["run_id"],
                         proof_density=[s0["proof_density"]["score"], s1["proof_density"]["score"]],
                         subcategories={k: [s0[k]["status"], s0[k]["score"], s1[k]["status"], s1[k]["score"]]
                                        for k in s0 if (s0[k]["score"], s0[k]["status"]) != (s1[k]["score"], s1[k]["status"])})
        rep["roles"][label] = entry
        judge.asked = Counter()
        if res.get("paused"):
            rep["stopped"] = res["paused"]
            break
    rows = conn.execute("SELECT method, model_version, used, COUNT(*) FROM judgments WHERE candidate_id = ? "
                        "AND model_version != 'rule-v0.1' GROUP BY 1, 2, 3", (cid,)).fetchall()
    rep["judgments_saved"] = [list(r) for r in rows]
    rep["jev_to_route_fallbacks"] = conn.execute(
        "SELECT COUNT(*) FROM judgments WHERE candidate_id = ? AND method = 'jev' AND used = 0", (cid,)).fetchone()[0]
    rep["total_seconds"] = round(time.time() - started, 1)
    out = ROOT / "timings" / "readme_live_gate.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps({k: v for k, v in rep.items() if k != "readmes"}, indent=1, default=str))
    print(f"\nwritten to {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estimate", action="store_true")
    g.add_argument("--run", action="store_true")
    args = ap.parse_args()
    estimate() if args.estimate else run()


if __name__ == "__main__":
    main()
