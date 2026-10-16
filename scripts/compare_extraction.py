"""Compare a provider's extraction with the saved reference (tests/extraction_reference/). Live: model calls.

    python scripts/compare_extraction.py --provider gemini [--model gemini/gemini-3.8-flash] [--docs resume,linkedin_pdf]

Runs the shared extraction prompt on the exact normalized text stored in the reference, validates the
result with the same code, and reports: item counts, missing and extra items, quote-check drops,
claim-kind differences, and Proof density against the reference.

Proof density here is a structural estimate computed in code, identically for both sides: a claim
mentioned in an artifact (portfolio/presentation/publication project or certificate) counts as artifact,
one mentioned only in a bullet as bullet, otherwise claimed only. It is the best label the judge could
give; the judge itself is not called. Writes the report to timings/compare_<provider>.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, timing  # noqa: E402
from core.llm import RoutedLLM  # noqa: E402
from core.text import contains_phrase  # noqa: E402
from extraction.extract import extract_document  # noqa: E402
from extraction.validate import SourceDoc, validate_extraction  # noqa: E402
from graph.nodes.trust import ARTIFACT_KINDS  # noqa: E402
from scoring import formulas as f  # noqa: E402

REF_DIR = ROOT / "tests" / "extraction_reference"
DEFAULT_MODELS = {"gemini": f"gemini/{config.GEMINI_MODELS['extract']}", "claude": "claude-haiku-4-5-20251001"}


def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").strip().lower())


def items(p: dict) -> dict[str, dict[str, str]]:
    """Comparable keys per item type (key → display text)."""
    out: dict[str, dict[str, str]] = {k: {} for k in ("role", "bullet", "project", "claim", "certification")}
    for r in p.get("roles", []):
        out["role"][norm(f"{r['title']} @ {r.get('employer')}")] = f"{r['title']} @ {r.get('employer')}"
        out["bullet"].update({norm(b["text"]): b["text"] for b in r.get("bullets", [])})
    for pr in p.get("projects", []):
        out["project"][norm(pr["name"])] = f"{pr.get('kind')}: {pr['name']}"
        out["bullet"].update({norm(b["text"]): b["text"] for b in pr.get("bullets", [])})
    for c in p.get("claims", []):
        out["claim"][norm(c.get("name_raw"))] = f"{c['kind']}: {c.get('name_raw')}"
    for c in p.get("certifications", []):
        out["certification"][norm(c["name"])] = c["name"]
    return out


def kinds(p: dict) -> dict[str, str]:
    return {norm(c.get("name_raw")): c["kind"] for c in p.get("claims", [])}


def structural_proof_density(p: dict) -> tuple[float | None, dict[str, int]]:
    def text(pr):
        return " ".join([pr["name"], pr.get("description") or "", *[b["text"] for b in pr.get("bullets", [])]])
    artifacts = [text(pr) for pr in p.get("projects", []) if pr.get("kind") in ARTIFACT_KINDS]
    artifacts += [c["name"] + (f" ({c['issuer']})" if c.get("issuer") else "") for c in p.get("certifications", [])]
    bullets = [b["text"] for item in [*p.get("roles", []), *p.get("projects", [])] for b in item.get("bullets", [])]
    labelled, counts = [], {"artifact": 0, "bullet": 0, "claimed_only": 0}
    for c in p.get("claims", []):
        if c["kind"] not in ("skill", "tool"):
            continue
        names = {c.get("name_raw") or "", c.get("name_norm") or ""} - {""}
        label = ("artifact" if any(contains_phrase(a, n) for a in artifacts for n in names)
                 else "bullet" if any(contains_phrase(b, n) for b in bullets for n in names) else "claimed_only")
        counts[label] += 1
        labelled.append((c.get("name_norm") or norm(c.get("name_raw")), label))
    score = f.proof_density(labelled)
    return score.score, counts


def compare(doc_type: str, provider: str, model: str) -> str:
    ref = json.loads((REF_DIR / f"{doc_type}.json").read_text())
    doc = SourceDoc("ref", doc_type, ref["text"], doc_type)
    rec = timing.Recorder()
    start = time.time()
    with timing.recording(rec):
        out = extract_document(doc, RoutedLLM(), model=model)
    seconds = time.time() - start
    profile, report = validate_extraction(doc, out)
    got = json.loads(json.dumps(asdict(profile), default=str))
    tokens_out = sum(e.get("output_tokens") or 0 for e in rec.of("model"))

    lines = [f"## {doc_type}", "",
             f"Reference: {ref['provider']} `{ref['model']}` (prompt @ {ref['prompt_commit']}), kept {ref['items_kept']}, "
             f"dropped {ref['items_dropped']}.  ",
             f"Now: {provider} `{model}` (current shared prompt), kept {report.kept}, dropped {report.dropped}; "
             f"{seconds:.0f} s, {tokens_out} output tokens.", "",
             "| item | reference | now | missing | extra |", "|---|---|---|---|---|"]
    a, b = items(ref["profile"]), items(got)
    for k in a:
        lines.append(f"| {k} | {len(a[k])} | {len(b[k])} | {len(set(a[k]) - set(b[k]))} | {len(set(b[k]) - set(a[k]))} |")
    for k in a:
        missing, extra = sorted(set(a[k]) - set(b[k])), sorted(set(b[k]) - set(a[k]))
        if missing or extra:
            lines += ["", f"**{k}**"]
            lines += [f"- missing: {a[k][x]}" for x in missing] + [f"- extra: {b[k][x]}" for x in extra]
    if report.dropped:
        lines += ["", "**Dropped by the quote check**"] + [f"- {d.item_type}: {d.text}" for d in report.dropped_items]
    ka, kb = kinds(ref["profile"]), kinds(got)
    changed = sorted((n, ka[n], kb[n]) for n in set(ka) & set(kb) if ka[n] != kb[n])
    lines += ["", f"**Claim kind differences** ({len(changed)})"] + [f"- {n}: {x} → {y}" for n, x, y in changed]
    pa, ca = structural_proof_density(ref["profile"])
    pb, cb = structural_proof_density(got)
    fmt = lambda v: "—" if v is None else f"{v:.3f}"  # noqa: E731
    lines += ["", f"**Proof density (structural estimate)**: reference {fmt(pa)} {ca} → now {fmt(pb)} {cb}"
              + (f", difference {pb - pa:+.3f}" if pa is not None and pb is not None else "")]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True, choices=["claude", "gemini", "groq"])
    ap.add_argument("--model", help="model id with its prefix, e.g. gemini/gemini-3.8-flash")
    ap.add_argument("--docs", default="resume,linkedin_pdf")
    args = ap.parse_args()
    model = args.model or DEFAULT_MODELS.get(args.provider)
    if not model:
        sys.exit("--model is required for this provider")
    sections = [f"# Extraction vs reference: {args.provider} `{model}`", ""]
    for d in args.docs.split(","):
        sections.append(compare(d.strip(), args.provider, model))
        sections.append("")
        print(sections[-2], flush=True)
    out = ROOT / "timings" / f"compare_{args.provider}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(sections))
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
