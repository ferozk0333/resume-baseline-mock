"""LinkedIn profile link for the recruiter view (display only; never scored).

Entered by the candidate on the upload form, or found in their own documents: the LinkedIn PDF export first
(its Contact section has the profile URL), then the resume. Code only: no model.
"""
from __future__ import annotations

import re

_PROFILE = re.compile(r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([A-Za-z0-9\-_%]{3,100})/?", re.I)
SOURCES = {"form": "entered by the candidate", "linkedin_pdf": "from the LinkedIn PDF", "resume": "from the resume"}


def normalize(url: str) -> str:
    """'linkedin.com/in/jane-doe/' → 'https://www.linkedin.com/in/jane-doe'. ValueError if it is not a profile URL."""
    m = _PROFILE.fullmatch((url or "").strip())
    if not m:
        raise ValueError("Enter a LinkedIn profile URL, e.g. https://www.linkedin.com/in/your-name")
    return f"https://www.linkedin.com/in/{m.group(1)}"


def find(text: str | None) -> str | None:
    m = _PROFILE.search(text or "")
    return f"https://www.linkedin.com/in/{m.group(1)}" if m else None


def profile_link(candidate: dict, evidence: list[dict]) -> dict | None:
    """{url, source} for the panel: what the candidate entered, else the first link in their LinkedIn PDF or resume."""
    if candidate.get("linkedin_url"):
        return {"url": candidate["linkedin_url"], "source": SOURCES["form"]}
    for kind in ("linkedin_pdf", "resume"):
        for e in evidence:
            if e["type"] == kind and (url := find(e.get("raw_text"))):
                return {"url": url, "source": SOURCES[kind]}
    return None
