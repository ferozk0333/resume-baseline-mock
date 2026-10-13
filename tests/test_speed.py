"""Phase 9 speed changes (specs/09 §4): concurrency, batching, multi-question Jev, early start."""
import time

from core import config, db
from graph.context import Deps
from graph.pipeline import Pipeline
from judges.questions import QUESTIONS
from tests.fixtures.archetypes import ARCHETYPES, TODAY

DS = "baseline-data-scientist-entry"
DELAY = 0.04
DIMENSION_OF = {
    **{k: "authority" for k in ("outcome_metric", "outcome_direction", "impact_type", "ownership", "reach")},
    **{k: "expertise" for k in ("skill_evidence", "applied_level", "tool_evidence", "complexity",
                                "work_sample_relevant")},
    **{f"quality_check_{i}": "trust" for i in range(1, 6)}, "proof_support": "trust", "source_alignment": "trust",
    "scope_vs_baseline": "experience", "progression": "experience",
}


def test_judge_calls_overlap_across_dimensions_within_the_limit(tmp_path):
    path = str(tmp_path / "s.db")
    conn = db.init_app_db(path)
    cid, judge = ARCHETYPES["strong_ds"](conn)
    judge.delay = DELAY
    limit = 8
    start = time.perf_counter()
    res = Pipeline(Deps(path, judge, today=TODAY, max_parallel_calls=limit)).score(cid, DS)
    wall = time.perf_counter() - start
    assert res["errors"] == []
    n = len(judge.spans)
    assert n > 40
    assert judge.max_in_flight == limit                       # the shared limit is reached and never exceeded
    # Total ≈ longest call × batches, not the sum: allow generous overhead for thread start-up.
    assert wall < n * DELAY / 2, (wall, n)
    assert wall >= (n // limit) * DELAY * 0.9
    # Calls of different dimensions are in flight together (the limit is shared across subgraphs).
    spans = [(DIMENSION_OF[k], s, e) for k, s, e in judge.spans if k in DIMENSION_OF]
    overlapping = {frozenset((a[0], b[0])) for a in spans for b in spans
                   if a[0] != b[0] and a[1] < b[2] and b[1] < a[2]}
    assert len(overlapping) >= 3, overlapping


def test_judge_concurrency_setting_is_the_default_limit():
    assert Deps("x.db", judge=None).max_parallel_calls == config.JUDGE_CONCURRENCY
    assert set(QUESTIONS) >= {"quality_check_1", "ownership"}
