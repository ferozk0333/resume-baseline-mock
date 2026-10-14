"""Judge interface (specs/02): models produce labels; code computes scores."""
from __future__ import annotations

import json
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from core.text import wrap_tag
from judges.questions import QUESTIONS, Question


class Judgment(BaseModel):
    label: str
    probabilities: dict[str, float] | None = None
    quote: str | None = None
    reasoning: str | None = None
    method: Literal["jev", "claude", "gemini", "groq", "rule"]
    model_version: str
    confidence: float | None = None   # Jev confidence (yes/no: |2p - 1|); drives the Claude fallback
    score: float | None = None        # Jev Score's in-between position; stored only, never used in scoring
    # A low-confidence or failed Jev judgment that this one replaced; saved with used = 0.
    fallback_from: "Judgment | None" = Field(default=None, exclude=True)


class JudgeError(Exception):
    """A judgment could not be produced after retries.

    `discarded` is an answer that exists but is not trusted (e.g. Jev below the
    confidence threshold before Claude also failed). It is saved with used = 0.
    """

    def __init__(self, message: str, discarded: "Judgment | None" = None):
        super().__init__(message)
        self.discarded = discarded


class Judge(Protocol):
    def choice(self, question: str, options: list[str], context: dict) -> Judgment: ...
    def yes_no(self, question: str, context: dict) -> Judgment: ...
    def models_for(self, question: str) -> list[str]:
        """Model versions whose saved judgments may be reused for this question."""
        ...


def ask(judge: Judge, question: Question, context: dict) -> Judgment:
    if question.kind == "yes_no":
        return judge.yes_no(question.key, context)
    return judge.choice(question.key, list(question.options), context)


def question(key: str) -> Question:
    try:
        return QUESTIONS[key]
    except KeyError:
        raise KeyError(f"unknown judgment question {key!r}") from None


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(f"{k}: {_as_text(v)}" for k, v in value.items() if v not in (None, "", []))
    if isinstance(value, (list, tuple)):
        return "\n".join(f"- {_as_text(v)}" for v in value)
    return json.dumps(value, default=str)


def render_context(context: dict) -> str:
    """Each context entry as <key>…</key>. Keys starting with '_' are metadata and never sent."""
    return "\n\n".join(wrap_tag(k, _as_text(v)) for k, v in context.items() if not k.startswith("_"))
