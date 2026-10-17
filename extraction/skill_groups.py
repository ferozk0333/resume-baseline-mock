"""Skill and tool groups shown in Review (specs/19 item 10). Display only: never read by scoring."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from extraction import dedupe

FILE = Path(__file__).resolve().parent.parent / "config" / "skill_groups.yaml"
OTHER = "Other"


@lru_cache(maxsize=1)
def _load() -> tuple[list[str], dict[str, str]]:
    data = yaml.safe_load(FILE.read_text()) if FILE.exists() else {}
    groups = list((data or {}).get("groups") or {})
    names: dict[str, str] = {}
    for group, members in ((data or {}).get("groups") or {}).items():
        for m in members or []:
            names.setdefault(dedupe.canonical(str(m)), group)
    if OTHER not in groups:
        groups.append(OTHER)
    return groups, names


def groups() -> list[str]:
    return _load()[0]


def group_of(name: str, chosen: str | None = None) -> str:
    """The group a candidate picked when adding it, else the reviewed file's, else Other."""
    order, names = _load()
    if chosen in order:
        return chosen
    return names.get(dedupe.canonical(name), OTHER)
