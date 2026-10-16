"""Postings: store, dedupe, window, level (code first), and the extraction cache (specs/15 §3–§5)."""
from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from core import config
from core.db import new_id, now_iso
from extraction import job_reader as jr

LEVEL_TITLE = [("lead", r"\b(staff|lead|principal|head|director)\b"), ("senior", r"\b(senior|sr\.?|iii)\b"),
               ("mid", r"\b(ii|mid[- ]level)\b"), ("entry", r"\b(associate|junior|jr\.?|new grad|graduate|entry|i)\b")]


def norm_text(text: str) -> str:
    return " ".join(re.sub(r"[^\w+#/]+", " ", text.lower()).split())


def text_hash(text: str) -> str:
    return hashlib.sha256(norm_text(text).encode()).hexdigest()


def code_level(title: str, text: str) -> str | None:
    """Stated years, then title words. None when code can't decide (the extraction's level is used)."""
    t = title.lower()
    for level, rx in LEVEL_TITLE:
        if level != "entry" and re.search(rx, t):
            return level
    years = None
    for line in text.splitlines():
        if re.search(r"\byears?\b|\bnew grads?\b|\brecent graduates?\b", line, re.I):
            y = jr.parse_years(line)
            if y is not None:
                years = y if years is None else min(years, y)
    if years is not None:
        return "entry" if years <= config.ENTRY_LEVEL_MAX_YEARS else "mid" if years <= 5 else "senior" if years <= 8 else "lead"
    if re.search(LEVEL_TITLE[-1][1], t):
        return "entry"
    return None


def now() -> datetime:
    return datetime.now(timezone.utc)


def add_posting(conn, *, role: str, company: str, tier: str, source: str, title: str, text: str,
                source_id: str = "", external_id: str | None = None, url: str | None = None, location: str | None = None,
                posted_at: str | None = None, raw_file: str | None = None, seen_at: str | None = None,
                status: str = "active") -> tuple[str, bool]:
    """(posting id, new?). The same company with the same or a near-identical text is one posting (reposts,
    multi-city copies): only last_seen moves."""
    seen = seen_at or now_iso()
    h = text_hash(text)
    same = conn.execute("SELECT id FROM market_postings WHERE role = ? AND company = ? AND (text_hash = ? OR "
                        "(external_id IS NOT NULL AND external_id = ? AND source = ?))",
                        (role, company, h, external_id, source)).fetchone()
    if same is None:
        mine = norm_text(text)
        for pid, other in conn.execute("SELECT id, text FROM market_postings WHERE role = ? AND company = ?",
                                       (role, company)).fetchall():
            if difflib.SequenceMatcher(None, mine, norm_text(other), autojunk=False).ratio() >= config.DEDUPE_SIMILARITY:
                same = (pid,)
                break
    if same is not None:
        with conn:
            conn.execute("UPDATE market_postings SET last_seen = MAX(last_seen, ?), status = CASE WHEN status = "
                         "'closed' THEN 'active' ELSE status END WHERE id = ?", (seen, same[0]))
        return same[0], False
    level = code_level(title, text)
    pid = new_id()
    with conn:
        conn.execute("INSERT INTO market_postings (id, role, company, tier, source, source_id, external_id, source_url, "
                     "title, location, level, level_method, text_hash, text, raw_file, posted_at, first_seen, last_seen, "
                     "status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (pid, role, company, tier, source, source_id, external_id, url, title, location, level,
                      "code" if level else None, h, text, raw_file, posted_at, seen, seen, status, now_iso()))
    return pid, True


def postings(conn, role: str, *, in_window: bool = True, at: datetime | None = None) -> list[dict]:
    q, args = "SELECT * FROM market_postings WHERE role = ? AND status = 'active'", [role]
    if in_window:
        q += " AND last_seen >= ?"
        args.append(((at or now()) - timedelta(days=config.WINDOW_DAYS)).isoformat(timespec="seconds"))
    return [dict(r) for r in conn.execute(q + " ORDER BY company, first_seen", args)]


def close_missing(conn, role: str, company: str, source: str, open_ids: set[str]) -> int:
    """Postings of a fetched board that are no longer listed stop counting (their last_seen stays)."""
    rows = conn.execute("SELECT id, external_id FROM market_postings WHERE role = ? AND company = ? AND source = ? "
                        "AND status = 'active'", (role, company, source)).fetchall()
    gone = [r[0] for r in rows if r[1] and r[1] not in open_ids]
    with conn:
        for pid in gone:
            conn.execute("UPDATE market_postings SET status = 'closed' WHERE id = ?", (pid,))
    return len(gone)


# --- Extraction, cached by text hash -------------------------------------------------------------------------------

def cached(conn, h: str) -> dict | None:
    row = conn.execute("SELECT extract_json FROM market_extractions WHERE text_hash = ?", (h,)).fetchone()
    return json.loads(row[0]) if row else None


def save_extraction(conn, h: str, model: str, data: dict) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO market_extractions (text_hash, model, extract_json, created_at) "
                     "VALUES (?, ?, ?, ?)", (h, model, json.dumps(data), now_iso()))


def extract(conn, posting: dict, llm) -> dict | None:
    """The spec 14 JD reader, once per posting text (never twice). None when the model is unavailable."""
    hit = cached(conn, posting["text_hash"])
    if hit is not None:
        return hit
    if llm is None:
        return None
    from extraction.job_reader import read_job
    out = read_job(llm, posting["text"], model=config.MARKET_EXTRACT_MODEL)
    if out["status"] != "ok":
        return None
    save_extraction(conn, posting["text_hash"], out["model"], out)
    return out


def set_level(conn, posting: dict, extraction: dict | None) -> str | None:
    """Every posting gets a level: code first; then the extraction, but only when the posting states a level or
    years (the reader's own default of "entry" is never used here). Otherwise the level stays unknown and the
    posting counts toward no level baseline. Only the entry baseline is built for now; mid/senior/lead postings are
    kept for later level baselines."""
    if posting["level"] or not extraction:
        return posting["level"]
    quotes = extraction.get("detail_quotes") or {}
    stated = bool(quotes.get("level") or quotes.get("min_years"))
    level = extraction["details"].get("level") if stated else None
    with conn:
        conn.execute("UPDATE market_postings SET level = ?, level_method = ? WHERE id = ?",
                     (level, "extraction" if stated else "unknown", posting["id"]))
    return level


def level_counts(conn, role: str) -> dict[str, int]:
    rows = conn.execute("SELECT COALESCE(level, 'unknown'), COUNT(*) FROM market_postings WHERE role = ? AND "
                        "status = 'active' GROUP BY 1", (role,)).fetchall()
    return {r[0]: r[1] for r in rows}
