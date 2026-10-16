"""Running the market pipeline: fetch (weekly), manual postings, fixtures (test mode), dry-run counts (specs/15)."""
from __future__ import annotations

import json
import math
from pathlib import Path

from core import config
from market import fetchers, sources
from market import store as ms


def fetch_role(conn, role: sources.Role, session=None) -> dict:
    """Read every configured public board for the role. New matching postings are stored; known ones get a new
    last_seen; postings no longer listed stop counting. No model calls."""
    out = {"boards": 0, "seen": 0, "new": 0, "excluded_titles": 0, "closed": 0, "errors": [], "skipped": 0}
    for c in role.companies:
        if c.placeholder or c.source == "manual" or not c.source_id:
            out["skipped"] += 1
            continue
        try:
            posts = fetchers.fetch(c.source, c.source_id, session)
        except (fetchers.FetchError, OSError, ValueError) as exc:
            out["errors"].append(f"{c.name} ({c.source}): {exc}")
            continue
        out["boards"] += 1
        open_ids = set()
        for p in posts:
            if not role.title_matches(p.title):
                out["excluded_titles"] += 1
                continue
            open_ids.add(p.external_id)
            _, new = ms.add_posting(conn, role=role.key, company=c.name, tier=c.tier, source=c.source,
                                    source_id=c.source_id, external_id=p.external_id, title=p.title, text=p.text,
                                    url=p.url, location=p.location, posted_at=p.posted_at)
            out["seen"] += 1
            out["new"] += int(new)
        out["closed"] += ms.close_missing(conn, role.key, c.name, c.source, open_ids)
    return out


def add_manual(conn, role: sources.Role, *, company: str, tier: str, title: str, text: str, url: str | None = None,
               posted_at: str | None = None, raw_file: str | None = None) -> dict:
    """The "Add postings" screen: pasted text or an uploaded PDF/DOCX (normalized by the caller)."""
    if tier not in sources.TIERS:
        raise ValueError(f"tier must be one of {sources.TIERS}")
    if len(text.strip()) < 200:
        raise ValueError("Paste the full posting (at least a few sentences).")
    if not role.title_matches(title):
        raise ValueError(f"“{title}” does not match the {role.title} title filter (see config/market_sources.yaml).")
    pid, new = ms.add_posting(conn, role=role.key, company=company.strip(), tier=tier, source="manual", title=title,
                              text=text, url=url, posted_at=posted_at, raw_file=raw_file)
    return {"id": pid, "new": new}


def load_fixtures(conn, role: sources.Role, folder: Path | None = None) -> int:
    """Test mode: the hand-labelled postings, with their labels stored as the (cached) extraction."""
    folder = folder or config.MARKET_FIXTURES / role.key
    n = 0
    for meta_path in sorted(folder.glob("*.json")):
        meta = json.loads(meta_path.read_text())
        text = meta_path.with_suffix(".txt").read_text()
        pid, new = ms.add_posting(conn, role=role.key, company=meta["company"], tier=meta["tier"], source="fixture",
                                  title=meta["title"], text=text, posted_at=meta.get("posted_at"))
        ms.save_extraction(conn, ms.text_hash(text), "hand-labelled", meta["extraction"])
        n += int(new)
    return n


def dry_run(conn, roles: dict[str, sources.Role], rpd: int | None = None) -> dict:
    """Counts without calling any model: postings per source, new (not yet extracted) postings, model calls and
    days at the configured daily limit."""
    model = config.MARKET_EXTRACT_MODEL
    if rpd is None:
        rpd = config.gemini_limits(model.split("/", 1)[-1])[2] if model.startswith("gemini/") else 0
    out = {"model": model, "requests_per_day": rpd or None, "roles": {}}
    total = 0
    for key, role in roles.items():
        by_source: dict[str, dict] = {}
        for c in role.companies:
            s = by_source.setdefault(c.source, {"companies": 0, "placeholders": 0})
            s["companies"] += 1
            s["placeholders"] += int(c.placeholder)
        rows = ms.postings(conn, key)
        for p in rows:
            by_source.setdefault(p["source"], {"companies": 0, "placeholders": 0})
            by_source[p["source"]]["postings"] = by_source[p["source"]].get("postings", 0) + 1
        new = [p for p in rows if ms.cached(conn, p["text_hash"]) is None]
        summary = 1 if rows else 0                          # one summary per role per version, when it has postings
        calls = len(new) + summary
        total += calls
        out["roles"][key] = {"sources": by_source, "postings_in_window": len(rows), "new_postings": len(new),
                             "companies_with_postings": len({p["company"] for p in rows}),
                             "model_calls": {"extraction": len(new), "summary": summary, "total": calls}}
    pending = conn.execute("SELECT COUNT(*) FROM market_suggestions WHERE status = 'pending' AND canonical IS NULL"
                           ).fetchone()[0]
    out["merge_batches"] = math.ceil(pending / config.MARKET_MERGE_BATCH) if pending else 0   # names are shared
    total += out["merge_batches"]
    out["total_model_calls"] = total
    out["days_at_limit"] = math.ceil(total / rpd) if rpd else None
    return out
