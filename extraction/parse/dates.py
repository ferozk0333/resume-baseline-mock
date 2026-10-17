"""Date ranges as written in resumes and LinkedIn exports (specs/18 §4.2 item 16). No library: the formats are few.

Handles "Jan 2020", "January 2020", "Sep. 2020", "01/2020", "2020-01", "2019–2022", "Summer 2021" and
"Present/Current". Values come out as the extraction schema writes them: "YYYY-MM", or "YYYY" for a year alone
(validation then sets Jan/Dec and flags it for review, exactly as for an LLM extraction)."""
from __future__ import annotations

import re
from dataclasses import dataclass

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "sept": 9,
          "oct": 10, "nov": 11, "dec": 12}
_MON = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
        r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_SEASON = r"(?:spring|summer|fall|autumn|winter)"
_POINT = rf"(?:{_MON}\s*'?\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}}[-/]\d{{1,2}}(?!\d)|{_SEASON}\s+\d{{4}}|(?<![\d/-])\d{{4}}(?![\d/-]))"
_NOW = r"(?:present|current|now|today|ongoing)"
_SEP = r"\s*(?:–|—|‒|-|to|until|through|till)\s*"
RANGE = re.compile(rf"(?P<start>{_POINT}){_SEP}(?P<end>{_POINT}|{_NOW})", re.I)
SINGLE = re.compile(rf"(?:expected\s+|exp\.?\s+|issued\s+)?(?P<point>{_POINT})", re.I)
DURATION = re.compile(r"^\(?\s*(?:\d+\s+(?:years?|yrs?|months?|mos?)\s*)+\)?$", re.I)


@dataclass
class DateRange:
    start: str | None            # "YYYY-MM" | "YYYY"
    end: str | None
    is_current: bool
    raw: str                     # the exact text matched
    span: tuple[int, int]
    needs_review: bool = False   # a season or a year alone


def point(raw: str) -> tuple[str | None, bool]:
    """One date → (schema value, approximate)."""
    s = raw.strip().casefold().replace("’", "'")
    if m := re.fullmatch(rf"({_MON})\s*'?(\d{{4}})", s):
        mon = re.match(r"[a-z]+", m.group(1)).group(0)
        key = "sept" if mon.startswith("sept") else mon[:3]
        return f"{m.group(2)}-{MONTHS[key]:02d}", False
    if m := re.fullmatch(r"(\d{1,2})/(\d{4})", s):
        return (f"{m.group(2)}-{int(m.group(1)):02d}", False) if 1 <= int(m.group(1)) <= 12 else (None, True)
    if m := re.fullmatch(r"(\d{4})[-/](\d{1,2})", s):
        return (f"{m.group(1)}-{int(m.group(2)):02d}", False) if 1 <= int(m.group(2)) <= 12 else (None, True)
    if m := re.fullmatch(rf"{_SEASON}\s+(\d{{4}})", s):
        return m.group(1), True
    if re.fullmatch(r"\d{4}", s):
        return s, True
    return None, True


def find_range(text: str) -> DateRange | None:
    m = RANGE.search(text)
    if not m:
        return None
    start, a = point(m["start"])
    current = bool(re.fullmatch(_NOW, m["end"].strip(), re.I))
    end, b = (None, False) if current else point(m["end"])
    return DateRange(start, end, current, m.group(0), m.span(), needs_review=a or b or start is None)


def find_single(text: str) -> DateRange | None:
    m = SINGLE.search(text)
    if not m:
        return None
    value, approx = point(m["point"])
    return DateRange(value, value, False, m.group(0), m.span(), needs_review=approx or value is None)


def find_any(text: str) -> DateRange | None:
    return find_range(text) or find_single(text)


def is_duration(text: str) -> bool:
    """"2 years 3 months" (LinkedIn's total time at one employer)."""
    return bool(DURATION.match(text.strip()))
