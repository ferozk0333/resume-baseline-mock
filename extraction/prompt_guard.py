"""Prompt-injection screen (specs/18 Phase 4): Llama Prompt Guard 2 on Groq, in the background.

- Screens candidate documents, portfolio pages, READMEs, market postings and the hidden text removed from uploads.
- Runs in its own threads, after the text is saved: it never blocks reading, never changes a score, and nothing it
  finds reaches a recruiter. Scores are cached by chunk text hash.
- Visible text above PROMPT_GUARD_THRESHOLD is kept as is and logged for Admin. Hidden text stays removed; when it is
  also flagged, the candidate sees "We ignored hidden text in your <document>." on Review.
- Prompt Guard reads at most 512 tokens, so text is screened in overlapping chunks; a document's score is its highest.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import Future, ThreadPoolExecutor

from core import config, db
from core.db import new_id, now_iso

log = logging.getLogger(__name__)

POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="prompt-guard")
_JOBS: list[Future] = []
DOC_NAMES = {"resume": "resume", "linkedin_pdf": "LinkedIn PDF", "portfolio": "portfolio page",
             "github_repo": "GitHub README"}


class GuardError(Exception):
    pass


def chunks(text: str) -> list[str]:
    """Overlapping word windows, each within the model's input window."""
    words = (text or "").split()
    out, i = [], 0
    while i < len(words):
        cur, n = [], 0
        j = i
        while j < len(words) and n + len(words[j]) + 1 <= config.PROMPT_GUARD_CHUNK_CHARS:
            cur.append(words[j])
            n += len(words[j]) + 1
            j += 1
        if not cur:                                   # one very long "word": cut it
            cur, j = [words[i][:config.PROMPT_GUARD_CHUNK_CHARS]], i + 1
        out.append(" ".join(cur))
        if j >= len(words):
            break
        back, k = 0, j
        while k > i + 1 and back + len(words[k - 1]) + 1 <= config.PROMPT_GUARD_OVERLAP:
            k -= 1
            back += len(words[k]) + 1
        i = k
    return out


def parse_score(content: str) -> float:
    """The model's answer as P(attack). Groq returns the classifier's output as the message text: a number, or a label
    ("MALICIOUS" / "BENIGN"). [verify] the exact form on the first live call."""
    s = (content or "").strip()
    try:
        return float(json.loads(s)) if s[:1] in "0123456789." else float(s)
    except (ValueError, TypeError):
        up = s.upper()
        if "MALICIOUS" in up or "INJECTION" in up or "JAILBREAK" in up:
            return 1.0
        if "BENIGN" in up:
            return 0.0
    raise GuardError(f"unreadable Prompt Guard answer: {s[:40]!r}")


class GroqGuard:
    """One request per chunk; the 22M model when the 86M one is busy or failing."""

    def __init__(self, session=None, api_key: str | None = None):
        import requests
        self._real = session is None
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else config.GROQ_API_KEY

    def score(self, chunk: str) -> tuple[float, str]:
        from core.llm import require_live
        if self._real:
            require_live("Groq")
        last = None
        for model in config.PROMPT_GUARD_MODELS:
            body = {"model": model.removeprefix("groq/"), "messages": [{"role": "user", "content": chunk}]}
            try:
                resp = self.session.post(config.GROQ_URL, json=body, timeout=15,
                                         headers={"Authorization": f"Bearer {self.api_key}"})
                if resp.status_code != 200:
                    last = GuardError(f"{model}: HTTP {resp.status_code}")
                    continue
                return parse_score(resp.json()["choices"][0]["message"]["content"]), model
            except Exception as exc:                         # noqa: BLE001 - a screen never fails anything
                last = exc
        raise GuardError(str(last))


def _cached(conn, h: str) -> tuple[float, str] | None:
    row = conn.execute("SELECT score, model FROM prompt_guard_cache WHERE text_hash = ? ORDER BY created_at DESC LIMIT 1",
                       (h,)).fetchone()
    return (row[0], row[1]) if row else None


def screen(db_path: str, candidate_id: str | None, evidence_id: str | None, source: str, text: str, guard=None,
           hidden: bool = False) -> list[dict]:
    """Score every chunk (cache first) and log the flagged ones. Returns the flags written."""
    guard = guard or GroqGuard()
    conn = db.connect(db_path)
    flags = []
    try:
        for i, chunk in enumerate(chunks(text)):
            h = hashlib.sha256(chunk.encode()).hexdigest()
            got = _cached(conn, h)
            if got is None:
                try:
                    got = guard.score(chunk)
                except Exception as exc:                     # noqa: BLE001 - unscreened, never blocking
                    log.warning("prompt guard: %s", exc)
                    continue
                with conn:
                    conn.execute("INSERT OR REPLACE INTO prompt_guard_cache (text_hash, model, score, created_at) "
                                 "VALUES (?, ?, ?, ?)", (h, got[1], got[0], now_iso()))
            if got[0] >= config.PROMPT_GUARD_THRESHOLD:
                flag = {"id": new_id(), "chunk_index": i, "chunk": chunk, "score": got[0], "model": got[1]}
                with conn:
                    conn.execute("INSERT INTO prompt_guard_flags (id, candidate_id, evidence_id, source, chunk_index, "
                                 "chunk, score, hidden, model, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 (flag["id"], candidate_id, evidence_id, source, i, chunk, got[0], int(hidden),
                                  got[1], now_iso()))
                flags.append(flag)
    finally:
        conn.close()
    return flags


def screen_in_background(db_path: str, candidate_id: str | None, items: list[tuple[str | None, str, str, bool]],
                         guard=None) -> None:
    """items: (evidence id, source, text, hidden). Returns at once; nothing waits for it."""
    if not config.PROMPT_GUARD:
        return
    for evidence_id, source, text, hidden in items:
        if text and text.strip():
            _JOBS.append(POOL.submit(screen, db_path, candidate_id, evidence_id, source, text, guard, hidden))


def wait(timeout: float = 60) -> None:
    """Tests and scripts only."""
    for job in list(_JOBS):
        job.result(timeout)
    _JOBS.clear()


def documents_to_screen(conn, candidate_id: str) -> list[tuple[str | None, str, str, bool]]:
    """Each document's text, plus any hidden text removed from it (screened separately, marked hidden)."""
    from ingest.documents import hidden_text
    out = []
    for e in conn.execute("SELECT id, type, raw_text, file_sha256 FROM evidence_items WHERE candidate_id = ? AND "
                          "type IN ('resume', 'linkedin_pdf', 'portfolio', 'github_repo')", (candidate_id,)):
        out.append((e[0], e[1], e[2] or "", False))
        if e[3]:
            removed = " ".join(h["text"] for h in hidden_text(conn, e[3]))
            if removed:
                out.append((e[0], e[1], removed, True))
    return out


def notices(conn, candidate_id: str) -> list[str]:
    """What the candidate sees on Review: only hidden text that was also flagged (it stayed removed)."""
    rows = conn.execute("SELECT DISTINCT e.type FROM prompt_guard_flags f JOIN evidence_items e ON e.id = f.evidence_id "
                        "WHERE f.candidate_id = ? AND f.hidden = 1", (candidate_id,)).fetchall()
    return [f"We ignored hidden text in your {DOC_NAMES.get(r[0], 'document')}." for r in rows]


def admin_flags(conn, candidate_id: str | None = None) -> list[dict]:
    q = "SELECT * FROM prompt_guard_flags" + (" WHERE candidate_id = ?" if candidate_id else "") + " ORDER BY created_at DESC"
    return [dict(r) for r in conn.execute(q, (candidate_id,) if candidate_id else ())]
