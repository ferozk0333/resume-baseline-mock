"""Time a full first run with the real models (specs/09 §1). Live: costs API calls.

    python scripts/time_run.py --resume R.pdf [--linkedin L.pdf] [--github USER] \
        [--baseline baseline-ai-engineer-entry] [--label before] [--out DIR]

Uses a fresh SQLite file and upload folder under --out, so nothing is cached and the
app's own database is not touched: every judgment is a cold call, as for a new candidate.
The review step is confirmed automatically. Writes <label>.json (timing summary, scores)
and <label>.txt (the table) to --out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resume", required=True)
    ap.add_argument("--linkedin")
    ap.add_argument("--github")
    ap.add_argument("--baseline", default="baseline-ai-engineer-entry")
    ap.add_argument("--label", default="run")
    ap.add_argument("--out", default=str(ROOT / "timings"))
    ap.add_argument("--github-fixture", help="replay GitHub responses from this file; record it first if missing")
    args = ap.parse_args()

    out = Path(args.out) / args.label
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "timing.db"
    for p in out.glob("timing.db*"):
        p.unlink()
    os.environ["RIGHTSTAFF_DB"] = str(db_path)
    os.environ["RIGHTSTAFF_UPLOADS"] = str(out / "uploads")
    sys.path.insert(0, str(ROOT))

    from core import config, db, timing
    from core.llm import ClaudeLLM
    from extraction.onboard import OnboardingInputs, Upload
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from ingest.github import GitHubClient
    from ingest.http_cache import RecordingSession, ReplaySession
    from judges.service import get_judge

    db.init_app_db(str(db_path)).close()
    up = lambda p: Upload(Path(p).name, Path(p).read_bytes()) if p else None  # noqa: E731
    inputs = OnboardingInputs(resume=up(args.resume), linkedin_pdf=up(args.linkedin), github=args.github,
                              name="Timing run", target_role=args.baseline)
    recording = None
    github_client = None
    if args.github and args.github_fixture:
        if Path(args.github_fixture).exists():
            github_client = GitHubClient(session=ReplaySession(args.github_fixture))   # recorded latency kept
        else:
            recording = RecordingSession()
            github_client = GitHubClient(session=recording)
    pipe = Pipeline(Deps(str(db_path), get_judge(), llm=ClaudeLLM(), github_client=github_client,
                         planner=ClaudeLLM(max_tokens=config.PLAN_MAX_TOKENS)), print_timings=False)

    t0 = time.perf_counter()
    first = pipe.start_onboarding(inputs, args.baseline)
    t1 = time.perf_counter()
    if recording is not None:
        recording.save(args.github_fixture)
        print(f"recorded {len(recording.records)} GitHub responses to {args.github_fixture}")
    if first.get("warnings"):
        print("warnings:", *first["warnings"], sep="\n  ")
    if not first.get("awaiting_review"):
        sys.exit(f"onboarding did not reach review: blocked={first.get('blocked')!r}")
    res = pipe.confirm_review(first["thread_id"])
    t2 = time.perf_counter()

    summary = pipe.recorders[first["thread_id"]].summary()
    summary["measured"] = {"upload_to_review": round(t1 - t0, 2), "confirm_to_end": round(t2 - t1, 2)}
    result = {"label": args.label, "baseline": args.baseline, "final": res.get("final"),
              "dimensions": res.get("dimension_scores"), "errors": res.get("errors"),
              "plan_error": res.get("plan_error"), "warnings": first.get("warnings"),
              "extraction": [dict(r) for r in db.connect(str(db_path)).execute(
                  "SELECT type, status, error, items_extracted, items_dropped FROM evidence_items")],
              "subcategories": {k: {"status": v["status"], "score": v["score"]}
                                for k, v in (res.get("subcategory_scores") or {}).items()},
              "timings": summary}
    text = timing.table(summary) + (f"\n\nfinal {res.get('final'):.1f}  errors {len(res.get('errors') or [])}"
                                    if res.get("final") is not None else "\n\nno score")
    (out / f"{args.label}.json").write_text(json.dumps(result, indent=1, default=str))
    (out / f"{args.label}.txt").write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
