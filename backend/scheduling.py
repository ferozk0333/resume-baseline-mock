"""Interview slots and calendar invites (specs/16 §3). Code only, no model.

- Availability windows are the job's free text, e.g. "Mon–Wed 10:00–14:00 ET" or
  "Mon, Thu 09:00-12:00 PT; Fri 13:00-15:00 PT". Unreadable text → no proposals (the recruiter types a time).
- `propose` offers 2–3 slots: the candidate's own times first when they fit the windows and are free, then the
  first free slot on each of the next available days. Slots already booked for this job are skipped.
- `parse_times` reads simple times from a reply ("Tuesday 11:00", "Oct 20 at 2pm ET"): the code fallback when
  the reply model is unavailable, and the check on the model's times.
- Calendar: `CalendarProvider` with an .ics implementation (no calendar API). A Google Calendar provider is out
  of scope.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
TZ_ALIASES = {"ET": "America/New_York", "EST": "America/New_York", "EDT": "America/New_York",
              "CT": "America/Chicago", "CST": "America/Chicago", "CDT": "America/Chicago",
              "MT": "America/Denver", "MST": "America/Denver", "MDT": "America/Denver",
              "PT": "America/Los_Angeles", "PST": "America/Los_Angeles", "PDT": "America/Los_Angeles",
              "UTC": "UTC", "GMT": "UTC", "BST": "Europe/London", "CET": "Europe/Berlin", "IST": "Asia/Kolkata"}
MIN_LEAD = timedelta(hours=12)          # no slot sooner than this
SEARCH_DAYS = 21
STEP = timedelta(minutes=30)


def now() -> datetime:
    """Current time (UTC); tests replace it."""
    return datetime.now(timezone.utc)


def zone(name: str | None) -> ZoneInfo:
    name = (name or "").strip()
    try:
        return ZoneInfo(TZ_ALIASES.get(name.upper(), name) or "UTC")
    except (KeyError, ValueError):
        return ZoneInfo("UTC")


def tz_name(dt: datetime) -> str:
    return getattr(dt.tzinfo, "key", None) or "UTC"


def default_zone(windows: list) -> str:
    return windows[0].tz if windows else "America/New_York"


@dataclass(frozen=True)
class Window:
    days: frozenset[int]                # 0 = Monday
    start: time
    end: time
    tz: str                             # IANA name


def _day_index(word: str) -> int | None:
    w = word.lower()[:3]
    return DAYS.index(w) if w in DAYS else None


_TIME = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?"
_WINDOW = re.compile(rf"(?P<days>[A-Za-z ,–\-/&]+?)\s+{_TIME}\s*(?:–|-|to)\s*{_TIME}\s*(?P<tz>[A-Za-z_/]+)?\s*$", re.I)


def _clock(h: str, m: str | None, ampm: str | None) -> time:
    hour, minute = int(h), int(m or 0)
    if ampm:
        hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
    return time(hour, minute)


def _days(text: str) -> set[int]:
    out: set[int] = set()
    for part in re.split(r"\s*(?:,|/|&|\band\b)\s*", text.strip()):
        bits = [b for b in re.split(r"\s*(?:–|-|\bto\b)\s*", part) if b]
        idx = [_day_index(b) for b in bits]
        if len(idx) == 2 and None not in idx:
            a, b = idx
            out.update(range(a, b + 1) if a <= b else [*range(a, 7), *range(0, b + 1)])
        elif len(idx) == 1 and idx[0] is not None:
            out.add(idx[0])
        elif part.strip().lower() in ("weekdays", "weekday"):
            out.update(range(5))
    return out


def parse_availability(text: str, default_tz: str = "ET") -> list[Window]:
    """Windows separated by ";" or newlines. A window without a time zone uses the previous one (or ET)."""
    out, tz = [], TZ_ALIASES.get(default_tz, default_tz)
    for chunk in re.split(r"[;\n]+", text or ""):
        m = _WINDOW.match(chunk.strip())
        if not m:
            continue
        days = _days(m.group("days"))
        if m.group("tz"):
            tz = zone(m.group("tz")).key
        start, end = _clock(m.group(2), m.group(3), m.group(4)), _clock(m.group(5), m.group(6), m.group(7))
        if days and start < end:
            out.append(Window(frozenset(days), start, end, tz))
    return out


@dataclass
class Slot:
    start: datetime                     # aware, in the window's zone
    end: datetime
    source: str = "availability"        # availability | candidate
    fits: bool = True                   # inside a window and free
    note: str | None = None

    def as_dict(self) -> dict:
        return {"start": self.start.isoformat(), "end": self.end.isoformat(), "timezone": tz_name(self.start),
                "label": label(self.start, self.end), "source": self.source, "fits": self.fits, "note": self.note}


def label(start: datetime, end: datetime | None = None) -> str:
    tz = start.tzname() or ""
    text = f"{start:%a %b} {start.day}, {start:%H:%M}"
    return f"{text}–{end:%H:%M} {tz}" if end else f"{text} {tz}"


def _overlaps(a0, a1, b0, b1) -> bool:
    return a0 < b1 and b0 < a1


def fits(start: datetime, duration: timedelta, windows: list[Window]) -> bool:
    for w in windows:
        local = start.astimezone(ZoneInfo(w.tz))
        if local.weekday() in w.days and w.start <= local.time() and (local + duration).time() <= w.end \
                and (local + duration).date() == local.date():
            return True
    return False


def free(start: datetime, duration: timedelta, booked: list[tuple[datetime, datetime]]) -> bool:
    return not any(_overlaps(start, start + duration, b0, b1) for b0, b1 in booked)


def propose(windows: list[Window], duration_minutes: int, booked: list[tuple[datetime, datetime]], *,
            preferred: list[datetime] = (), after: date | None = None, n: int = 3,
            at: datetime | None = None) -> list[Slot]:
    """The candidate's fitting times first, then the first free slot on each following available day."""
    at = at or now()
    duration = timedelta(minutes=duration_minutes)
    earliest = at + MIN_LEAD
    out: list[Slot] = []
    for p in preferred:
        ok = p >= earliest and fits(p, duration, windows) and free(p, duration, booked)
        note = None if ok else ("outside the availability windows" if not fits(p, duration, windows)
                                else "already booked" if not free(p, duration, booked) else "too soon")
        out.append(Slot(p, p + duration, "candidate", ok, note))
    taken_days = {s.start.date() for s in out if s.fits}
    start_day = max(earliest.date(), after) if after else earliest.date()
    for offset in range(SEARCH_DAYS):
        if sum(1 for s in out if s.fits) >= n:
            break
        for w in windows:
            tzi = ZoneInfo(w.tz)
            day = start_day + timedelta(days=offset)
            if day.weekday() not in w.days or day in taken_days:
                continue
            t = datetime.combine(day, w.start, tzi)
            close = datetime.combine(day, w.end, tzi)
            while t + duration <= close:
                if t >= earliest and free(t, duration, booked):
                    out.append(Slot(t, t + duration))
                    taken_days.add(day)
                    break
                t += STEP
            if day in taken_days:
                break
    good = [s for s in out if s.fits][:n]
    return [*good, *[s for s in out if not s.fits]]


# --- Times in a reply (code) --------------------------------------------------------------------------------

_WEEKDAY = r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?"
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
_TZ_WORD = r"\b(ET|EST|EDT|CT|CST|CDT|MT|MST|MDT|PT|PST|PDT|UTC|GMT|BST|CET|IST)\b"


def parse_times(text: str, default_tz: str, at: datetime | None = None) -> list[tuple[str, datetime]]:
    """(quote, start) pairs for "Tuesday 11:00", "Tue at 2pm ET", "Oct 20 10:30". Weekdays mean the next one
    after today. A time without am/pm from 1 to 7 is read as afternoon."""
    at = at or now()
    out = []
    pattern = re.compile(rf"\b(?:(?:next\s+)?{_WEEKDAY}|({'|'.join(_MONTHS)})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?)"
                         rf"(?:,?\s*(?:at|@|from)?\s*)(\d{{1,2}})(?::(\d{{2}}))?\s*(am|pm)?(?:\s*{_TZ_WORD})?", re.I)
    for m in pattern.finditer(text):
        wd, mon, mday, h, mi, ampm = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5), m.group(6)
        if not mi and not ampm:
            continue                                    # "Tuesday 3" is not clearly a time
        tz = zone(m.group(7) or default_tz)
        local_today = at.astimezone(tz).date()
        hour = int(h)
        if not ampm and 1 <= hour <= 7:
            ampm = "pm"
        try:
            clock = _clock(h, mi, ampm)
        except ValueError:
            continue
        if wd:
            target = DAYS.index(wd.lower()[:3])
            ahead = (target - local_today.weekday()) % 7 or 7       # "next Tuesday" reads the same
            day = local_today + timedelta(days=ahead)
        else:
            month = _MONTHS.index(mon.lower()[:3]) + 1
            try:
                day = date(local_today.year, month, int(mday))
            except ValueError:
                continue
            if day < local_today:
                day = date(local_today.year + 1, month, int(mday))
        out.append((m.group(0).strip(), datetime.combine(day, clock, tz)))
    return out


def mentions_next_week(text: str) -> bool:
    return bool(re.search(r"\bnext week\b", text, re.I))


def next_monday(at: datetime | None = None) -> date:
    d = (at or now()).date()
    return d + timedelta(days=7 - d.weekday())


# --- Calendar invites -------------------------------------------------------------------------------------------

@dataclass
class Invite:
    uid: str
    start: datetime
    end: datetime
    summary: str
    description: str
    organizer: str | None
    organizer_name: str
    attendee: str
    attendee_name: str | None
    sequence: int = 0


class CalendarProvider(Protocol):
    """How a confirmed interview reaches the candidate's calendar."""
    name: str

    def attachment(self, invite: Invite) -> dict | None: ...


def _ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """RFC 5545 §3.1: lines longer than 75 octets continue on the next line after CRLF + space."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not parts else 74):
            parts.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    parts.append(cur.decode("utf-8"))
    return "\r\n ".join(parts)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


class IcsCalendar:
    """An .ics invite attached to the confirmation email (METHOD:REQUEST). No calendar API."""
    name = "ics"

    def ics(self, inv: Invite, stamp: datetime | None = None) -> str:
        org = f"ORGANIZER;CN={_ics_text(inv.organizer_name)}:mailto:{inv.organizer}" if inv.organizer else None
        who = f";CN={_ics_text(inv.attendee_name)}" if inv.attendee_name else ""
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//RightStaff//Hiring workflow//EN", "CALSCALE:GREGORIAN",
                 "METHOD:REQUEST", "BEGIN:VEVENT", f"UID:{inv.uid}", f"DTSTAMP:{_utc(stamp or now())}",
                 f"DTSTART:{_utc(inv.start)}", f"DTEND:{_utc(inv.end)}", f"SUMMARY:{_ics_text(inv.summary)}",
                 f"DESCRIPTION:{_ics_text(inv.description)}", *([org] if org else []),
                 f"ATTENDEE{who};ROLE=REQ-PARTICIPANT;PARTSTAT=NEEDS-ACTION;RSVP=TRUE:mailto:{inv.attendee}",
                 f"SEQUENCE:{inv.sequence}", "STATUS:CONFIRMED", "TRANSP:OPAQUE", "END:VEVENT", "END:VCALENDAR"]
        return "\r\n".join(_fold(x) for x in lines) + "\r\n"

    def attachment(self, invite: Invite) -> dict:
        return {"filename": "invite.ics", "content_type": "text/calendar; method=REQUEST; charset=UTF-8",
                "text": self.ics(invite)}


def new_uid() -> str:
    return f"{uuid.uuid4()}@rightstaff.local"


CALENDAR: CalendarProvider = IcsCalendar()
