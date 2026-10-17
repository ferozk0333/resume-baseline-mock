"""What every parser returns, the confidence gate (specs/18 §4.2 item 21) and the Jev questions they share."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from core import config
from extraction import schemas
from extraction.parse.labeller import Ask, Answer

LINE_ROLES = {
    "section_heading": "A section heading, such as Experience, Education or Skills.",
    "job_title": "A job title or position held.",
    "employer": "The name of an employer, company, school or organisation.",
    "date_range": "Dates or a date range.",
    "location": "A city, region, country or 'Remote'.",
    "bullet": "A description of work done or a result: a bullet or a sentence about a role or project.",
    "education": "A degree, school or course of study.",
    "certification": "A certificate, licence or credential.",
    "publication": "A paper, article, poster or talk the person authored.",
    "award": "An award, honour, prize, scholarship or ranking.",
    "skill": "A skill, tool or list of skills and tools.",
    "other": "Anything else: contact details, a name, a page number, a heading of no known section.",
}


@dataclass
class Report:
    """Per document: what was parsed, how, and why it fell back (logged; timings and calls per source)."""
    doc_type: str
    outcome: str = "parsed"                       # parsed | fallback
    reasons: list[str] = field(default_factory=list)
    unclear: int = 0
    jev_requests: int = 0
    jev_questions: int = 0
    llm_calls: int = 0
    seconds: float = 0.0
    items: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> None:
        self.outcome = "fallback"
        if reason not in self.reasons:
            self.reasons.append(reason)

    @property
    def ok(self) -> bool:
        return self.outcome == "parsed"


@dataclass
class ParseResult:
    extract: schemas.ProfileExtract | schemas.PortfolioExtract | None
    report: Report
    methods: dict[int, str] = field(default_factory=dict)    # id(schema item) → parser | jev | llm

    def method_of(self, item) -> str | None:
        return self.methods.get(id(item))


class Timer:
    def __init__(self, report: Report, labeller):
        self.report, self.labeller = report, labeller

    def __enter__(self):
        self.t0 = time.perf_counter()
        self.req0 = getattr(self.labeller.counts, "requests", 0)
        self.q0 = getattr(self.labeller.counts, "questions", 0)
        return self

    def __exit__(self, *exc):
        self.report.seconds = round(time.perf_counter() - self.t0, 4)
        self.report.jev_requests = self.labeller.counts.requests - self.req0
        self.report.jev_questions = self.labeller.counts.questions - self.q0
        return False


def src(quote: str, location: str) -> schemas.Source:
    return schemas.Source(quote=quote, location=location)


def gate(report: Report, answers: dict[str, Answer | None], unclear_keys: list[str]) -> None:
    """Fall back when too many lines stay unclear after Jev or any Jev label is below the threshold."""
    unresolved = [k for k in unclear_keys if answers.get(k) is None or not answers[k].trusted]
    low = [k for k in unclear_keys if answers.get(k) is not None and not answers[k].trusted]
    report.unclear = len(unresolved)
    if low:
        report.fail(f"{len(low)} Jev label(s) below the confidence threshold ({config.JEV_CONFIDENCE_THRESHOLD})")
    if len(unresolved) > config.PARSER_MAX_UNCLEAR:
        report.fail(f"{len(unresolved)} unclear line(s) after Jev (more than {config.PARSER_MAX_UNCLEAR})")


# --- Shared questions -------------------------------------------------------------------------------------------

def line_role(key: str, line: str, before: str, after: str) -> Ask:
    return Ask(key, "choice", f"Classify the resume line in `{key}`. `{key}_before` and `{key}_after` are the lines "
               "around it, for context only.", tuple(LINE_ROLES.items()),
               ((key, line), (f"{key}_before", before), (f"{key}_after", after)))


def same_item(key: str, line: str, previous: str) -> Ask:
    return Ask(key, "noul", f"Does the line in `{key}` belong to the same item (the same job, project, degree or "
               f"entry) as the line in `{key}_previous`?",
               ((True, "Same item: it continues or describes the previous line's entry."),
                (False, "A new item starts with this line.")), ((key, line), (f"{key}_previous", previous)))


def states_use(key: str, name: str, text: str) -> Ask:
    return Ask(key, "noul", f"In `{key}_text`, does the candidate state that they use, used or know `{key}_name`?",
               ((True, "Yes: the candidate used, built with, worked in or knows it."),
                (False, "No: it is mentioned in passing, as someone else's, as not used, or it is not a skill or "
                        "tool here.")), ((f"{key}_name", name), (f"{key}_text", text)))


def skill_or_tool(key: str, name: str) -> Ask:
    return Ask(key, "choice", f"Is `{key}` a tool or a skill?",
               (("tool", "A programming language, library, framework, platform, cloud service or product."),
                ("skill", "A method, technique or ability.")), ((key, name),))
