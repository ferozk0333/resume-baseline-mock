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


# --- 2.3 Several questions per Jev request --------------------------------------------------------

BULLET_KEYS = ["outcome_metric", "outcome_direction", "impact_type", "ownership", "reach"]


def _jev_answers(low: str | None = None) -> dict:
    """Jev answers for the 5 per-bullet questions; `low` gets a confidence below the threshold."""
    out = {}
    for k in BULLET_KEYS:
        q = QUESTIONS[k]
        if q.kind == "yes_no":
            out[k] = {"type": "noul", "noul": 0.55 if k == low else 0.97}
        elif q.kind == "graded":
            n = len(q.options)
            out[k] = {"type": "score", "score": n - 1, "confidence": 0.1 if k == low else 0.9,
                      "probabilities": {str(i): (0.9 if i == n - 1 else 0.1 / (n - 1)) for i in range(n)}}
        else:
            first = next(iter(q.options))
            out[k] = {"type": "choice", "choice": first, "confidence": 0.1 if k == low else 0.9,
                      "probabilities": {o: (0.9 if o == first else 0.0) for o in q.options}}
    return out


def test_one_jev_request_carries_all_bullet_questions_and_low_confidence_falls_back_alone(tmp_path):
    from judges.fake_judge import FakeJudge
    from judges.jev_judge import JevJudge
    from judges.service import JudgmentService
    from tests.test_judges import PostSession
    session = PostSession((200, {"model": "jev-1.13.0", "answers": _jev_answers(low="ownership")}))
    fallback = FakeJudge(labels={"ownership": "contributed"})
    judge = JevJudge(fallback=fallback, session=session, api_key="k", model="jev-1.13.0", threshold=0.6, backoff=0)
    conn = db.init_app_db(str(tmp_path / "j.db"))
    svc = JudgmentService(conn, judge, "cand-1")
    got = svc.ask_group(BULLET_KEYS, "bullet", "b-1", {"bullet": "Led the churn model; cut churn 12%."}, None)
    assert len(session.bodies) == 1 and list(session.bodies[0]["questions"]) == BULLET_KEYS
    assert [k for k, _ in fallback.calls] == ["ownership"]                 # only the unsure answer
    assert got["ownership"].label == "contributed" and got["ownership"].fallback_from.method == "jev"
    assert {k: got[k].method for k in BULLET_KEYS} == {**{k: "jev" for k in BULLET_KEYS}, "ownership": "claude"}
    rows = conn.execute("SELECT question, method, used FROM judgments ORDER BY rowid").fetchall()
    assert sorted((r["question"], r["method"], r["used"]) for r in rows) == sorted(
        [(k, "jev", 1) for k in BULLET_KEYS if k != "ownership"] + [("ownership", "jev", 0), ("ownership", "claude", 1)])
    # Each row has the same cache key as a single ask: a later solo ask is a cache hit, no request.
    for k in BULLET_KEYS:
        assert svc.ask(k, "bullet", "b-1", {"bullet": "Led the churn model; cut churn 12%."}).label == got[k].label
    assert len(session.bodies) == 1


def test_jev_request_failure_falls_back_per_question(tmp_path):
    from judges.fake_judge import FakeJudge
    from judges.jev_judge import JevJudge
    from tests.test_judges import PostSession
    session = PostSession((400, {"error": "bad"}))
    fallback = FakeJudge(default=None, script=lambda q, c: next(iter(QUESTIONS[q].options)))
    judge = JevJudge(fallback=fallback, session=session, api_key="k", model="jev-1.13.0", threshold=0.6, backoff=0)
    out = judge.group(BULLET_KEYS, {"bullet": "x"})
    assert sorted(k for k, _ in fallback.calls) == sorted(BULLET_KEYS)
    assert all(j.method == "claude" for j in out.values())


def test_pipeline_sends_bullet_and_quality_questions_grouped(tmp_path):
    path = str(tmp_path / "g.db")
    conn = db.init_app_db(path)
    cid, judge = ARCHETYPES["strong_ds"](conn)
    Pipeline(Deps(path, judge, today=TODAY)).score(cid, DS)
    assert set(judge.groups) == {tuple(BULLET_KEYS), tuple(f"quality_check_{i}" for i in range(1, 6))}
    bullet_groups = sum(1 for g in judge.groups if g == tuple(BULLET_KEYS))
    assert sum(1 for k, _ in judge.calls if k in BULLET_KEYS) == 5 * bullet_groups   # never asked one by one


# --- 2.5 Sources extracted in parallel ------------------------------------------------------------

def test_all_sources_are_extracted_at_once():
    import threading
    from extraction.extract import extract_all
    from extraction.validate import SourceDoc
    state = {"now": 0, "max": 0}
    lock = threading.Lock()

    class SlowLLM:
        def parse(self, *, model, system, user, output_type):
            with lock:
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.05)
            with lock:
                state["now"] -= 1
            return output_type.model_validate({"roles": [], "projects": [], "claims": [], "certifications": []}
                                              if "roles" in output_type.model_fields else
                                              {"projects": [], "claims": []})

    docs = [SourceDoc("r", "resume", "x", "Resume"), SourceDoc("l", "linkedin_pdf", "x", "LinkedIn"),
            SourceDoc("p", "portfolio", "x", "Portfolio")] + \
        [SourceDoc(f"g{i}", "github_repo", "x", f"repo {i}") for i in range(6)]
    start = time.perf_counter()
    out = extract_all(docs, SlowLLM())
    assert len(out) == 9 and state["max"] == 9
    assert time.perf_counter() - start < 0.05 * 3
