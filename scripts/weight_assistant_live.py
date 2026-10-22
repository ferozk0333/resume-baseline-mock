"""Spec 21 §7: the weight assistant on the 10 sample descriptions (tests/fixtures/weight_texts.yaml).

    python scripts/weight_assistant_live.py                     # offline: the hand-written stand-in answers
    LIVE=1 LLM_PROVIDER=gemini python scripts/weight_assistant_live.py --live    # only after the owner's go

Runs on a copy of data/demo.db (data/weight-live.db) against the AI Engineer demo job: 10 WEIGHTS_MODEL calls at most
(one per text; the plan route, free tier; a retry only on an invalid answer). Nothing is applied. For each text:
the validated weights, the reasons, the ignored phrases, the ranking preview and whether the expected direction
held. Writes timings/weight_assistant_live.md and .json (timings/ is not in git).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

import yaml  # noqa: E402

from core import config, db  # noqa: E402

WORK = ROOT / "data" / "weight-live.db"
OUT = ROOT / "timings" / "weight_assistant_live"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--job", default="AI Engineer")
    a = ap.parse_args()
    from backend import recruiter as rc
    from backend import weight_assistant as wa
    from parser_gates import db_copy
    from tests.test_weight_assistant import check_direction
    if a.live:
        if not config.LIVE:
            sys.exit("Live: set LIVE=1 LLM_PROVIDER=gemini, and only after the go.")
        from core.llm import RoutedLLM
        llm, mode = RoutedLLM(), "live"
    else:
        from tests.ui_fakes import StubWeightsLLM
        llm, mode = StubWeightsLLM(), "stubs"
    db_copy(ROOT / "data" / "demo.db", WORK)
    conn = db.connect(str(WORK))
    job = next(rc.get_job(conn, j["id"]) for j in rc.list_jobs(conn) if j["title"] == a.job)
    texts = yaml.safe_load((ROOT / "tests" / "fixtures" / "weight_texts.yaml").read_text())["texts"]
    results, lines = [], [f"# Weight assistant sample set ({mode}) — job: {job['title']}", ""]
    for t in texts:
        res = wa.suggest(conn, llm, job, t["text"], fresh=True)
        if res["status"] != "ok":
            results.append({"id": t["id"], "status": res["status"], "message": res["message"]})
            lines += [f"## {t['id']}: {res['message']}", ""]
            continue
        v = wa.view(conn, job, wa.get(conn, job["id"], res["id"]))
        problems = check_direction(t, job["weights"], v["weights"], v["ignored"])
        results.append({"id": t["id"], "model": wa.get(conn, job["id"], res["id"])["model"], "summary": v["summary"],
                        "changes": v["changes"], "ignored": v["ignored"], "preview": v["preview"],
                        "expected": "as expected" if not problems else problems})
        lines += [f"## {t['id']}", f"> {t['text']}", "", f"Summary: {v['summary']}",
                  f"Ignored: {', '.join(v['ignored']) or 'none'}",
                  f"Expected direction: {'held' if not problems else '; '.join(problems)}", "",
                  "| weight | current → suggested | reason |", "|---|---|---|"]
        lines += [f"| {c['label']} | {c['current']:g} → {c['suggested']:g} | {c['reason']} |" for c in v["changes"]]
        p = v["preview"]
        lines += ["", (f"Preview: ↑{p['up']} ↓{p['down']} ={p['same']} of {p['total']}; top 5: " + ", ".join(
            f"{r['rank']}. {r['name']} ({'+' if r['move'] > 0 else ''}{r['move']})" for r in p["top"][:5]))
                  if p["available"] else f"Preview: {p['message']}", ""]
        print(f"{t['id']}: {'ok' if not problems else problems}", flush=True)
    conn.close()
    OUT.parent.mkdir(exist_ok=True)
    OUT.with_suffix(".md").write_text("\n".join(lines))
    OUT.with_suffix(".json").write_text(json.dumps(results, indent=1, default=str))
    print(f"\n{sum(1 for r in results if r.get('expected') == 'as expected')}/{len(results)} as expected → "
          f"{OUT.with_suffix('.md')}")


if __name__ == "__main__":
    main()
