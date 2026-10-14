"""Complexity labels for ~10 items of the reference resume and LinkedIn, by one model. Live: model calls.

    python scripts/compare_complexity.py --model gemini/gemini-3.5-flash-lite
    python scripts/compare_complexity.py --model gemini/gemini-3.8-flash
    python scripts/compare_complexity.py --diff gemini/gemini-3.5-flash-lite gemini/gemini-3.8-flash

Items: the roles and projects in tests/extraction_reference/ (resume, then LinkedIn), judged against the
AI Engineer baseline with the shared complexity question. Each model's labels are saved to
timings/complexity_<model>.json; --diff reports the labels that differ, with each model's quote and reasoning.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db  # noqa: E402
from core.llm import RoutedLLM  # noqa: E402
from judges.base import question, render_context  # noqa: E402
from judges.claude_judge import JUDGE_MAX_TOKENS, output_model, system_prompt  # noqa: E402

REF = ROOT / "tests" / "extraction_reference"
BASELINE = "baseline-ai-engineer-entry"


def items() -> list[tuple[str, str]]:
    out = []
    for doc in ("resume", "linkedin_pdf"):
        p = json.loads((REF / f"{doc}.json").read_text())["profile"]
        for r in p["roles"]:
            head = f"{r['title']} at {r['employer']}" if r.get("employer") else r["title"]
            out.append((f"{doc}: {head}", "\n".join([head, *[f"- {b['text']}" for b in r.get("bullets", [])]])))
        for pr in p["projects"]:
            lines = [pr["name"] + (f": {pr['description']}" if pr.get("description") else "")]
            out.append((f"{doc}: {pr['name']}", "\n".join([*lines, *[f"- {b['text']}" for b in pr.get("bullets", [])]])))
    return out[:10]


def run(model: str) -> None:
    b = db.get_baseline(db.connect(), BASELINE)
    rubric = f"Target role: {b.title} ({b.level}). Desired complexity: {b.complexity_criteria or ''}"
    q = question("complexity")
    llm = RoutedLLM(max_tokens=JUDGE_MAX_TOKENS)
    results = []
    for name, text in items():
        out = llm.parse(model=model, system=system_prompt(q), user=render_context({"baseline": rubric, "item": text}),
                        output_type=output_model("complexity"))
        results.append({"item": name, "label": out.label, "quote": out.quote, "reasoning": out.reasoning})
        print(f"{out.label:8s} {name}", flush=True)
    path = ROOT / "timings" / f"complexity_{model.replace('/', '_')}.json"
    path.write_text(json.dumps(results, indent=1))
    print(f"saved {path}")


def diff(a: str, b: str) -> None:
    load = lambda m: {r["item"]: r for r in json.loads(  # noqa: E731
        (ROOT / "timings" / f"complexity_{m.replace('/', '_')}.json").read_text())}
    ra, rb = load(a), load(b)
    same = [i for i in ra if i in rb and ra[i]["label"] == rb[i]["label"]]
    print(f"{len(same)} of {len(ra)} labels agree")
    for i in ra:
        if i in rb and ra[i]["label"] != rb[i]["label"]:
            print(f"\n{i}\n  {a}: {ra[i]['label']} · “{ra[i]['quote']}” · {ra[i]['reasoning']}"
                  f"\n  {b}: {rb[i]['label']} · “{rb[i]['quote']}” · {rb[i]['reasoning']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--diff", nargs=2)
    args = ap.parse_args()
    diff(*args.diff) if args.diff else run(args.model)
