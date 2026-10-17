"""What Review shows for each saved item (specs/19 Phase B–D): source chips, date flags, skill groups, credential
wording and the display-only proof types. Display only: nothing here is read by scoring."""
from __future__ import annotations

from datetime import date

from extraction import dedupe, skill_groups
from extraction.records import REVIEW_MESSAGES, ExtraItem, Profile, sources_of
from extraction.validate import CONFLICT

CHIPS = {"resume": "Resume", "linkedin_pdf": "LinkedIn", "github_repo": "GitHub", "portfolio": "Portfolio",
         "certification": "Added by you", "reference": "Added by you", "user": "Added by you"}
SHORT = {"resume": "your resume", "linkedin_pdf": "LinkedIn", "github_repo": "GitHub", "portfolio": "your portfolio"}
YEAR_ONLY = ("year_only_start", "year_only_end")
YEAR_ONLY_MESSAGE = "We only found the year. Please check the months."


def chips(rec, types: dict[str, str]) -> list[str]:
    """Where an item came from, in order, without repeats ("Resume", "LinkedIn", "Added by you")."""
    out = [CHIPS.get("user" if s == "user" else types.get(s, ""), "") for s in sources_of(rec)]
    if not out and rec.source == "user_edit":
        out = ["Added by you"]
    return [x for x in dict.fromkeys(out) if x]


def friendly_month(v: str | None) -> str:
    if not v:
        return "?"
    if v == "present":
        return "Present"
    try:
        return date.fromisoformat(v + "-01" if len(v) == 7 else v).strftime("%b %Y")
    except ValueError:
        return v


def conflict_message(code: str, own: str | None, types: dict[str, str]) -> str:
    _, other, start, end = (code.split("|") + ["", "", ""])[:4]
    a = SHORT.get(own or "", "one document")
    b = SHORT.get(types.get(other, ""), "another document")
    who = b[0].upper() + b[1:] if b else "Another document"
    return f"{a[0].upper() + a[1:]} and {b} give different dates for this role ({who}: {friendly_month(start)} – " \
           f"{friendly_month(end)}). Check them."


def credential_status(c) -> str | None:
    """Honest wording (specs/19 item 5): we never check a credential, so nothing says "verified"."""
    if (c.credential_id or "").strip():
        return "Credential ID added"
    if (c.verify_url or "").strip():
        return "Link added"
    return None


def review_view(data: dict, profile: Profile, evidence: list[dict], extra: list[ExtraItem]) -> None:
    """Adds the Review fields to the profile response in place."""
    types = {e["id"]: e["type"] for e in evidence}
    for key, recs in (("roles", profile.roles), ("projects", profile.projects)):
        for item, rec in zip(data[key], recs):
            codes = list(item["review_reasons"])
            item["review_codes"] = [c.split("|")[0] for c in codes]
            item["year_only"] = any(c in YEAR_ONLY for c in codes)
            conflict = next((c for c in codes if c.startswith(CONFLICT)), None)
            item["conflict"] = conflict_message(conflict, types.get(rec.evidence_id), types) if conflict else None
            item["review_reasons"] = [REVIEW_MESSAGES.get(c, c) for c in codes
                                      if c not in YEAR_ONLY and not c.startswith(CONFLICT)]
            item["sources"] = chips(rec, types)
    for item, rec in zip(data["claims"], profile.claims):
        item["group"] = skill_groups.group_of(rec.name_raw, rec.skill_group)
        item["sources"] = chips(rec, types)
    for item, rec in zip(data["certifications"], profile.certifications):
        item["sources"] = chips(rec, types)
        item["exam_code"] = dedupe.exam_code(rec.name)
        item["credential_status"] = credential_status(rec)
    data["extra"] = [{"id": x.id, "type": x.type, "fields": {k: v for k, v in x.fields.items() if k != "_also"},
                      "source": x.source, "quote": x.quote,
                      "sources": [s for s in dict.fromkeys(
                          [CHIPS.get(types.get(e or "", ""), "") for e in [x.evidence_id, *x.fields.get("_also", [])]]
                          + (["Added by you"] if x.source == "user_edit" and not x.evidence_id else [])) if s]}
                     for x in extra]
