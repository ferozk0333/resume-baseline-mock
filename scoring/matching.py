"""Rule-based matching of candidate items to baseline criteria (no model calls)."""
from __future__ import annotations

from core.models import BaselineCriterion
from core.text import contains_phrase


def match_certification(cert_name: str, cert_criteria: list[BaselineCriterion]) -> str | None:
    """Name of the first certification criterion whose name or an alias appears in `cert_name`.

    Case-insensitive, whole-phrase match. Baseline names are generic ("Cloud ML
    certification"), so real titles usually match only through aliases. No match
    means "not matched", not "irrelevant".
    """
    for criterion in cert_criteria:
        if criterion.kind != "certification":
            continue
        if any(contains_phrase(cert_name, term) for term in [criterion.name, *criterion.aliases]):
            return criterion.name
    return None
