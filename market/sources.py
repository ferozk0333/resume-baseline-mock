"""config/market_sources.yaml: roles, companies (tiers, source), title filters (specs/15 §2)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml

from core import config

TIERS = ("large", "mid", "startup")
SOURCES = ("greenhouse", "lever", "ashby", "manual")


@dataclass
class Company:
    name: str
    tier: str
    source: str
    source_id: str = ""
    placeholder: bool = False


@dataclass
class Role:
    key: str
    title: str
    manual_baseline: str
    title_keywords: list[str]
    title_exclusions: list[str]
    companies: list[Company] = field(default_factory=list)

    def title_matches(self, title: str) -> bool:
        t = f" {re.sub(r'[^a-z0-9]+', ' ', title.lower())} "
        norm = lambda k: f" {re.sub(r'[^a-z0-9]+', ' ', k.lower()).strip()} "     # noqa: E731
        return any(norm(k) in t for k in self.title_keywords) and not any(norm(x) in t for x in self.title_exclusions)


def load(path=None) -> dict[str, Role]:
    data = yaml.safe_load(open(path or config.MARKET_SOURCES))
    out = {}
    for key, r in data["roles"].items():
        companies = [Company(**c) for c in r.get("companies", [])]
        for c in companies:
            if c.tier not in TIERS or c.source not in SOURCES:
                raise ValueError(f"{key}: {c.name}: tier must be one of {TIERS}, source one of {SOURCES}")
        out[key] = Role(key, r["title"], r["manual_baseline"], r["title_keywords"], r["title_exclusions"], companies)
    return out


def role_for_baseline(roles: dict[str, Role], baseline_id: str) -> Role | None:
    return next((r for r in roles.values() if r.manual_baseline == baseline_id), None)
