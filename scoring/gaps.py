"""Gap ranking in code (specs/04 §7). Pure: no model calls, so weight changes re-rank instantly."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Mapping

from core import config
from core.models import Baseline
from scoring.aggregate import INCLUDED_STATUSES, aggregate
from scoring.formulas import SubScore

TOP_N = 5


@dataclass
class Gap:
    subcategory: str
    label: str
    dimension: str
    status: str                 # scored | missing
    score: float
    effective_weight: float
    gain: float                 # points out of 100, if the subcategory reached 1.0
    guidance: str | None = None
    details: dict = field(default_factory=dict)

    @property
    def gain_text(self) -> str:
        """Always "up to N points": the maximum possible, never a promised score."""
        return f"up to {self.gain:.1f} points"

    def as_dict(self) -> dict:
        return {**asdict(self), "gain_text": self.gain_text}


def _sd(s: SubScore | Mapping) -> dict:
    return asdict(s) if isinstance(s, SubScore) else dict(s)


def rank_gaps(subscores: Mapping[str, SubScore | Mapping], weights: dict | None = None,
              baseline: Baseline | None = None) -> list[Gap]:
    """All subcategories with status scored|missing and something left to gain, largest gain first.

    gain = 100 × effective weight × (1 − score); effective weights come from aggregate(), i.e. after
    na/error renormalization. na and error are never gaps.
    """
    agg = aggregate(subscores, weights)
    gaps = []
    for name, eff in agg.effective_weights.items():
        s = _sd(subscores[name])
        if s["status"] not in INCLUDED_STATUSES:
            continue
        score = 0.0 if s["status"] == "missing" else float(s["score"])
        gain = 100 * eff * (1 - score)
        if gain <= 1e-9:
            continue
        gaps.append(Gap(name, config.SUBCATEGORY_LABELS[name], config.SUBCATEGORY_DIMENSION[name], s["status"],
                        score, eff, gain, s.get("guidance"), gap_details(name, s, baseline)))
    gaps.sort(key=lambda g: (-g.gain, g.subcategory))
    return gaps


def top_gaps(subscores, weights=None, baseline=None, n: int = TOP_N) -> list[Gap]:
    return rank_gaps(subscores, weights, baseline)[:n]


# --- Details passed to the report: unmet criteria, missing evidence, best/worst items with quotes ----

_CRITERIA_SUBS = {"role_skills", "applied_knowledge", "tools"}
_TOP_LABEL = {"role_skills": "bullet", "applied_knowledge": "skill_task_result", "tools": "used"}


def _scalars(details: dict) -> dict:
    return {k: v for k, v in details.items()
            if isinstance(v, (int, float, str, bool)) and k not in ("reason",) and v is not None}


def gap_details(name: str, s: dict, baseline: Baseline | None = None) -> dict:
    details = s.get("details") or {}
    out: dict = {"facts": _scalars(details)}
    if name in _CRITERIA_SUBS:
        unmet = []
        for priority in ("required", "preferred"):
            for crit, label in ((details.get(priority) or {}).get("items") or {}).items():
                if label != _TOP_LABEL[name]:
                    unmet.append({"criterion": crit, "priority": priority, "label": label})
        out["unmet_criteria"] = unmet
    trail = details.get("judgments") or []
    none_labels = {"missing", "no", "none", "not_stated", "claimed_only", "unrelated", "below",
                   "mention_or_missing", "does_not", "not_aligned"}
    supported = [t for t in trail if t.get("label") and t["label"] not in none_labels]
    weak = [t for t in trail if t.get("label") in none_labels]
    out["best_evidence"] = [{"question": t["question"], "label": t["label"],
                             "quote": t.get("quote") or t.get("subject")} for t in supported[:3]]
    out["weakest_items"] = [{"question": t["question"], "label": t["label"], "item": t.get("subject")}
                            for t in weak[:5]]
    if name == "validation_strength" and baseline is not None:
        out["relevant_certifications"] = [c.name + (f" (e.g. {', '.join(c.aliases)})" if c.aliases else "")
                                          for c in baseline.items("certification")]
    return out
