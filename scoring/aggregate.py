"""Dimension and final scores from saved subcategory scores (specs/04 §6).

Pure code: changing weights only re-runs this module, never a model call.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from core import config
from scoring.formulas import SubScore

INCLUDED_STATUSES = ("scored", "missing")


def normalize_weights(weights: Mapping) -> dict:
    """Same shape as config.default_weights(); each group scaled to sum to 1.

    A group whose weights are all zero stays all zero (it then contributes nothing).
    Negative weights are rejected.
    """

    def norm(group: Mapping[str, float]) -> dict[str, float]:
        if any(v < 0 for v in group.values()):
            raise ValueError(f"weights must be >= 0: {dict(group)}")
        total = sum(group.values())
        return {k: (v / total if total > 0 else 0.0) for k, v in group.items()}

    return {
        "dimensions": norm(weights["dimensions"]),
        "subcategories": {d: norm(weights["subcategories"][d]) for d in config.DIMENSIONS},
    }


def _status_score(s: SubScore | Mapping | None) -> tuple[str | None, float | None]:
    if s is None:
        return None, None
    if isinstance(s, SubScore):
        return s.status, s.score
    return s.get("status"), s.get("score")


@dataclass
class Aggregate:
    dimensions: dict[str, float | None]          # 0..1, None when no included subcategory
    final: float | None                          # 0..100
    effective_weights: dict[str, float]          # included subcategories only; sums to 1
    included: dict[str, list[str]] = field(default_factory=dict)
    excluded: dict[str, list[str]] = field(default_factory=dict)  # na, error or not computed


def aggregate(subscores: Mapping[str, SubScore | Mapping], weights: Mapping | None = None) -> Aggregate:
    """Weighted averages with na/error excluded and weights renormalized.

    `missing` counts as 0. A subcategory absent from `subscores` is treated like
    `error` (excluded).
    """
    w = normalize_weights(weights or config.default_weights())
    dims: dict[str, float | None] = {}
    included: dict[str, list[str]] = {}
    excluded: dict[str, list[str]] = {}
    local_weights: dict[str, dict[str, float]] = {}

    for d in config.DIMENSIONS:
        num = den = 0.0
        included[d], excluded[d], local_weights[d] = [], [], {}
        for s in config.SUBCATEGORIES[d]:
            status, score = _status_score(subscores.get(s))
            if status not in INCLUDED_STATUSES:
                excluded[d].append(s)
                continue
            if status == "missing":
                score = 0.0
            if score is None or not 0.0 <= score <= 1.0:
                raise ValueError(f"{s}: status {status} needs a score in [0, 1], got {score!r}")
            ws = w["subcategories"][d][s]
            included[d].append(s)
            local_weights[d][s] = ws
            num += ws * score
            den += ws
        dims[d] = num / den if den > 0 else None
        if den > 0:
            local_weights[d] = {s: ws / den for s, ws in local_weights[d].items()}

    dim_total = sum(w["dimensions"][d] for d in config.DIMENSIONS if dims[d] is not None)
    if dim_total > 0:
        final = 100 * sum(w["dimensions"][d] * dims[d] for d in config.DIMENSIONS if dims[d] is not None) / dim_total
    else:
        final = None

    effective: dict[str, float] = {}
    if dim_total > 0:
        for d in config.DIMENSIONS:
            if dims[d] is None:
                continue
            for s, local in local_weights[d].items():
                effective[s] = (w["dimensions"][d] / dim_total) * local

    return Aggregate(dims, final, effective, included, excluded)
