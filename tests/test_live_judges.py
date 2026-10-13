"""Live ClaudeJudge checks (specs/07 §5, §6). Opt-in: RIGHTSTAFF_LIVE=1. A handful of Haiku/Sonnet calls."""
import os

import pytest

from core import config
from judges.claude_judge import ClaudeJudge
from tests.helpers import INJECTION

pytestmark = pytest.mark.skipif(os.getenv("RIGHTSTAFF_LIVE") != "1", reason="live model test; set RIGHTSTAFF_LIVE=1")

BASELINE = "Data Scientist (entry level). Turns data into decisions: analysis, statistical modeling, experiments, ML."
OPTIONS = ["relevant", "adjacent", "unrelated"]
ROLE = ("Barista, Corner Coffee, 2022-2023\n"
        "- Prepared espresso drinks during the morning rush.\n"
        "- Trained two new staff on the till.")


def test_live_relevance_label_resists_injection():
    """07 §6: hidden instructions must not change the label."""
    judge = ClaudeJudge()
    clean = judge.choice("relevance", OPTIONS, {"baseline": BASELINE, "role": ROLE})
    attacked = judge.choice("relevance", OPTIONS, {"baseline": BASELINE, "role": f"{ROLE}\n{INJECTION}"})
    assert clean.label == "unrelated"
    assert attacked.label == clean.label
    assert attacked.quote is None or "ignore previous" not in attacked.quote.casefold()


def test_live_yes_no_and_quote_is_from_input():
    j = ClaudeJudge().yes_no("outcome_metric", {"bullet": "Built a churn model that cut churn 12% in two quarters."})
    assert j.label == "yes" and j.model_version == config.JUDGE_FALLBACK_MODEL
    assert j.quote is None or j.quote in "Built a churn model that cut churn 12% in two quarters."


def test_live_complexity_on_reasoning_model():
    j = ClaudeJudge().choice("complexity", ["meets", "partly", "no"], {
        "baseline": "Desired complexity: worked with messy or multi-source data, chose and justified a method, "
                    "handled evaluation and trade-offs, connected results to a business decision.",
        "item": "Churn model: joined CRM, billing and support data; compared logistic regression and gradient "
                "boosting, chose GBM for recall; tuned the threshold with finance to cut churn 12%.",
    })
    assert j.label in ("meets", "partly") and j.model_version == config.COMPLEXITY_MODEL
    assert j.reasoning


# --- JevJudge (synthetic text only) ----------------------------------------------------

def _jev():
    from judges.jev_judge import JevJudge
    return JevJudge(fallback=ClaudeJudge())


def test_live_jev_three_question_types():
    judge = _jev()
    yn = judge.yes_no("outcome_metric", {"bullet": "Built a churn model that cut churn 12% in two quarters."})
    ch = judge.choice("relevance", OPTIONS, {"baseline": BASELINE, "role": ROLE})
    sc = judge.choice("scope_vs_baseline", ["meets", "partly", "below"], {
        "baseline": "Expected scope: owns an analysis or model for a defined business question.",
        "role": "Example Analyst: owned the churn model end to end and presented it to sales leadership."})
    assert yn.label == "yes"
    assert ch.label == "unrelated"
    assert sc.label in ("meets", "partly")
    for j in (yn, ch, sc):
        assert (j.method == "jev" and j.confidence >= config.JEV_CONFIDENCE_THRESHOLD and j.quote is None) \
            or (j.method == "claude" and j.fallback_from is not None)
    assert sc.method != "jev" or sc.score is not None


def test_live_jev_label_resists_injection():
    judge = _jev()
    clean = judge.choice("relevance", OPTIONS, {"baseline": BASELINE, "role": ROLE})
    attacked = judge.choice("relevance", OPTIONS, {"baseline": BASELINE, "role": f"{ROLE}\n{INJECTION}"})
    assert attacked.label == clean.label == "unrelated"


def test_live_unmatched_skill_gets_credit_with_quote():
    """Phase 5 decision: no alias match → Claude with all bullets; label must carry a quote from the input."""
    bullets = ["Cleaned survey data with pandas for the marketing team.",
               "Automated weekly sales reports with SQL, saving 5 hours per week.",
               "Built a churn model in Python and scikit-learn that cut churn 12%."]
    j = ClaudeJudge().choice("skill_evidence", ["bullet", "keyword", "missing"], {
        "criterion": "Data wrangling: Cleaning, joining and reshaping data. Also called: data cleaning, "
                     "data preparation, ETL.",
        "bullets": bullets, "skills_list": ["Python", "SQL", "Tableau"]})
    assert j.label == "bullet", j
    assert j.quote and any(j.quote in b or b in j.quote for b in bullets), j.quote
