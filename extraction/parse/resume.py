"""PARSE_RESUME (specs/18 §4.3 item 24), shadow mode only for now: runs after the LLM extraction, is compared with
it and logged; never shown and never scored.

Shared blocks: layout reader → sections → items (header lines, then bullets) → dates, claims. Unclear header lines
go to Jev; skills inside bullets are vocabulary names Jev confirms, plus one small LLM call per document for bullets
with no vocabulary name (item 18c; only when PARSE_SHADOW_CALLS allows calls). The gate falls back when a required
section is missing, a role has no dates, too many lines stay unclear, or reading order looks scrambled.
"""
from __future__ import annotations

import re

from extraction import schemas
from extraction.parse import dates, sections
from extraction.parse.claims import Vocabulary, award_sentences, find_names, guess_kind, split_skills_text
from extraction.parse.common import ParseResult, Report, Timer, gate, line_role, src, states_use
from ingest.layout import logical_lines, read_layout

TITLE_WORDS = {"engineer", "scientist", "analyst", "manager", "intern", "developer", "assistant", "associate",
               "consultant", "lead", "director", "researcher", "specialist", "coordinator", "representative",
               "officer", "designer", "architect", "administrator", "fellow", "member", "head", "founder", "co-founder",
               "owner", "president", "vp", "cto", "ceo", "programmer", "technician", "trainee", "apprentice", "tutor",
               "instructor", "teacher", "professor", "lecturer", "advisor", "strategist", "editor", "writer",
               "producer", "operator", "agent", "clerk", "supervisor", "principal", "staff", "contractor",
               "freelancer", "freelance", "volunteer", "ambassador", "mentor", "partner", "executive", "student",
               "postdoc", "scholar", "steward", "secretary", "coach", "organizer", "chair", "recruiter", "planner",
               "marketer", "owner", "tester", "auditor", "accountant", "economist", "statistician", "nurse", "medic"}
AWARD = re.compile(r"\b(award|awarded|winner|won|finalist|scholarship|fellowship|prize|honou?r|medal|dean'?s list|"
                   r"runner[- ]up|1st place|first place|2nd place|second place|top \d+|ranked|recipient)\b", re.I)
_PLACES = ("United States|USA|U\\.S\\.|India|Canada|United Kingdom|UK|Germany|France|Australia|Singapore|"
           "Netherlands|Ireland|Remote|Washington|California|New York|Texas|Oregon|Massachusetts|Illinois|Ontario|"
           "Maharashtra|Karnataka|Telangana|Tamil Nadu|Madhya Pradesh|Delhi|England|Bavaria")
_LOCATION = re.compile(rf"^(?:remote|hybrid|on-?site|[A-Z][a-zA-Z .'-]{{1,30}},\s*(?:[A-Z]{{2}}|{_PLACES})"
                       rf"(?:,\s*(?:{_PLACES}|[A-Z]{{2,3}}))?)$", re.I)
_SPLIT = re.compile(r"\s+[—–|·]\s+|\s+-\s+|\s+at\s+|\s+@\s+")
_LINKS = re.compile(r"\s*\((?:demo |github |live |project )?link\)|\s*\((?:https?://|www\.|github\.com)[^)]*\)", re.I)


def title_score(text: str) -> int:
    words = set(re.findall(r"[a-z][a-z-]+", text.casefold()))
    return len(words & TITLE_WORDS)


def parse_resume(filename: str, data: bytes, source: str, vocab: Vocabulary, labeller) -> ParseResult:
    report = Report("resume")
    with Timer(report, labeller):
        return _parse(filename, data, source, vocab, labeller, report)


def _groups(lines, body: float):
    """(section keys, lines) in reading order; the lines before the first heading are the header (name, contact)."""
    groups = [(["header"], [])]
    for ln in lines:
        keys = sections.is_heading(ln, body)
        if keys:
            groups.append((keys, []))
        else:
            groups[-1][1].append(ln)
    return groups


def _items(lines):
    """Header lines (not bullets) followed by their bullets and description lines."""
    items, cur = [], None
    for ln in lines:
        prose = not ln.bullet and len(ln.text.split()) >= 12 and len(ln.cells) == 1 and \
            not dates.find_any(ln.text) and " · " not in ln.text and ln.text.rstrip().endswith((".", "!", "?")) \
            and cur is not None and cur["head"]
        if ln.bullet or prose:
            if cur is None:
                cur = {"head": [], "body": []}
                items.append(cur)
            cur["body"].append(ln)
        else:
            if cur is None or cur["body"] or len(cur["head"]) >= 3:
                cur = {"head": [], "body": []}
                items.append(cur)
            cur["head"].append(ln)
    return items


def _pieces(head) -> tuple[list[str], dates.DateRange | None]:
    """Header text pieces (cells and separators) and the date range found in them."""
    rng, pieces = None, []
    for ln in head:
        for cell in ln.cells or [ln.text]:
            r = dates.find_range(cell) or (dates.find_single(cell) if re.fullmatch(
                r"(?:\w+\.?\s+)?(?:\w+\.?\s+)?\d{4}", cell.strip()) else None)
            if r is not None and rng is None:
                rng = r
                cell = (cell[:r.span[0]] + cell[r.span[1]:]).strip(" ,·|–—-")
            cell = re.sub(r"^\*+|\*+$", "", cell).strip()
            for p in _SPLIT.split(cell):
                p = p.strip(" ,·|")
                if p:
                    pieces.append(p)
    return pieces, rng


_TRAILING_PLACE = re.compile(r",\s*(?:[A-Z][a-zA-Z .'-]{1,30},\s*[A-Z]{2}|[A-Z][a-zA-Z .'-]{1,30},\s*(?:" + _PLACES
                             + r")|Remote|Hybrid)$")


def _role_parts(pieces: list[str]) -> tuple[str | None, str | None, int]:
    """(title, employer, unclear lines) from header pieces. A comma inside a piece may join title and employer;
    a location after the employer ("Quillstack, Seattle, WA") is not part of it."""
    pieces = [_TRAILING_PLACE.sub("", p).strip() for p in pieces if not _LOCATION.match(p)]
    if not pieces:
        return None, None, 1
    scored = sorted(range(len(pieces)), key=lambda i: -title_score(pieces[i]))
    if title_score(pieces[scored[0]]) == 0:
        if len(pieces) == 1 and "," in pieces[0]:
            a, b = [x.strip() for x in pieces[0].split(",", 1)]
            return a, b, 1
        return pieces[0], pieces[1] if len(pieces) > 1 else None, 1
    t = scored[0]
    title = pieces[t]
    others = [p for i, p in enumerate(pieces) if i != t]
    employer = others[0] if others else None
    if employer is None and "," in title:
        a, b = [x.strip() for x in title.split(",", 1)]
        if title_score(a) and not _LOCATION.match(b):
            title, employer = a, b
        elif title_score(b):
            title, employer = b, a
    unclear = int(len(others) > 1 and title_score(others[0]) > 0)
    return title, employer, unclear


def _parse(filename, data, source, vocab, labeller, report: Report) -> ParseResult:
    layout = read_layout(filename, data)
    lines = logical_lines(layout, source)
    body = layout.body_size
    groups = _groups(lines, body)
    found = {k for keys, _ in groups for k in keys}
    if not found & {"experience", "projects"}:
        report.fail("no Experience or Projects section found")
    out = schemas.ProfileExtract(roles=[], projects=[], claims=[], certifications=[])
    methods: dict[int, str] = {}
    unclear: list = []
    bullets_all: list[tuple[schemas.Bullet, str]] = []

    def claim(kind, name, where, method="parser", bullet_ref=None):
        if any(c.name.casefold() == name.casefold() and c.kind == kind for c in out.claims):
            return
        c = schemas.Claim(kind=kind, name=name, bullet_ref=bullet_ref, source=src(name, where))
        out.claims.append(c)
        methods[id(c)] = method

    for keys, glines in groups:
        key = keys[0]
        if key in ("experience", "projects"):
            for it in _items(glines):
                if not it["head"]:
                    unclear += [(f"line_{id(b)}", b, "", "") for b in it["body"][:1]]
                    continue
                pieces, rng = _pieces(it["head"])
                texts = [b.text for b in it["body"]]
                if key == "experience" and (rng is not None or title_score(" ".join(pieces))):
                    title, employer, n_unclear = _role_parts(pieces)
                    if title is None:
                        report.fail("a role header could not be read")
                        continue
                    for _ in range(n_unclear):
                        h = it["head"][0]
                        unclear.append((f"line_{len(unclear)}", h, "", it["head"][1].text if len(it["head"]) > 1
                                        else ""))
                    if rng is None or rng.start is None:
                        report.fail(f"role '{title}' has no dates that parse")
                    i = len(out.roles) + 1
                    r = schemas.Role(title=title, employer=employer, industry=None, start=rng.start if rng else None,
                                     end=rng.end if rng else None, is_current=bool(rng and rng.is_current),
                                     team_size=None, bullets=[schemas.Bullet(text=t, source=src(
                                         t, f"role {i}, bullet {j + 1}")) for j, t in enumerate(texts)],
                                     source=src(title, f"role {i}"))
                    out.roles.append(r)
                    methods[id(r)] = "parser"
                    bullets_all += [(b, b.source.location) for b in r.bullets]
                else:
                    def only_date_text(t):
                        r = dates.find_any(t)
                        return r is not None and len(r.raw) >= len(t.strip()) - 3
                    hl = next((h for h in it["head"] if not only_date_text(h.text)), it["head"][0])
                    cells = hl.cells or [hl.text]
                    lead = hl.bold_lead
                    only_date = dates.find_any(lead) is not None and len(dates.find_any(lead).raw) >= len(lead) - 3
                    if lead and not hl.bold and len(lead) >= 3 and not only_date:
                        cells = [lead]                    # the bold name before its description or dates
                    # In a table the first cell may be the dates: the name is the first cell that is not one.
                    raw = next((c for c in cells if not (dates.find_any(c) and len(dates.find_any(c).raw) >= len(c) - 3)),
                               cells[0])
                    # The name runs to the first "·" or "|", link or date ("Name · Sep 2025 · (github.com/…)").
                    name = re.split(r"\s+[·|]\s+|\s*\((?:demo |github |live )?link\)|\s*\((?:https?://|www\.|"
                                    r"github\.com)", raw, flags=re.I)[0]
                    r = dates.find_any(name)
                    if r is not None and r.span[1] >= len(name.rstrip()) - 1 and \
                            re.search(r"[·|,–—(-]\s*$", name[:r.span[0]]):
                        name = name[:r.span[0]]           # "Name · Sep 2025"; a year in a name stays ("Hackathon 2025")
                    name = name.strip(" *—–-|,")
                    i = len(out.projects) + 1
                    p = schemas.Project(kind="project", name=name, description=None, url=None,
                                        start=rng.start if rng else None, end=rng.end if rng else None,
                                        role_index=None, bullets=[schemas.Bullet(text=t, source=src(
                                            t, f"project {i}, bullet {j + 1}")) for j, t in enumerate(texts)],
                                        source=src(name, f"project {i}"))
                    out.projects.append(p)
                    methods[id(p)] = "parser"
                    bullets_all += [(b, b.source.location) for b in p.bullets]
        elif key == "skills":
            text = "\n".join(ln.text for ln in glines)
            for n in split_skills_text(text, vocab):
                claim(guess_kind(n, vocab), n, "skills section")
        elif key in ("certifications", "publications") and "awards" not in keys:
            for ln in glines:
                _cert_or_pub(ln, key, out, methods)
        elif key == "summary":
            summary = " ".join(ln.text for ln in glines)
            for n in split_skills_text(summary, vocab, labelled_only=True):
                claim(guess_kind(n, vocab), n, "summary")
            for a in award_sentences(summary):
                claim("award", a, "summary")
        if key in ("awards", "leadership", "education", "volunteering") or "awards" in keys:
            for ln in glines:
                text = re.sub(r"^Awards?:\s*", "", ln.text)
                if key == "awards" or AWARD.search(text):
                    claim("award", text, key)

    # Skills named inside bullets: vocabulary names Jev confirms (18b).
    names, asks = [], []
    for b, where in bullets_all:
        for f in find_names(b.text, vocab):
            k = f"uses_{len(names)}"
            names.append((k, f, where))
            asks.append(states_use(k, f.text, b.text))
    ul = [line_role(k, ln.text, before, after) for k, ln, before, after in unclear]
    answers = labeller.ask(ul + asks) if (ul or asks) else {}
    gate(report, answers, [a.key for a in ul])
    for k, f, where in names:
        a = answers.get(k)
        if a is not None and a.trusted and a.label == "yes":
            claim(guess_kind(f.text, vocab), f.text, where, "jev", bullet_ref=where)
    unmatched = [b for b, _ in bullets_all if not find_names(b.text, vocab)]
    if unmatched:
        report.notes.append(f"{len(unmatched)} bullet(s) with no vocabulary name: the small claims call would read "
                            "them (not made in shadow mode without PARSE_SHADOW_CALLS)")
    report.items = {"roles": len(out.roles), "bullets": len(bullets_all), "projects": len(out.projects),
                    "claims": len(out.claims), "certifications": len(out.certifications)}
    return ParseResult(out if report.ok else None, report, methods)


def _cert_or_pub(ln, key: str, out, methods) -> None:
    cells = ln.cells or [ln.text]
    main = _LINKS.sub("", cells[0]).strip()
    when = next((dates.find_single(c) for c in cells[1:] if dates.find_single(c)), None)
    parts = [p.strip() for p in re.split(r"\s+·\s+", main)]
    name = parts[0]
    if m := re.search(r",\s*((?:19|20)\d{2})$", name):           # "Certificate (online course), 2022"
        when = when or dates.find_single(m.group(1))
        name = name[:m.start()]
    cred = next((re.sub(r"^.*Credential ID\s*", "", p) for p in parts if "Credential ID" in p), None)
    when = when or next((dates.find_single(p) for p in parts[1:] if dates.find_single(p)), None)
    issuer = None
    m = re.match(r"(?P<name>.+?),\s+(?P<issuer>[A-Z][\w&.' -]{2,60})$", name)
    if m and not re.search(r"\d", m["issuer"]) and len(m["issuer"].split()) <= 6:
        name, issuer = m["name"].strip(), m["issuer"].strip()
    if key == "publications":
        title = re.split(r"\s+[—–]\s+", name)[0].strip(' "“”')
        p = schemas.Project(kind="publication", name=title, description=name, url=None, start=None,
                            end=when.end if when else None, role_index=None, bullets=[], source=src(title, "publications"))
        out.projects.append(p)
        methods[id(p)] = "parser"
        return
    c = schemas.Certification(name=name, issuer=issuer, date=when.start if when else None, credential_id=cred,
                              verify_url=None, source=src(name, "certifications"))
    out.certifications.append(c)
    methods[id(c)] = "parser"
