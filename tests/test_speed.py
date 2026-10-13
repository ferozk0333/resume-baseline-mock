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


# --- 2.2 Batched unmatched-skill calls -----------------------------------------------------------

def _unmatched_profile(conn, first_label, second_label):
    """A profile whose bullets name none of the baseline's skills: every skill takes the Claude route."""
    from tests.fixtures.archetypes import Builder
    b = Builder(conn, "Unmatched")
    b.role("Shift Lead", "Corner Store", start=12, current=True, bullets=[
        b.bullet("Managed the weekly staffing rota for 9 people."),
        b.bullet("Reconciled the till every night.")])
    skills = [c.name for c in db.get_baseline(conn, DS).items("skill")]
    b.skill("skill_evidence", skills[0], first_label)
    b.skill("skill_evidence", skills[1], second_label)
    cid, judge = b.save()
    return cid, judge, skills


def _rows(conn, cid, question, criterion_id):
    return conn.execute("SELECT label, method, used, reasoning FROM judgments WHERE candidate_id = ? AND question = ? "
                        "AND subject_id = ? ORDER BY rowid", (cid, question, criterion_id)).fetchall()


def test_unmatched_skills_go_to_claude_in_one_call(tmp_path):
    path = str(tmp_path / "b.db")
    conn = db.init_app_db(path)
    cid, judge, skills = _unmatched_profile(conn, ("bullet", "Managed the weekly staffing rota"), "missing")
    res = Pipeline(Deps(path, judge, today=TODAY)).score(cid, DS)
    assert res["errors"] == []
    assert ("skill_evidence", len(skills)) in judge.batches                 # one call for all N criteria
    assert sum(1 for k, _ in judge.calls if k == "skill_evidence") == len(skills)   # no solo calls
    baseline = db.get_baseline(conn, DS)
    for c in baseline.items("skill"):                                          # each criterion: its own row
        assert [r["used"] for r in _rows(conn, cid, "skill_evidence", c.id)] == [1]
    assert res["subcategory_scores"]["role_skills"]["details"]["judgments"][0]["label"] == "bullet"


def test_failed_quote_retries_that_criterion_alone_then_missing(tmp_path):
    path = str(tmp_path / "b.db")
    conn = db.init_app_db(path)
    cid, judge, skills = _unmatched_profile(conn, ("bullet", "Managed the weekly staffing rota"), ("bullet", None))
    Pipeline(Deps(path, judge, today=TODAY)).score(cid, DS)
    solo = [ctx["criterion"].split(":")[0] for k, ctx in judge.calls if k == "skill_evidence"][len(skills):]
    assert solo == [skills[1]]                                                 # only the failing criterion
    second = db.get_baseline(conn, DS).items("skill")[1]
    rows = _rows(conn, cid, "skill_evidence", second.id)
    assert [(r["label"], r["method"], r["used"]) for r in rows] == [
        ("bullet", "claude", 0), ("bullet", "claude", 0), ("missing", "rule", 1)]
    assert "in the batch and alone" in rows[-1]["reasoning"]


def test_cached_criteria_are_not_batched_again(tmp_path):
    path = str(tmp_path / "b.db")
    conn = db.init_app_db(path)
    cid, judge, skills = _unmatched_profile(conn, "missing", "missing")
    Pipeline(Deps(path, judge, today=TODAY)).score(cid, DS)
    before = len(judge.batches)
    Pipeline(Deps(path, judge, today=TODAY)).score(cid, DS)
    assert len(judge.batches) == before


def test_claude_batch_prompt_keeps_instructions_and_options():
    from judges.claude_judge import ClaudeJudge, batch_output_model
    from judges.questions import QUESTIONS as Q

    class LLM:
        def parse(self, *, model, system, user, output_type):
            self.system, self.user = system, user
            return batch_output_model("skill_evidence")(answers=[
                {"criterion": "C2", "label": "bullet", "quote": "built a churn model", "reasoning": "r"},
                {"criterion": "C1", "label": "keyword", "quote": "not in the input", "reasoning": "r"}])

    llm = LLM()
    shared = {"bullets": ["Built a churn model in Python."], "skills_list": ["SQL"]}
    out = ClaudeJudge(llm=llm).batch("skill_evidence", [{"criterion": "SQL: queries", **shared},
                                                        {"criterion": "Python: code", **shared}])
    assert Q["skill_evidence"].instructions in llm.system
    assert all(desc in llm.system for desc in Q["skill_evidence"].options.values())
    assert llm.user.count("Built a churn model") == 1 and "C1: SQL: queries" in llm.user
    assert [(j.label, j.quote) for j in out] == [("keyword", None), ("bullet", "built a churn model")]
