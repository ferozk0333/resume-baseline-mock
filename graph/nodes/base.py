"""A subcategory = what to judge (plan) + how to score it (compute). Both read the same Ctx."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from graph.context import Ask, Ctx, Labels
from scoring.formulas import SubScore


@dataclass(frozen=True)
class Subcategory:
    name: str
    plan: Callable[[Ctx], list[Ask]]
    compute: Callable[[Ctx, Labels], SubScore]


def no_asks(ctx: Ctx) -> list[Ask]:
    return []
