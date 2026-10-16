"""Candidate replies (specs/16 §3–§4): reading them and receiving them.

Reading (REPLY_MODEL, one call per reply): a label (`slot_chosen` / `times_proposed` / `question` / `decline` /
`unclear`), a supporting quote, any times with their time zone, and the question. Every quote must be found in
the reply; a label whose quote is not found becomes `unclear`. When the model is unavailable, simple code rules
read the reply instead ("Read by code rules"). Either way the reading is shown to the recruiter and only ever
proposes the next step: nothing is acted on alone.

Receiving: pasted on the workflow page, or (REPLY_INBOX=imap) polled from the demo inbox over IMAP, read-only.
A message belongs to a workflow by the "[RS-<token>]" in its subject, else by In-Reply-To/References naming a
message we sent. Messages we sent ourselves (the demo inbox receives them too) are skipped by Message-ID.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend import scheduling as sch
from core import config
from core.llm import LLMError
from core.text import wrap_tag
from extraction.extract import load_prompt

log = logging.getLogger(__name__)

LABELS = ("slot_chosen", "times_proposed", "question", "decline", "unclear")
CODE_RULES = "code rules"
TOKEN_RE = re.compile(r"\[RS-([0-9a-f]{6})\]", re.I)
CANNED = {"slot": "Tuesday 11:00 works for me. Thanks!",
          "next_week": "Thanks for the invite! Can we do next week instead?",
          "question": "Thanks! Will the interview be on video, and should I prepare anything?",
          "decline": "Thank you for considering me. I've accepted another offer, so I'd like to withdraw my application."}


class ReplyTime(BaseModel):
    quote: str = Field(description="The exact words in the reply that give this time.")
    start: str = Field(description="Local start as ISO 8601 without offset, e.g. 2026-10-20T11:00.")
    timezone: str | None = Field(None, description="IANA zone or abbreviation stated in the reply; null if none.")


class ReplyReading(BaseModel):
    label: Literal["slot_chosen", "times_proposed", "question", "decline", "unclear"]
    quote: str = Field(description="The exact sentence of the reply that supports the label.")
    times: list[ReplyTime] = Field(default_factory=list)
    question: str | None = Field(None, description="The candidate's question in their words, if any.")


def _found(quote: str, text: str) -> bool:
    norm = lambda s: re.sub(r"\s+", " ", s).strip().lower()           # noqa: E731
    return bool(quote.strip()) and norm(quote) in norm(text)


def code_reading(text: str, default_tz: str, at: datetime | None = None) -> dict:
    """Code rules: decline words, times (+ "works"), a question mark. Otherwise unclear."""
    first = lambda pattern: next((s for s in re.split(r"(?<=[.!?])\s+", text) if re.search(pattern, s, re.I)), text)  # noqa: E731
    decline = r"\b(accepted (?:another|an(?:other)? offer|a different)|withdraw|no longer (?:interested|available|looking)|" \
              r"not interested|(?:have to|must) decline|pull out)\b"
    times = sch.parse_times(text, default_tz, at)
    if re.search(decline, text, re.I):
        label, pattern = "decline", decline
    elif times and len(times) == 1 and re.search(r"\b(works?|good|great|perfect|confirm|fine|suits?)\b", text, re.I):
        label, pattern = "slot_chosen", re.escape(times[0][0])
    elif times or re.search(r"\b(next week|another (?:time|day)|reschedul\w*|different time|later date)\b", text, re.I):
        label, pattern = "times_proposed", re.escape(times[0][0]) if times else \
            r"next week|another (?:time|day)|reschedul|different time|later date"
    elif "?" in text:
        label, pattern = "question", r"\?"
    else:
        label, pattern = "unclear", r"."
    quote = first(pattern).strip()
    q = next((s for s in re.split(r"(?<=[.!?])\s+", text) if s.strip().endswith("?")), None)
    return {"label": label, "quote": quote, "question": q if label == "question" else None,
            "times": [{"quote": qt, "start": t.isoformat(), "timezone": t.tzinfo.key} for qt, t in times],
            "method": CODE_RULES, "model": None, "next_week": sch.mentions_next_week(text), "notes": []}


def read_reply(writer, text: str, *, sent_context: str, default_tz: str, at: datetime | None = None) -> dict:
    """One REPLY_MODEL call, quote-checked; code rules when the model is unavailable."""
    at = at or sch.now()
    if writer is None:
        return code_reading(text, default_tz, at) | {"notes": ["Reply model not configured: read by code rules."]}
    user = "\n\n".join([wrap_tag("today", at.astimezone(sch.zone(default_tz)).strftime("%A %Y-%m-%d")),
                        wrap_tag("default_timezone", default_tz), wrap_tag("our_last_email", sent_context),
                        wrap_tag("candidate_reply", text)])
    try:
        r = writer.parse(model=config.REPLY_MODEL, system=load_prompt("reply_reading_system"), user=user,
                         output_type=ReplyReading)
    except LLMError as exc:
        log.warning("reply model unavailable, using code rules: %s", exc)
        return code_reading(text, default_tz, at) | {"notes": [f"Reply model unavailable ({exc}): read by code rules."]}
    notes, times = [], []
    for t in r.times:
        if not _found(t.quote, text):
            notes.append(f"time dropped: quote not found in the reply ({t.quote!r})")
            continue
        try:
            start = datetime.fromisoformat(t.start)
        except ValueError:
            notes.append(f"time dropped: unreadable start {t.start!r}")
            continue
        tz = sch.zone(t.timezone or default_tz)
        start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
        if not at - timedelta(days=1) <= start <= at + timedelta(days=90):
            notes.append(f"time dropped: {start:%Y-%m-%d} is not in the next 90 days")
            continue
        times.append({"quote": t.quote, "start": start.isoformat(), "timezone": tz.key})
    label = r.label
    if not _found(r.quote, text):
        notes.append("label quote not found in the reply: shown as unclear")
        label = "unclear"
    if label == "slot_chosen" and not times:
        notes.append("no readable time for the chosen slot: shown as times proposed")
        label = "times_proposed"
    return {"label": label, "quote": r.quote, "times": times, "question": r.question, "method": "model",
            "model": config.REPLY_MODEL, "next_week": sch.mentions_next_week(text), "notes": notes}


# --- Receiving: IMAP (optional) --------------------------------------------------------------------------------

@dataclass
class Inbound:
    message_id: str | None
    in_reply_to: str | None
    references: list[str]
    subject: str
    from_addr: str
    body: str
    date: str | None


# Gmail wraps "On Fri, Oct 16, 2026 at 9:00 AM Name <a@b.c> wrote:" over two lines; Outlook uses "From:" headers.
_QUOTE_START = re.compile(r"^(On [^\n]{5,250}(?:\n[^\n]{0,120})?wrote:\s*$|-{2,}\s*Original Message\s*-{2,}|From: .+|>)",
                          re.I | re.M)


def strip_quoted(text: str) -> str:
    """The new part of a reply: everything before the quoted original."""
    m = _QUOTE_START.search(text)
    return (text[:m.start()] if m else text).strip()


def parse_message(raw: bytes) -> Inbound:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    part = msg.get_body(preferencelist=("plain", "html"))
    body = part.get_content() if part is not None else ""
    if part is not None and part.get_content_type() == "text/html":
        from bs4 import BeautifulSoup
        body = BeautifulSoup(body, "html.parser").get_text("\n")
    refs = (msg.get("References") or "").split()
    try:
        when = parsedate_to_datetime(msg["Date"]).isoformat() if msg["Date"] else None
    except (TypeError, ValueError):
        when = None
    subject, sender = str(msg.get("Subject") or ""), str(msg.get("From") or "")
    message_id = msg.get("Message-ID") or fallback_id(sender, str(msg.get("Date") or ""), subject, body)
    return Inbound(message_id, msg.get("In-Reply-To"), refs, subject, sender, strip_quoted(body), when)


def fallback_id(sender: str, date: str, subject: str, body: str) -> str:
    """A stable key for a message without a Message-ID (sender, date, subject, first 500 characters of the body),
    so the poll never reads it twice."""
    import hashlib
    h = hashlib.sha256("\n".join([sender.strip(), date.strip(), subject.strip(), body[:500]]).encode()).hexdigest()
    return f"<no-message-id-{h[:32]}@rightstaff.local>"


def match(conn, inbound: Inbound) -> str | None:
    """The application a reply belongs to: subject token first, then In-Reply-To / References."""
    m = TOKEN_RE.search(inbound.subject)
    if m:
        row = conn.execute("SELECT application_id FROM workflows WHERE token = ?", (m.group(1).lower(),)).fetchone()
        if row:
            return row[0]
    for ref in filter(None, [inbound.in_reply_to, *inbound.references]):
        row = conn.execute("SELECT application_id FROM messages WHERE direction = 'out' AND message_id = ?",
                           (ref.strip(),)).fetchone()
        if row:
            return row[0]
    return None


def known(conn, message_id: str | None) -> bool:
    """Already stored (received before, or one of ours: the demo inbox receives what we send)."""
    return bool(message_id) and conn.execute("SELECT 1 FROM messages WHERE message_id = ?",
                                             (message_id.strip(),)).fetchone() is not None


def our_copy(conn, inbound: Inbound) -> bool:
    """Our own sent email arriving in the demo inbox (it is sent to the same address). Matched by Message-ID, or by
    the exact subject of a message we sent (a reply's subject starts with "Re:")."""
    if known(conn, inbound.message_id):
        return True
    return conn.execute("SELECT 1 FROM messages WHERE direction = 'out' AND subject = ?",
                        (inbound.subject.strip(),)).fetchone() is not None


def header_wanted(conn, raw_headers: bytes) -> bool:
    """Cheap first pass on headers only: a subject token or a reference to a message we sent."""
    msg = email.message_from_bytes(raw_headers, policy=email.policy.default)
    if TOKEN_RE.search(str(msg.get("Subject") or "")):
        return True
    refs = [*(str(msg.get("In-Reply-To") or "").split()), *(str(msg.get("References") or "").split())]
    return any(conn.execute("SELECT 1 FROM messages WHERE direction = 'out' AND message_id = ?", (r,)).fetchone()
               for r in refs)


class ImapInbox:
    """Read-only polling of the demo inbox (BODY.PEEK: nothing is marked read, moved or deleted). Searches by date
    only (Gmail's SUBJECT search tokenizes "[RS-…]"), reads headers first, then the full text of candidates."""

    def __init__(self, factory=None):
        self.factory = factory or (lambda: imaplib.IMAP4_SSL(config.IMAP_HOST, config.IMAP_PORT, timeout=30))

    def fetch(self, since_days: int = 7, wanted=lambda headers: True) -> list[bytes]:
        if not (config.IMAP_USER and config.IMAP_PASSWORD):
            raise RuntimeError("IMAP is not configured: set IMAP_USER/IMAP_PASSWORD (or SMTP_USER/SMTP_PASSWORD).")
        box = self.factory()
        try:
            box.login(config.IMAP_USER, config.IMAP_PASSWORD)
            box.select("INBOX", readonly=True)
            since = (sch.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            typ, data = box.search(None, "SINCE", since)
            out = []
            for num in (data[0] or b"").split():
                typ, parts = box.fetch(num, "(BODY.PEEK[HEADER.FIELDS (SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES)])")
                head = next((p[1] for p in parts if isinstance(p, tuple)), b"")
                if not wanted(head):
                    continue
                typ, parts = box.fetch(num, "(BODY.PEEK[])")
                out += [p[1] for p in parts if isinstance(p, tuple)]
            return out
        finally:
            try:
                box.logout()
            except Exception:                       # noqa: BLE001 - logout failures don't matter
                pass
