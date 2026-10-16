"""Reading a job description (specs/14 §4): one JOB_EXTRACT_MODEL call, then code checks.

Same rules as candidate extraction: the JD text is data inside tags (instructions in it are ignored), every item
carries a quote that must be found in the text, nothing is invented. Code then:
- splits flat lists with the shared rules (`split_claim_names`) and trims qualifiers ("basic CI/CD" → "CI/CD");
- decides required vs preferred from the section the quote sits in ("Minimum/Required" → required,
  "Preferred/Nice to have" → preferred), keeping the model's choice only when no heading says;
- drops anything from salary, benefits, EEO, team size or "how to apply" text;
- normalizes years ("3+ years" → 3, "2–4 years" → 2, "new grad" / "0–1" → 0) and maps the level.
When the model or its quota is unavailable, `read_job` returns no fields and a notice; the recruiter fills the
form by hand next to the JD text. Creation is never blocked.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from core import config
from core.llm import LLMError
from core.models import REACH_LABELS
from core.text import wrap_tag
from extraction.extract import load_prompt
from extraction.validate import QuoteIndex, split_claim_names

log = logging.getLogger(__name__)


class Quoted(BaseModel):
    text: str
    quote: str = Field(description="The exact sentence or line of the JD this comes from.")


class JDRequirement(BaseModel):
    name: str = Field(description="One skill, tool or certification, short (e.g. 'Python', 'Docker', 'RAG').")
    kind: Literal["skill", "tool", "certification"]
    priority: Literal["required", "preferred"]
    aliases: list[str] = Field(default_factory=list, description="Other names for the same thing, from the JD or "
                                                                  "common spelling variants. No broader terms.")
    description: str | None = Field(None, description="Short description in the JD's words, or null.")
    quote: str = Field(description="The exact line of the JD this requirement comes from.")


class JDReach(BaseModel):
    label: Literal["own_work", "team", "cross_functional", "organization", "external"]
    quote: str


class JobExtract(BaseModel):
    title: Quoted | None = None
    company: Quoted | None = None
    department: Quoted | None = None
    location: Quoted | None = None
    workplace: Literal["on_site", "hybrid", "remote"] | None = None
    workplace_quote: str | None = None
    employment_type: Quoted | None = None
    level: Quoted | None = Field(None, description="The seniority as stated, e.g. 'Early career', 'Senior'.")
    years: Quoted | None = Field(None, description="The minimum experience statement, e.g. '1+ years of ...'.")
    short_description: str | None = Field(None, description="Two sentences for candidates, from the JD's own words.")
    requirements: list[JDRequirement] = Field(default_factory=list)
    responsibilities: list[Quoted] = Field(default_factory=list)
    scope: Quoted | None = Field(None, description="What this person will own (draft, from the JD).")
    complexity: Quoted | None = Field(None, description="What makes the work hard (draft, from the JD).")
    reach: JDReach | None = None


REACH = {"own_work": 1, "team": 2, "cross_functional": 3, "organization": 4, "external": 5}
LEVELS = ("entry", "mid", "senior", "lead")
EXCLUDED_TEXT = re.compile(
    r"\$|€|£|\bsalary\b|\bequity\b|\b401\s*\(k\)|\bmedical\b|\bdental\b|\bvision coverage\b|\btime off\b|\bholidays?\b|"
    r"\blearning budget\b|\bequal opportunity\b|\bdiscriminat\w*|\bprotected characteristic|\bhow to apply\b|"
    r"\bapply with\b|\b\d+\s+(?:engineers|people|employees)\b|\bteam of \d+|\b\d+-person\b|\bvisa sponsorship\b", re.I)
HEADINGS = {
    "required": re.compile(r"^(?:#+\s*)?(minimum|required|basic|must[- ]have|requirements|qualifications|what you(?:'|’)ll need"
                           r"|you have|who you are)\b.*$", re.I),
    "preferred": re.compile(r"^(?:#+\s*)?(preferred|nice[- ]to[- ]have|bonus|pluses|extra credit|good to have)\b.*$", re.I),
    "excluded": re.compile(r"^(?:#+\s*)?(what we offer|benefits|perks|compensation|salary|how to apply|equal opportunity|"
                           r"eeo|about (?:us|the company)|our values)\b.*$", re.I),
    "other": re.compile(r"^(?:#+\s*)?(what you(?:'|’)ll do|responsibilities|about the role|the role|what success looks like|"
                        r"about \w+|team|level|reports to|day to day)\b.*$", re.I),
}
_QUALIFIERS = re.compile(r"^(?:basic|strong|solid|good|working|hands-on|deep|some|familiarity with|knowledge of|"
                         r"experience (?:with|in)|proficiency (?:with|in)|understanding of)\s+", re.I)


def parse_years(text: str | None) -> float | None:
    """'3+ years' → 3, '2–4 years' → 2, 'new grad' / '0–1' → 0, 'one year' → 1. None if no number."""
    if not text:
        return None
    t = text.lower().replace("–", "-").replace("—", "-")
    if re.search(r"\b(new grad|recent graduate|no experience|graduate program|entry[- ]level with no)\b", t):
        return 0.0
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ten": 10}
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:\+|-\s*\d+|to\s+\d+|or more)?\s*(?:years?|yrs?)", t) or \
        re.search(r"\b(one|two|three|four|five|six|seven|eight|ten)\b\s*(?:\+|or more)?\s*years?", t)
    if not m:
        return None
    v = m.group(1)
    return float(words.get(v, v))


def map_level(text: str | None, years: float | None) -> str:
    """Stated level first ('early career' → entry, 'senior' → senior …), else from the years."""
    t = (text or "").lower()
    rules = [("lead", r"\b(lead|staff|principal|head of|director)\b"), ("senior", r"\b(senior|sr\.?|iii)\b"),
             ("mid", r"\b(mid|intermediate|ii)\b"),
             ("entry", r"\b(entry|early[- ]career|junior|jr\.?|new grad|graduate|associate|intern|i)\b")]
    for level, pattern in rules:
        if re.search(pattern, t):
            return level
    if years is None:
        return "entry"
    return "entry" if years <= config.ENTRY_LEVEL_MAX_YEARS else "mid" if years <= 5 else "senior" if years <= 8 else "lead"


def sections(text: str) -> list[tuple[int, str]]:
    """(offset, kind) for each heading line: required | preferred | excluded | other."""
    out, pos = [], 0
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s and len(s) <= 60 and not s.startswith(("-", "*", "•")) and not s.endswith((".", ",", ";")):
            for kind, rx in HEADINGS.items():
                if rx.match(s):
                    out.append((pos, kind))
                    break
        pos += len(line)
    return out


def position(text: str, quote: str) -> int | None:
    """Where a quote starts in the text, tolerant of spacing, dashes and punctuation (first 8 words)."""
    words = re.findall(r"\w+", quote)[:8]
    if not words:
        return None
    m = re.search(r"\W+".join(map(re.escape, words)), text, re.I)
    return m.start() if m else None


def section_of(text: str, quote: str, heads: list[tuple[int, str]]) -> str | None:
    at = position(text, quote)
    if at is None:
        return None
    before = [k for off, k in heads if off <= at]
    return before[-1] if before else None


def clean_name(name: str) -> str:
    """Drop qualifiers a list item carries ("basic CI/CD" → "CI/CD", "working knowledge of Git" → "Git")."""
    name, prev = name.strip(), None
    while name != prev:
        prev, name = name, _QUALIFIERS.sub("", name).strip(" .;:")
    return name


def check(raw: JobExtract, text: str) -> dict:
    """Quote check, splitting, sections and exclusions. Returns the draft fields plus what was dropped and why."""
    index = QuoteIndex(text)
    heads = sections(text)
    dropped: list[dict] = []

    def ok(item: Quoted | None, what: str) -> str | None:
        if item is None or not item.text.strip():
            return None
        if not index.contains(item.quote):
            dropped.append({"item": what, "text": item.text, "reason": "quote not found in the JD"})
            return None
        return item.text.strip()

    reqs, seen = [], {}
    for r in raw.requirements:
        if not index.contains(r.quote):
            dropped.append({"item": "requirement", "text": r.name, "reason": "quote not found in the JD",
                            "quote": r.quote})
            continue
        where = section_of(text, r.quote, heads)
        if where == "excluded" or EXCLUDED_TEXT.search(r.quote) or EXCLUDED_TEXT.search(r.name):
            dropped.append({"item": "requirement", "text": r.name, "reason": "salary, benefits, EEO, team size or "
                                                                             "how-to-apply text is never a requirement"})
            continue
        priority = {"required": "required", "preferred": "preferred"}.get(where or "", r.priority)
        names = [r.name] if r.kind == "certification" else split_claim_names(r.name)
        for n in names:
            n = clean_name(n) if r.kind != "certification" else n.strip()
            if not n:
                continue
            key = n.casefold()
            if key in seen:
                prev = reqs[seen[key]]
                if prev["priority"] == "preferred" and priority == "required":
                    prev["priority"] = "required"
                continue
            seen[key] = len(reqs)
            aliases = [a.strip() for a in r.aliases if a.strip() and a.strip().casefold() != key][:6]
            reqs.append({"name": n, "kind": r.kind, "priority": priority, "aliases": aliases,
                         "suggested_aliases": aliases, "description": r.description, "from_jd": True, "quote": r.quote,
                         "section": where})
    # One kind per name (a name is a skill or a tool, never both): keep the first.
    years_text = ok(raw.years, "years")
    years_quote = raw.years.quote if years_text else None
    years = parse_years(years_quote or years_text) if years_text else None
    if years is None and raw.level and index.contains(raw.level.quote):
        years = parse_years(raw.level.quote)
    level_text = ok(raw.level, "level")
    level = map_level(level_text, years)
    reach = None
    if raw.reach:
        if index.contains(raw.reach.quote):
            reach = {"value": REACH[raw.reach.label], "label": REACH_LABELS[REACH[raw.reach.label]], "quote": raw.reach.quote}
        else:
            dropped.append({"item": "reach", "text": raw.reach.label, "reason": "quote not found in the JD"})
    workplace = raw.workplace if raw.workplace and raw.workplace_quote and index.contains(raw.workplace_quote) else None
    details = {"title": ok(raw.title, "title"), "company": ok(raw.company, "company"),
               "department": ok(raw.department, "department"), "location": ok(raw.location, "location"),
               "workplace": workplace, "employment_type": ok(raw.employment_type, "employment type"),
               "level": level, "min_years": years if years is not None else (0.0 if level == "entry" else None),
               "short_description": raw.short_description}
    quotes = {k: getattr(raw, k).quote for k in ("title", "company", "department", "location", "employment_type")
              if details.get(k) and getattr(raw, k) is not None}
    if level_text:
        quotes["level"] = raw.level.quote
    if years_text:
        quotes["min_years"] = raw.years.quote
    if workplace:
        quotes["workplace"] = raw.workplace_quote
    good = {"scope_expectation": ok(raw.scope, "scope"), "complexity_criteria": ok(raw.complexity, "complexity"),
            "expected_reach": reach["value"] if reach else None}
    good_quotes = {"scope_expectation": raw.scope.quote if good["scope_expectation"] else None,
                   "complexity_criteria": raw.complexity.quote if good["complexity_criteria"] else None,
                   "expected_reach": reach["quote"] if reach else None}
    resp = [{"text": r.text, "quote": r.quote} for r in raw.responsibilities if index.contains(r.quote)]
    return {"details": details, "detail_quotes": quotes, "requirements": reqs, "good": good,
            "good_quotes": good_quotes, "responsibilities": resp, "dropped": dropped}


def read_job(llm, text: str) -> dict:
    """One JOB_EXTRACT_MODEL call + code checks. {"status": "ok", ...} or {"status": "unavailable", "notice"}."""
    if llm is None:
        return {"status": "unavailable", "notice": "Reading job descriptions needs a model (JOB_EXTRACT_MODEL). "
                                                    "Fill in the form; the job description is on the right to copy from."}
    try:
        raw = llm.parse(model=config.JOB_EXTRACT_MODEL, system=load_prompt("job_extract_system"),
                        user=wrap_tag("job_description", text), output_type=JobExtract)
    except Exception as exc:                            # noqa: BLE001 - any failure falls back to the manual form
        if not isinstance(exc, LLMError):
            log.exception("job description reading failed")
        log.warning("job description model unavailable: %s", exc)
        return {"status": "unavailable", "notice": f"The job description could not be read automatically ({exc}). "
                                                    "Fill in the form; the job description is on the right to copy from."}
    return {"status": "ok", "model": config.JOB_EXTRACT_MODEL, **check(raw, text)}
