"""PARSE_LINKEDIN (specs/18 §4.3 item 23): LinkedIn's "Save to PDF" export without an LLM call.

The export has a fixed layout. Left sidebar: Contact, Top Skills, Languages, Certifications, Honors-Awards,
Publications. Main column: name, headline, location, Summary, Experience, Education. In Experience each entry is
an employer line (largest), a title line, a date line ("July 2022 - June 2023 (1 year)"), an optional location
(grey) and the description; several roles at one employer share its line (with a total like "2 years 1 month").
Sizes are read from the document, not hard-coded: the tiers in the Experience section give employer > title >
body. A document that does not look like this export, or whose entries cannot all be read, goes to the LLM.
"""
from __future__ import annotations

import re

from extraction import schemas
from extraction.parse import dates
from extraction.parse.claims import Vocabulary, award_sentences, find_names, guess_kind, split_skills_text
from extraction.parse.common import ParseResult, Report, Timer, gate, line_role, src, states_use
from ingest.layout import contrast, logical_lines, read_pdf

SIDEBAR = {"contact": "contact", "top skills": "skills", "skills": "skills", "languages": "languages",
           "certifications": "certifications", "licenses & certifications": "certifications",
           "honors-awards": "awards", "honors & awards": "awards", "honors and awards": "awards",
           "publications": "publications", "patents": "other", "projects": "other"}
MAIN = {"summary": "summary", "about": "summary", "experience": "experience", "education": "education",
        "licenses & certifications": "certifications", "certifications": "certifications",
        "volunteer experience": "other", "volunteering": "other", "projects": "other", "honors & awards": "awards",
        "honors-awards": "awards", "publications": "publications", "skills": "skills", "languages": "other",
        "courses": "other", "organizations": "other", "recommendations": "other", "patents": "other",
        "test scores": "other"}
_ISSUED = re.compile(r"^(?:Issued\s+(?P<date>[A-Za-z]{3,9}\.?\s+\d{4}|\d{4}))?\s*(?:·\s*)?"
                     r"(?:Credential ID\s+(?P<cred>\S+))?$")


def _key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().rstrip(":")).casefold()


def _grey(line, dark: int) -> bool:
    """LinkedIn prints dates' location and secondary text in grey: much lighter than the body colour."""
    return line.color != dark and contrast(line.color, (1.0, 1.0, 1.0)) < 0.6 * contrast(dark, (1.0, 1.0, 1.0))


def _date_line(text: str) -> dates.DateRange | None:
    """A line that is a date range (plus LinkedIn's "(N months)"), not a sentence that contains one."""
    r = dates.find_range(text)
    if r is None:
        return None
    rest = (text[:r.span[0]] + text[r.span[1]:]).strip()
    rest = re.sub(r"^\(.*\)$", "", rest).strip(" ·|,")
    return r if len(rest) <= 3 else None


def parse_linkedin(data: bytes, source: str, vocab: Vocabulary, labeller) -> ParseResult:
    report = Report("linkedin_pdf")
    with Timer(report, labeller):
        return _parse(data, source, vocab, labeller, report)


def _parse(data, source, vocab, labeller, report: Report) -> ParseResult:
    layout = read_pdf(data)
    lines = logical_lines(layout, source)
    if layout.hidden:
        report.notes.append(f"{len(layout.hidden)} hidden span(s) ignored")
    side = [ln for ln in lines if ln.column == 1]
    main = [ln for ln in lines if ln.column != 1]
    if not main:
        report.fail("not a LinkedIn Save to PDF layout (no main column)")
        return ParseResult(None, report)
    sizes = sorted({round(ln.size, 1) for ln in main}, reverse=True)
    name_size = sizes[0]
    head_size = next((s for s in sizes[1:] if any(_key(ln.text) in MAIN and round(ln.size, 1) == s for ln in main)), None)
    side_body = max({round(ln.size, 1) for ln in side} or {0}, key=lambda s: sum(round(ln.size, 1) == s for ln in side))
    side_heads = [ln for ln in side if ln.size > side_body + 1 and _key(ln.text) in SIDEBAR]
    recognised = head_size is not None and round(main[0].size, 1) == name_size and \
        (layout.footers >= 1 or side_heads) and any(_key(ln.text) in MAIN for ln in main)
    if not recognised:
        report.fail("not a LinkedIn Save to PDF layout")
        return ParseResult(None, report)
    if any(_date_line(ln.text) for ln in side):
        report.fail("reading order looks scrambled (dates in the sidebar)")

    out = schemas.ProfileExtract(roles=[], projects=[], claims=[], certifications=[])
    methods: dict[int, str] = {}
    unclear: list = []                   # (key, line, before, after)

    def claim(kind: str, name: str, where: str, method: str = "parser", bullet_ref: str | None = None):
        if any(c.name.casefold() == name.casefold() and c.kind == kind for c in out.claims):
            return
        c = schemas.Claim(kind=kind, name=name, bullet_ref=bullet_ref, source=src(name, where))
        out.claims.append(c)
        methods[id(c)] = method

    # --- Sidebar --------------------------------------------------------------------------------------------
    section = None
    for ln in side:
        if ln in side_heads:
            section = SIDEBAR[_key(ln.text)]
            continue
        if section == "skills":
            for n in split_skills_text(ln.text, vocab) or [ln.text]:
                claim(guess_kind(n, vocab), n, "top skills")
        elif section == "certifications":
            m = _ISSUED.match(ln.text)
            if out.certifications and m and (m["date"] or m["cred"]):
                prev = out.certifications[-1]
                out.certifications[-1] = prev.model_copy(update={
                    "date": prev.date or (dates.point(m["date"])[0] if m["date"] else None),
                    "credential_id": prev.credential_id or m["cred"]})
                methods[id(out.certifications[-1])] = "parser"
                continue
            c = schemas.Certification(name=ln.text, issuer=None, date=None, credential_id=None, verify_url=None,
                                      source=src(ln.text, "certifications"))
            out.certifications.append(c)
            methods[id(c)] = "parser"
        elif section == "awards":
            claim("award", ln.text, "honors-awards")
        elif section == "publications":
            p = schemas.Project(kind="publication", name=ln.text, description=None, url=None, start=None, end=None,
                                role_index=None, bullets=[], source=src(ln.text, "publications"))
            out.projects.append(p)
            methods[id(p)] = "parser"

    # --- Main column ------------------------------------------------------------------------------------------
    parts: dict[str, list] = {}
    section = "header"
    for ln in main[1:]:
        if round(ln.size, 1) == head_size and _key(ln.text) in MAIN:
            section = MAIN[_key(ln.text)]
            parts.setdefault(section, [])
            continue
        parts.setdefault(section, []).append(ln)
    dark = max({ln.color for ln in main}, key=lambda c: sum(x.color == c for x in main))

    summary = " ".join(ln.text for ln in parts.get("summary", []))
    for n in split_skills_text(summary, vocab, labelled_only=True):
        claim(guess_kind(n, vocab), n, "summary")
    for a in award_sentences(summary):
        claim("award", a, "summary")

    exp = parts.get("experience", [])
    roles = _experience(exp, dark, report, unclear)
    names_asks, names = [], []
    for i, (title, employer, rng, bullets) in enumerate(roles):
        if rng is None or rng.start is None:
            report.fail(f"role '{title.text}' has no dates that parse")
        r = schemas.Role(title=title.text, employer=employer.text if employer else None, industry=None,
                         start=rng.start if rng else None, end=rng.end if rng else None,
                         is_current=bool(rng and rng.is_current), team_size=None,
                         bullets=[schemas.Bullet(text=b, source=src(b, f"role {i + 1}, bullet {j + 1}"))
                                  for j, b in enumerate(bullets)], source=src(title.text, f"role {i + 1}"))
        out.roles.append(r)
        methods[id(r)] = "parser"
        for b in r.bullets:
            methods[id(b)] = "parser"
            for f in find_names(b.text, vocab):
                key = f"uses_{len(names)}"
                names.append((key, f, b))
                names_asks.append(states_use(key, f.text, b.text))
    for sec in ("certifications", "awards", "publications"):
        for ln in parts.get(sec, []):
            if sec == "awards":
                claim("award", ln.text, "honors & awards")
            elif sec == "publications":
                p = schemas.Project(kind="publication", name=ln.text, description=None, url=None, start=None,
                                    end=None, role_index=None, bullets=[], source=src(ln.text, "publications"))
                out.projects.append(p)
                methods[id(p)] = "parser"
            else:
                c = schemas.Certification(name=ln.text, issuer=None, date=None, credential_id=None, verify_url=None,
                                          source=src(ln.text, "certifications"))
                out.certifications.append(c)
                methods[id(c)] = "parser"
    for ln in parts.get("skills", []):
        for n in split_skills_text(ln.text, vocab):
            claim(guess_kind(n, vocab), n, "skills")

    asks = [line_role(k, ln.text, before, after) for k, ln, before, after in unclear]
    answers = labeller.ask(asks + names_asks) if (asks or names_asks) else {}
    resolved = [k for k, *_ in unclear if answers.get(k) is not None and answers[k].trusted
                and answers[k].label in ("bullet", "other", "location", "education")]
    gate(report, answers, [k for k, *_ in unclear if k not in resolved])
    for key, f, b in names:
        a = answers.get(key)
        if a is not None and a.trusted and a.label == "yes":
            claim(guess_kind(f.text, vocab), f.text, b.source.location, "jev", bullet_ref=b.source.location)
    report.items = {"roles": len(out.roles), "bullets": sum(len(r.bullets) for r in out.roles),
                    "projects": len(out.projects), "claims": len(out.claims), "certifications": len(out.certifications)}
    return ParseResult(out if report.ok else None, report, methods)


def _experience(exp: list, dark: int, report: Report, unclear: list):
    """[(title line, employer line, date range, description bullets)] from the Experience section's lines."""
    if not exp:
        return []
    body = [ln for ln in exp if not _grey(ln, dark)]
    tiers = sorted({round(ln.size, 1) for ln in body}, reverse=True)
    emp_size = tiers[0]
    date_at = [i for i, ln in enumerate(exp) if _date_line(ln.text) and not _grey(ln, dark)]
    if not date_at:
        report.fail("Experience has entries but no date lines")
        return []
    headers: dict[int, tuple] = {}       # date index → (title index, employer index | None)
    employer_idx = None
    for k in date_at:
        t = k - 1
        if t < 0 or t in date_at:
            report.fail("an Experience entry has dates but no title")
            continue
        e = None
        if t - 1 >= 0 and round(exp[t - 1].size, 1) == emp_size and not _grey(exp[t - 1], dark) and \
                round(exp[t].size, 1) < emp_size:
            e = t - 1
        elif t - 2 >= 0 and dates.is_duration(exp[t - 1].text) and round(exp[t - 2].size, 1) == emp_size:
            e = t - 2                                     # several roles at one employer: "Employer / 2 years / Title"
        if e is not None:
            employer_idx = e
        elif employer_idx is None:
            unclear.append((f"line_{t}", exp[t], "", exp[k].text))
        if len(tiers) >= 3 and round(exp[t].size, 1) == tiers[-1]:
            unclear.append((f"line_{t}", exp[t], exp[t - 1].text if t else "", exp[k].text))   # body-sized title
        headers[k] = (t, employer_idx)
    out = []
    starts = sorted(headers)
    first_header = min(e if e is not None else t for t, e in headers.values())
    for j, k in enumerate(starts):
        t, e = headers[k]
        lo = k + 1
        if lo < len(exp) and _grey(exp[lo], dark) and not _date_line(exp[lo].text):
            lo += 1                                       # the location line
        hi = len(exp)
        if j + 1 < len(starts):
            nt, ne = headers[starts[j + 1]]
            hi = ne if ne is not None and ne > k else nt
        bullets = []
        for ln in exp[lo:hi]:
            if dates.is_duration(ln.text):
                continue
            bullets.append(ln.text)
        out.append((exp[t], exp[e] if e is not None else None, _date_line(exp[k].text), bullets))
    for i in range(first_header):
        unclear.append((f"line_{i}", exp[i], "", exp[i + 1].text if i + 1 < len(exp) else ""))
    return out
