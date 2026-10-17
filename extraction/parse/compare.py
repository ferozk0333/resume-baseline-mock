"""Compare two validated profiles of the same document (reference vs parser): items found, missed and extra,
dates, and claims. Shared by the shadow log and the gate reports (scripts/parser_gates.py)."""
from __future__ import annotations

import re

from core.text import normalize_name

KINDS = ("role", "bullet", "project", "claim", "certification")


def _norm(s: str | None) -> str:
    return normalize_name((s or "").replace("’", "'"))


def items(p: dict) -> dict[str, dict[str, dict]]:
    """Comparable key → item, per item type."""
    out: dict[str, dict[str, dict]] = {k: {} for k in KINDS}
    for r in p.get("roles", []):
        out["role"][_norm(r["title"]) + " @ " + _norm(r.get("employer"))] = r
        for b in r.get("bullets", []):
            out["bullet"][_norm(b["text"])] = b
    for pr in p.get("projects", []):
        out["project"][_norm(pr["name"])] = pr
        for b in pr.get("bullets", []):
            out["bullet"][_norm(b["text"])] = b
    for c in p.get("claims", []):
        out["claim"][_norm(c.get("name_raw"))] = c
    for c in p.get("certifications", []):
        out["certification"][_norm(c["name"])] = c
    return out


def _words(s: str) -> set[str]:
    return set(s.split())


def _close(a: str, b: str, kind: str) -> bool:
    """Same item worded slightly differently: claims "AWS SageMaker" ~ "SageMaker" (one name inside the other);
    bullets and roles that share at least 90% of their words."""
    if a == b:
        return True
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return False
    if kind == "claim":
        return f" {a} " in f" {b} " or f" {b} " in f" {a} "
    return len(wa & wb) / max(len(wa), len(wb)) >= 0.9


def _match(ref: dict, got: dict, kind: str) -> tuple[dict[str, str], list[str], list[str]]:
    pairs, used = {}, set()
    for k in ref:
        if k in got:
            pairs[k] = k
            used.add(k)
    for k in ref:
        if k in pairs:
            continue
        hit = next((g for g in got if g not in used and _close(k, g, kind)), None)
        if hit is not None:
            pairs[k] = hit
            used.add(hit)
    return pairs, [k for k in ref if k not in pairs], [g for g in got if g not in used]


def _precision(d: str | None) -> int:
    return 0 if not d else 2 if re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", str(d)) else 1


def compare_profiles(ref: dict, got: dict) -> dict:
    a, b = items(ref), items(got)
    out = {"counts": {}, "missing": {}, "extra": {}, "exact": {}}
    dates = {"compared": 0, "identical": 0, "more_precise": 0, "different": []}
    for kind in KINDS:
        pairs, missing, extra = _match(a[kind], b[kind], kind)
        out["counts"][kind] = {"reference": len(a[kind]), "parser": len(b[kind]), "found": len(pairs),
                               "missing": len(missing), "extra": len(extra)}
        out["exact"][kind] = sum(1 for k, v in pairs.items() if k == v)
        out["missing"][kind] = [k for k in missing]
        out["extra"][kind] = [k for k in extra]
        if kind == "role":
            for k, g in pairs.items():
                for f in ("start_date", "end_date"):
                    x, y = a[kind][k].get(f), b[kind][g].get(f)
                    dates["compared"] += 1
                    if str(x) == str(y) and bool(a[kind][k].get("is_current")) == bool(b[kind][g].get("is_current")):
                        dates["identical"] += 1
                    elif x and y and str(y).startswith(str(x)[:4]) and _precision(y) > _precision(x):
                        dates["more_precise"] += 1
                    else:
                        dates["different"].append(f"{k}: {f} {x} → {y}")
    out["dates"] = dates
    kinds_a = {k: c["kind"] for k, c in a["claim"].items()}
    kinds_b = {k: c["kind"] for k, c in b["claim"].items()}
    out["claim_kind_differences"] = sorted(f"{k}: {kinds_a[k]} → {kinds_b[k]}" for k in set(kinds_a) & set(kinds_b)
                                           if kinds_a[k] != kinds_b[k])
    return out


def no_fewer_and_dates_ok(c: dict) -> bool:
    """Gate 26 (items and dates): nothing the reference found is missing; dates identical or more precise."""
    return all(c["counts"][k]["missing"] == 0 for k in KINDS) and not c["dates"]["different"]
