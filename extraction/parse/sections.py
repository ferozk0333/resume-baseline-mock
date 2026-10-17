"""Section headings (specs/18 §4.2 item 15): a dictionary of variants per section plus font and position cues."""
from __future__ import annotations

import re

from core.text import normalize_name

VARIANTS: dict[str, set[str]] = {
    "experience": {"experience", "work experience", "professional experience", "work history", "employment",
                   "employment history", "relevant experience", "career history", "industry experience",
                   "research experience", "teaching experience", "internships", "internship experience"},
    "projects": {"projects", "selected projects", "relevant projects", "personal projects", "academic projects",
                 "key projects", "side projects", "project experience", "technical projects", "portfolio"},
    "education": {"education", "academic background", "education and training", "academics",
                  "education professional development", "education & professional development"},
    "skills": {"skills", "technical skills", "core competencies", "competencies", "technologies", "tech stack",
               "tools", "skills and tools", "tools and technologies", "key skills", "areas of expertise", "expertise",
               "top skills"},
    "certifications": {"certifications", "certificates", "licenses", "licences", "licenses certifications",
                       "licenses & certifications", "licences & certifications", "credentials", "certification"},
    "publications": {"publications", "papers", "research", "talks", "presentations", "publications and talks"},
    "awards": {"awards", "honors", "honours", "awards and honors", "honors awards", "honors-awards",
               "achievements", "honors and awards", "awards & honors", "recognition", "accomplishments"},
    "volunteering": {"volunteering", "volunteer experience", "volunteer", "community", "community involvement"},
    "summary": {"summary", "profile", "professional summary", "about", "about me", "objective", "career objective",
                "overview", "professional profile"},
    "leadership": {"leadership", "leadership & engagements", "leadership and engagements", "activities",
                   "extracurricular activities", "engagements", "leadership experience", "involvement"},
    "contact": {"contact", "contact information"},
    "languages": {"languages", "spoken languages"},
    "interests": {"interests", "hobbies", "hobbies and interests"},
    "references": {"references", "referees"},
    "coursework": {"coursework", "relevant coursework"},
}
_LOOKUP = {normalize_name(v): k for k, vs in VARIANTS.items() for v in vs}


def match(text: str) -> list[str]:
    """Section keys a heading names ("Certifications & Publications" → both). Empty when it is not a heading."""
    t = normalize_name(re.sub(r"[:|]+\s*$", "", text))
    if not t or len(t.split()) > 6:
        return []
    if t in _LOOKUP:
        return [_LOOKUP[t]]
    parts = [p.strip() for p in re.split(r"\s*(?:&|\band\b|/|,)\s*", text.strip().rstrip(":")) if p.strip()]
    keys = [_LOOKUP.get(normalize_name(p)) for p in parts]
    return [k for k in keys if k] if len(parts) > 1 and all(keys) else []


def is_heading(line, body_size: float) -> list[str]:
    """Keys when the line is styled like a heading and names a known section: alone on its line, short, and bigger,
    bold or in capitals. A known name in body style ("Skills: Python, SQL") is not a heading."""
    if line.bullet or len(line.cells) > 1 or len(line.text) > 60:
        return []
    styled = line.bold or line.size >= body_size + 0.6 or (line.text.isupper() and len(line.text) > 3) \
        or line.style.startswith(("Heading", "Title"))
    return match(line.text) if styled else []
