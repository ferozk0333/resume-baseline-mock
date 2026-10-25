"""Prompt Guard false-positive check (specs/18 Phase 4). Live (Groq), only after the go.

    python scripts/prompt_guard_check.py --estimate
    LIVE=1 python scripts/prompt_guard_check.py --run

Screens the 16 synthetic test profiles' documents (the rendered resume and LinkedIn PDFs as read, their READMEs and
portfolio text). The first call prints the raw
answer so the response format is confirmed. Flags go to timings/prompt_guard_check.json, never to a candidate or a
recruiter. Scores are cached by chunk text (prompt_guard_cache in the scratch copy).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("PRINT_TIMINGS", "0")

from core import config, db  # noqa: E402
from extraction import prompt_guard as pg  # noqa: E402

WORK = ROOT / "data" / "prompt-guard-check.db"
RENDERED = ROOT / "tests" / "fixtures" / "profiles" / "rendered"


def documents() -> list[tuple[str, str, str]]:
    """(owner, document, text)."""
    from extraction.structured import github_repos, read_profile_file
    from ingest.documents import normalize_document
    out = []
    for path in sorted((ROOT / "rightstaff-test-profiles" / "profiles").glob("*/*.md")):
        pf = read_profile_file(path)
        for kind in ("resume", "linkedin"):
            f = next((RENDERED / f"{pf.id}_{kind}{ext}" for ext in (".pdf", ".docx")
                      if (RENDERED / f"{pf.id}_{kind}{ext}").exists()), None)
            if f is not None:
                out.append((pf.id, kind, normalize_document(f.name, f.read_bytes())))
        if pf.sections.get("GitHub"):
            data = json.loads(re.sub(r"^```json\s*|\s*```$", "", pf.sections["GitHub"].strip()))
            for repo in github_repos(data):
                if repo.readme:
                    out.append((pf.id, f"README {repo.name}", repo.readme))
        if pf.sections.get("Portfolio"):
            out.append((pf.id, "portfolio", pf.sections["Portfolio"]))
    return out


def estimate() -> None:
    docs = documents()
    n = sum(len(pg.chunks(t)) for *_, t in docs)
    print(f"{len(docs)} documents, {n} chunks → at most {n} Groq calls to llama-prompt-guard-2-86m "
          f"(30 RPM, 14.4K RPD), fewer for repeated chunks; ≈ {n / 30:.0f} minutes at 30 RPM.")


def run() -> None:
    if not config.LIVE:
        sys.exit("Live: set LIVE=1, only after the go.")
    import requests
    if not WORK.exists():
        db.copy_db(ROOT / "data" / "demo.db", WORK)
    docs = documents()
    # The first call, raw: what the model returns.
    first = pg.chunks(docs[0][2])[0]
    resp = requests.post(config.GROQ_URL, json={"model": config.PROMPT_GUARD_MODELS[0].removeprefix("groq/"),
                                                "messages": [{"role": "user", "content": first}]},
                         headers={"Authorization": f"Bearer {config.GROQ_API_KEY}"}, timeout=15)
    raw = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text
    print("FIRST CALL", resp.status_code, json.dumps(raw)[:800])
    print("HEADERS", {k: v for k, v in resp.headers.items() if k.lower().startswith(("x-ratelimit", "retry-after"))})
    guard = pg.GroqGuard()
    report = {"first_call": {"status": resp.status_code, "body": raw}, "documents": [], "flags": []}
    for owner, doc, text in docs:
        flags = pg.screen(str(WORK), None, None, f"{owner} / {doc}", text, guard)
        conn = db.connect(str(WORK))
        scores = [conn.execute("SELECT score FROM prompt_guard_cache WHERE text_hash = ?",
                               (__import__("hashlib").sha256(c.encode()).hexdigest(),)).fetchone()
                  for c in pg.chunks(text)]
        conn.close()
        top = max((s[0] for s in scores if s), default=None)
        report["documents"].append({"owner": owner, "document": doc, "chunks": len(scores),
                                    "screened": sum(1 for s in scores if s), "max_score": top})
        for f in flags:
            report["flags"].append({"owner": owner, "document": doc, "chunk": f["chunk_index"], "score": f["score"],
                                    "excerpt": f["chunk"][:300]})
        print(f"{owner} / {doc}: max {top}, flagged {len(flags)}", flush=True)
    report["calls"] = guard.calls + 1
    (ROOT / "timings" / "prompt_guard_check.json").write_text(json.dumps(report, indent=1, default=str))
    print(f"\ncalls {report['calls']}, flags {len(report['flags'])}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--estimate", action="store_true")
    g.add_argument("--run", action="store_true")
    a = ap.parse_args()
    estimate() if a.estimate else run()
