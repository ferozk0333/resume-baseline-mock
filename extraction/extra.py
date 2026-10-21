"""Display-only proof types read from a document's text by code (specs/19 Phase D): education, patents,
volunteering, courses and spoken languages. No model call. Each item keeps the lines it came from as its quote.

These are never part of the scored profile (extraction.records.Profile): they are shown in Review and to recruiters
with "Shown to recruiters, not scored". The candidate can edit, delete or add them like any other card.
"""
from __future__ import annotations

import re

from core.text import normalize_name
from extraction.parse import dates
from extraction.parse.sections import VARIANTS
from extraction.records import ExtraItem

HEADINGS: dict[str, set[str]] = {
    "education": VARIANTS["education"],
    "language": VARIANTS["languages"],
    "volunteering": VARIANTS["volunteering"] | {"volunteer work", "volunteering experience"},
    "course": VARIANTS["coursework"] | {"courses", "relevant courses", "online courses", "training", "trainings"},
    "patent": {"patents", "patent", "patents and inventions"},
}
_ALL = {normalize_name(v) for vs in VARIANTS.values() for v in vs} | \
    {normalize_name(v) for vs in HEADINGS.values() for v in vs}
_LOOKUP = {normalize_name(v): t for t, vs in HEADINGS.items() for v in vs}
_SCHOOL = re.compile(r"\b(university|college|institute|school|academy|polytechnic|universit[éa]t|iit|mit)\b", re.I)
_DEGREE = re.compile(r"^\s*(b\.?\s?s\.?c?|m\.?\s?s\.?c?|b\.?\s?a\.?|m\.?\s?a\.?|b\.?\s?tech|m\.?\s?tech|b\.?\s?e\.?|"
                     r"m\.?\s?e\.?|ph\.?\s?d\.?|mba|m\.?\s?eng|b\.?\s?eng|"
                     r"(?:bachelor|master)(?:'s)? of business administration|bachelor(?:'s)?(?: of [a-z]+)?|"
                     r"master(?:'s)?(?: of [a-z]+)?|doctor(?:ate)?(?: of [a-z]+)?|associate(?:'s)?(?: of [a-z]+)?|"
                     r"diploma|certificate)\b\.?", re.I)
_SEP = re.compile(r"\s+[·|•]\s+|\s+[–—-]\s+|,\s+")
_PAGE_FOOTER = re.compile(r"page\s+\d+(?:\s+of\s+\d+)?", re.I)      # a PDF page footer ("Page 1", "Page 1 of 2")

# The same degree written differently ("B.S." and LinkedIn's "Bachelor of Science"): one key, for matching only. The
# text shown is never changed.
_DEGREE_FORMS = {
    "bs": ("bs", "bsc", "bachelor of science"),
    "ba": ("ba", "bachelor of arts"),
    "ms": ("ms", "msc", "master of science"),
    "ma": ("ma", "master of arts"),
    "mba": ("mba", "master of business administration", "master of business"),   # older reads stopped at "Business"
    "phd": ("phd", "doctor of philosophy"),
    "btech": ("btech", "be", "beng", "bachelor of technology", "bachelor of engineering"),
    "mtech": ("mtech", "me", "meng", "master of technology", "master of engineering"),
}
_DEGREE_KEY = {form: key for key, forms in _DEGREE_FORMS.items() for form in forms}


def degree_key(text: str | None) -> str:
    """"B.S.", "BSc" and "Bachelor's of Science" → "bs". Unknown degrees: the normalized text, as before."""
    t = normalize_name(re.sub(r"[’']s\b", "", text or ""))
    return _DEGREE_KEY.get(t.replace(" ", ""), _DEGREE_KEY.get(t, t))


_PROFICIENCY = re.compile(r"\b(native|bilingual|fluent|full professional|professional working|limited working|"
                          r"elementary|conversational|basic|intermediate|advanced|proficient|c1|c2|b1|b2|a1|a2)\b", re.I)


def _heading(line: str) -> str | None:
    """The section a line opens ("## Education", "EDUCATION", "Education:"), or None for a body line."""
    t = normalize_name(re.sub(r"^#+\s*|[:|]+\s*$", "", line.strip()))
    if not t or len(t.split()) > 6:
        return None
    return _LOOKUP.get(t, "other" if t in _ALL else None)


def _sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lead = re.match(r"^([A-Za-z &/-]{3,40}):\s+\S", line)      # "Skills: Python, SQL" ends the section
        if current and lead and _heading(lead.group(1)) is not None:
            current = None
            continue
        h = _heading(line)
        if h is not None or line.startswith("#"):
            current = h if h in HEADINGS else None
            continue
        if current:
            out.setdefault(current, []).append(line)
    return out


def _bullet(line: str) -> bool:
    return bool(re.match(r"^[-*•]\s+", line))


def _plain(line: str) -> str:
    return re.sub(r"^[-*•]\s+", "", line).strip()


def _dates_of(text: str) -> tuple[str, dict]:
    r = dates.find_range(text) or dates.find_single(text)
    if r is None:
        return text, {}
    rest = (text[:r.span[0]] + text[r.span[1]:]).strip(" ·|,–—-()")
    fields = {"start": r.start or "", "end": "Present" if r.is_current else (r.end or "")}
    if r.start == r.end:
        fields = {"end": r.end or ""}
    return re.sub(r"\s{2,}", " ", rest), {k: v for k, v in fields.items() if v}


def _entries(lines: list[str], starts) -> list[list[str]]:
    """Group a section's lines into entries: a new entry starts at a non-bullet line that `starts` accepts."""
    groups: list[list[str]] = []
    for ln in lines:
        if not groups or (not _bullet(ln) and starts(ln, groups[-1])):
            groups.append([ln])
        else:
            groups[-1].append(ln)
    return groups


def _education(lines: list[str]) -> list[ExtraItem]:
    lines = [ln for ln in lines if not _PAGE_FOOTER.fullmatch(_plain(ln))]
    has_school = any(_SCHOOL.search(ln) for ln in lines)

    def starts(ln, group):
        if not has_school:
            return True
        return bool(_SCHOOL.search(ln)) and any(_SCHOOL.search(x) for x in group)
    out = []
    for group in _entries(lines, starts):
        fields: dict[str, str] = {}
        notes = []
        for ln in group:
            text, d = _dates_of(_plain(ln))
            fields.update({k: v for k, v in d.items() if k not in fields})
            for part in (p.strip(" ·|,") for p in _SEP.split(text) if p.strip(" ·|,")):
                if _SCHOOL.search(part) and "school" not in fields:
                    fields["school"] = part
                elif (m := _DEGREE.match(part)) and "degree" not in fields:
                    fields["degree"] = m.group(0).strip()
                    rest = part[m.end():].strip(" ,-–—")
                    rest = re.sub(r"^(?:in|of)\s+", "", rest, flags=re.I)
                    if rest:
                        fields["field"] = rest
                elif "degree" in fields and re.fullmatch(r"[A-Z]{2,5}", part):
                    continue                                    # LinkedIn's "Master of Science - MS"
                elif "degree" in fields and "field" not in fields and not re.search(r"\d", part):
                    fields["field"] = part
                elif "school" not in fields and not has_school and "degree" in fields:
                    fields["school"] = part
                else:
                    notes.append(part)
        if notes:
            fields["notes"] = " · ".join(notes)
        if fields:
            out.append(ExtraItem("education", fields, quote="\n".join(group)))
    return out


def _languages(lines: list[str]) -> list[ExtraItem]:
    out = []
    for ln in lines:
        for part in (p.strip() for p in re.split(r"[,;·|]|\s+[–—]\s+(?=[A-Z][a-z]+\s*\()", _plain(ln)) if p.strip()):
            m = re.match(r"^([A-Za-zÀ-ÿ' ]{2,30}?)\s*(?:\((.*?)\)|[:–—-]\s*(.+))?$", part)
            if not m:
                continue
            fields = {"language": m.group(1).strip()}
            prof = (m.group(2) or m.group(3) or "").strip()
            if prof:
                fields["proficiency"] = prof
            if len(fields["language"].split()) <= 3:
                out.append(ExtraItem("language", fields, quote=ln))
    return out


def _courses(lines: list[str]) -> list[ExtraItem]:
    out = []
    for ln in lines:
        text, d = _dates_of(_plain(ln))
        parts = [p.strip() for p in re.split(r",\s+|;\s+", text) if p.strip()] if not d else [text]
        for part in parts:
            name, _, provider = part.partition(" — ") if " — " in part else part.partition(" · ")
            fields = {"name": name.strip(), **({"provider": provider.strip()} if provider.strip() else {}),
                      **({"date": d.get("end") or d.get("start")} if d else {})}
            out.append(ExtraItem("course", fields, quote=ln))
    return out


def _with_details(lines: list[str], type_: str, head_key: str, body_key: str,
                  second_key: str | None = None) -> list[ExtraItem]:
    out = []
    for group in _entries(lines, lambda ln, g: True):
        head, d = _dates_of(_plain(group[0]))
        second = ""
        if second_key and (parts := re.split(r"\s+[–—]\s+|\s+-\s+|,\s+", head, maxsplit=1)) and len(parts) == 2:
            head, second = parts[0].strip(), parts[1].strip(" ,")
        fields = {head_key: head, **({second_key: second} if second else {}), **d}
        body = " ".join(_plain(x) for x in group[1:])
        if body:
            fields[body_key] = body
        if head:
            out.append(ExtraItem(type_, fields, quote="\n".join(group)))
    return out


def extra_items(text: str, evidence_id: str | None, role_names: set[str] | None = None) -> list[ExtraItem]:
    """Every display-only item a document's text names. `role_names` (normalized employers of scored roles): a
    volunteering entry already read as a role is not shown twice."""
    secs = _sections(text or "")
    items: list[ExtraItem] = []
    items += _education(secs.get("education", []))
    items += _languages(secs.get("language", []))
    items += _courses(secs.get("course", []))
    items += _with_details(secs.get("patent", []), "patent", "title", "notes")
    for v in _with_details(secs.get("volunteering", []), "volunteering", "organization", "description", "role"):
        org = normalize_name(v.fields.get("organization"))
        if not any(r and (r in org or org in r) for r in (role_names or set())):
            items.append(v)
    for x in items:
        x.evidence_id = evidence_id
    return items
