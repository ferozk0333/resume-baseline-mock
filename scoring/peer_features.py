"""Peer-signal features (specs/12 §2): the one place they are defined.

Each feature reads one run's saved subcategory details (no model calls) and returns True / False, or None when
it cannot be known (that subcategory failed). `text` completes "Among higher-scoring candidates for this role,
NN% …". Adding a feature here needs no change anywhere else.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

ARTIFACT_SHARE = 0.5          # "backs at least half of their claims with an artifact"
REQUIRED_SHOWN_SHARE = 0.5    # "shows at least half of the required skills in their work"
RELEVANT_MONTHS = 12          # "has at least a year of relevant experience"


@dataclass(frozen=True)
class Feature:
    key: str
    dimension: str
    subcategory: str
    text: str
    value: Callable[[dict], bool | None]       # subcategory details (with "judgments") → has it?


def _judged(details: dict, question: str, label: str) -> bool:
    return any(j["question"] == question and j["label"] == label for j in details.get("judgments", []))


def _metric_and_direction(details: dict) -> bool:
    labels: dict[str, set] = {}
    for j in details.get("judgments", []):
        if j["label"] == "yes":
            labels.setdefault(j["subject_id"], set()).add(j["question"])
    return any({"outcome_metric", "outcome_direction"} <= q for q in labels.values())


def _share(values: list[float], at_least: float) -> bool:
    return bool(values) and sum(values) / len(values) >= at_least


FEATURES: list[Feature] = [
    Feature("verified_certification", "trust", "validation_strength",
            "hold a relevant certification with a credential ID or link",
            lambda d: any(c.get("verified") and c.get("match") == "matched" for c in d.get("certifications", []))),
    Feature("manager_reference", "trust", "validation_strength",
            "have a manager or supervisor reference confirming relevant work",
            lambda d: d.get("reference_id") is not None),
    Feature("aligned_second_source", "trust", "consistency",
            "have a second source (LinkedIn, GitHub or portfolio) that agrees with their resume",
            lambda d: any(s.get("aligned") for s in d.get("sources", []))),
    Feature("claims_backed_by_artifact", "trust", "proof_density",
            "back at least half of their skills with an artifact such as a repo or portfolio project",
            lambda d: _share([1.0 if v == 1.0 else 0.0 for v in (d.get("claims") or {}).values() if v is not None],
                             ARTIFACT_SHARE)),
    Feature("relevant_work_sample", "expertise", "work_samples",
            "link a repo or work sample relevant to this role",
            lambda d: bool(d.get("relevant_kinds"))),
    Feature("required_skills_in_work", "expertise", "role_skills",
            "show at least half of the required skills in their work, not only in a skills list",
            lambda d: _share([1.0 if v == "bullet" else 0.0 for v in ((d.get("required") or {}).get("items") or {}).values()],
                             REQUIRED_SHOWN_SHARE)),
    Feature("metric_and_direction", "authority", "outcomes",
            "have a bullet with a number and the direction of the change",
            _metric_and_direction),
    Feature("business_impact", "authority", "impact",
            "have a bullet with business impact",
            lambda d: _judged(d, "impact_type", "business")),
    Feature("led_the_work", "authority", "ownership",
            "have a bullet saying they led the work",
            lambda d: _judged(d, "ownership", "led")),
    Feature("year_of_relevant_experience", "experience", "depth",
            "have at least a year of relevant experience (roles or counted projects)",
            lambda d: d.get("relevant_years") is not None and 12 * d["relevant_years"] >= RELEVANT_MONTHS),
]
