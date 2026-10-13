"""Pydantic models for stored entities. Later phases add candidate/evidence models."""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from core.config import ENTRY_LEVEL_MAX_YEARS

CriterionKind = Literal["skill", "tool", "certification"]
Priority = Literal["required", "preferred"]

REACH_LABELS = {
    1: "own work",
    2: "team",
    3: "cross-functional",
    4: "organization",
    5: "external",
}


class BaselineCriterion(BaseModel):
    id: str | None = None
    kind: CriterionKind
    name: str = Field(min_length=1)
    description: str | None = None
    aliases: list[str] = Field(default_factory=list)
    priority: Priority

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("criterion name must not be blank")
        return v

    @field_validator("aliases")
    @classmethod
    def _clean_aliases(cls, v: list[str]) -> list[str]:
        return [a.strip() for a in v if a and a.strip()]


class Baseline(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1)
    level: str = Field(min_length=1)
    required_years: float = Field(ge=0)
    expected_reach: int = Field(ge=1, le=5)
    scope_expectation: str | None = None
    complexity_criteria: str | None = None
    description: str | None = None
    is_mock: bool = True
    updated_at: str | None = None
    criteria: list[BaselineCriterion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _skill_or_tool_never_both(self) -> "Baseline":
        # specs/04 §3: each item is a skill or a tool, never both.
        seen: dict[str, str] = {}
        for c in self.criteria:
            key = c.name.casefold()
            prev = seen.get(key)
            if prev is not None and prev != c.kind and {prev, c.kind} == {"skill", "tool"}:
                raise ValueError(f"'{c.name}' is listed as both a skill and a tool")
            if prev == c.kind:
                raise ValueError(f"duplicate {c.kind} criterion '{c.name}'")
            seen[key] = c.kind
        return self

    def items(self, kind: CriterionKind, priority: Priority | None = None) -> list[BaselineCriterion]:
        return [
            c for c in self.criteria
            if c.kind == kind and (priority is None or c.priority == priority)
        ]

    def content_hash(self) -> str:
        """SHA-256 of the fields judges see; part of the cache key for job-dependent judgments.

        Level is included because the complexity prompt shows it. Criteria are
        sorted so reordering rows does not invalidate cached judgments.
        """
        payload = {
            "title": self.title,
            "level": self.level,
            "description": self.description,
            "scope_expectation": self.scope_expectation,
            "complexity_criteria": self.complexity_criteria,
            "criteria": sorted(
                [c.name, c.description or "", list(c.aliases)] for c in self.criteria
            ),
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def level_years_warning(level: str, required_years: float) -> str | None:
    """Warn when the level label disagrees with the entry-level rule (required_years <= 2)."""
    is_entry_level = level.strip().casefold() == "entry"
    if is_entry_level and required_years > ENTRY_LEVEL_MAX_YEARS:
        return (
            f"Level is 'entry' but required years is {required_years:g}. "
            f"Scoring treats this baseline as NOT entry level (rule: required years <= {ENTRY_LEVEL_MAX_YEARS})."
        )
    if not is_entry_level and required_years <= ENTRY_LEVEL_MAX_YEARS:
        return (
            f"Level is '{level}' but required years is {required_years:g}. "
            f"Scoring treats this baseline as entry level (rule: required years <= {ENTRY_LEVEL_MAX_YEARS})."
        )
    return None
