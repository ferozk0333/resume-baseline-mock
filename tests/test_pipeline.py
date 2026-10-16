"""Phase 5: the LangGraph pipeline end to end with FakeLLM + FakeJudge (no network)."""
import json
from datetime import date

import pytest

from core import config, db
from extraction.onboard import CertificationForm, OnboardingInputs, ReferenceForm, Upload
from graph.context import Deps
from graph.pipeline import Pipeline, rescore, run_scores
from judges.fake_judge import FakeJudge
from judges.questions import QUESTIONS
from scoring.aggregate import aggregate
from tests.helpers import FakeLLM, SAMPLE_RESUME_LINES, make_pdf, sample_resume_extract

TODAY = date(2026, 6, 15)
DS, AI = "baseline-data-scientist-entry", "baseline-ai-engineer-entry"
ALL_SUBS = set(config.SUBCATEGORY_DIMENSION)


def best(q, c):
    return list(QUESTIONS[q].options)[0]


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    path = str(tmp_path / "t.db")
    db.init_app_db(path).close()
    return path


def pipeline(db_path, judge=None, llm=None, claude_judge=None):
    return Pipeline(Deps(db_path, judge or FakeJudge(script=best),
                         llm=llm or FakeLLM({"resume": sample_resume_extract()}), today=TODAY,
                         claude_judge=claude_judge))


def inputs(**kw):
    return OnboardingInputs(resume=Upload("cv.pdf", make_pdf(SAMPLE_RESUME_LINES)), **kw)


def onboard_and_score(db_path, judge=None, baseline=DS, **kw):
    p = pipeline(db_path, judge)
    r = p.start_onboarding(inputs(**kw), baseline)
    return p, p.confirm_review(r["thread_id"])


def connect(db_path):
    return db.connect(db_path)


# --- Flow ------------------------------------------------------------------------------------

def test_onboarding_pauses_for_review_then_scores_all_subcategories(db_path):
    p = pipeline(db_path)
    r = p.start_onboarding(inputs(), DS)
    review = r["awaiting_review"]
    assert review and review["blocked"] is None and review["candidate_id"]
    assert "run_id" not in r                                   # nothing scored before review
    done = p.confirm_review(r["thread_id"])
    assert done["awaiting_review"] is None
    assert set(done["subcategory_scores"]) == ALL_SUBS
    assert 0 <= done["final"] <= 100 and done["errors"] == []
    conn = connect(db_path)
    assert len(conn.execute("SELECT * FROM scores WHERE run_id = ?", (done["run_id"],)).fetchall()) == 19


def test_run_records_logic_version_weights_models_and_baseline_hash(db_path):
    _, done = onboard_and_score(db_path)
    conn = connect(db_path)
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (done["run_id"],)).fetchone()
    assert run["status"] == "done" and run["finished_at"]
    assert run["logic_version"] == config.LOGIC_VERSION
    assert json.loads(run["weights_json"]) == config.default_weights()
    assert run["baseline_hash"] == db.get_baseline(conn, DS).content_hash()
    models = json.loads(run["models_json"])
    assert models["complexity_model"] == config.COMPLEXITY_MODEL and models["judge"] == config.JUDGE
    rows = conn.execute("SELECT DISTINCT logic_version FROM scores WHERE run_id = ?", (done["run_id"],)).fetchall()
    assert [r[0] for r in rows] == [config.LOGIC_VERSION]


def test_scores_are_explainable_with_judgment_trail(db_path):
    _, done = onboard_and_score(db_path)
    tools = done["subcategory_scores"]["tools"]
    trail = tools["details"]["judgments"]
    assert trail and {t["question"] for t in trail} == {"tool_evidence"}
    assert any(t["method"] == "rule" for t in trail)          # unmentioned tools labelled by code
    assert any(t["method"] == "claude" for t in trail)        # FakeJudge reports itself as claude


def test_no_resume_ends_with_message(db_path):
    r = pipeline(db_path).start_onboarding(OnboardingInputs(resume=None), DS)
    assert r["blocked"] == "Upload a resume to get your score." and r["awaiting_review"] is None


def test_review_loops_while_blocked_and_can_cancel(db_path):
    p = pipeline(db_path)
    r = p.start_onboarding(OnboardingInputs(resume=Upload("scan.pdf", make_pdf([]))), DS)
    assert r["awaiting_review"]["blocked"] == "Add at least one role or project before scoring."
    again = p.confirm_review(r["thread_id"])
    assert again["awaiting_review"]["blocked"]                # still nothing to score → asked again
    end = p.confirm_review(r["thread_id"], {"cancel": True})
    assert end["awaiting_review"] is None and "run_id" not in end


def test_score_another_baseline_without_reextracting(db_path):
    p, first = onboard_and_score(db_path)
    llm_calls = len(p.deps.llm.calls)
    second = p.score(first["candidate_id"], AI)
    assert len(p.deps.llm.calls) == llm_calls                 # no extraction
    assert second["run_id"] != first["run_id"] and set(second["subcategory_scores"]) == ALL_SUBS


# --- Caching, reuse and weights ---------------------------------------------------------------

def test_rescoring_same_inputs_makes_no_model_calls_and_same_scores(db_path):
    _, first = onboard_and_score(db_path)
    silent = FakeJudge(script=lambda q, c: AssertionError(f"judge called for {q}"))
    again = pipeline(db_path, silent).score(first["candidate_id"], DS)
    assert silent.calls == []
    assert again["errors"] == []
    assert {k: (v["status"], v["score"]) for k, v in again["subcategory_scores"].items()} == \
           {k: (v["status"], v["score"]) for k, v in first["subcategory_scores"].items()}
    assert again["final"] == first["final"]


def test_weight_change_reaggregates_saved_scores_without_judge(db_path, monkeypatch):
    _, done = onboard_and_score(db_path)
    import judges.base
    monkeypatch.setattr(judges.base, "ask", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model call")))
    conn = connect(db_path)
    assert rescore(conn, done["run_id"]).final == pytest.approx(done["final"])
    w = config.default_weights()
    w["dimensions"]["trust"] = 0
    expected = aggregate(run_scores(conn, done["run_id"]), w).final
    assert rescore(conn, done["run_id"], w).final == pytest.approx(expected)
    assert expected != pytest.approx(done["final"])


def test_trust_judgments_reused_across_baselines(db_path):
    p, first = onboard_and_score(db_path)
    counting = FakeJudge(script=best)
    pipeline(db_path, counting).score(first["candidate_id"], AI)
    asked = {q for q, _ in counting.calls}
    independent = {"proof_support", "source_alignment", "outcome_metric", "outcome_direction", "impact_type",
                   "ownership", "reach", "progression", *(f"quality_check_{i}" for i in range(1, 6))}
    assert asked and not (asked & independent)                 # only job-dependent questions re-asked
    assert all(QUESTIONS[q].job_dependent for q in asked)


def test_editing_baseline_invalidates_only_job_dependent_judgments(db_path):
    _, first = onboard_and_score(db_path)
    conn = connect(db_path)
    b = db.get_baseline(conn, DS)
    db.update_baseline(conn, b.model_copy(update={"description": b.description + " Also SQL-heavy."}))
    counting = FakeJudge(script=best)
    pipeline(db_path, counting).score(first["candidate_id"], DS)
    asked = {q for q, _ in counting.calls}
    assert "relevance" in asked and all(QUESTIONS[q].job_dependent for q in asked)


def test_changing_weights_in_state_only_changes_aggregation(db_path):
    p, first = onboard_and_score(db_path)
    w = config.default_weights()
    w["dimensions"]["experience"] = 90
    silent = FakeJudge(script=lambda q, c: AssertionError("no calls expected"))
    again = pipeline(db_path, silent).score(first["candidate_id"], DS, weights=w)
    assert silent.calls == [] and again["final"] != pytest.approx(first["final"])
    assert again["subcategory_scores"]["relevance"]["score"] == first["subcategory_scores"]["relevance"]["score"]


# --- Failures never crash the run -------------------------------------------------------------

def test_failing_judgment_makes_subcategory_error_and_run_continues(db_path):
    def script(q, c):
        return RuntimeError("impact judge down") if q == "impact_type" else best(q, c)

    _, done = onboard_and_score(db_path, FakeJudge(script=script))
    subs = done["subcategory_scores"]
    assert subs["impact"]["status"] == "error" and subs["impact"]["score"] is None
    assert subs["ownership"]["status"] == "scored"
    assert any("impact_type" in e for e in done["errors"])
    agg = aggregate(subs)
    assert done["final"] == pytest.approx(agg.final) and "impact" in agg.excluded["authority"]


def test_one_failed_bullet_keeps_subcategory_scored(db_path):
    failed = []

    def script(q, c):                       # one bullet's ownership always fails (grouped call and solo ask)
        if q == "ownership" and (not failed or c["_subject_id"] == failed[0]):
            failed[:1] = [c["_subject_id"]]
            return RuntimeError("one bullet failed")
        return best(q, c)

    _, done = onboard_and_score(db_path, FakeJudge(script=script))
    assert done["subcategory_scores"]["ownership"]["status"] == "scored"     # 05 §D: other judgments usable
    assert len([e for e in done["errors"] if "ownership" in e]) == 1


def test_compute_exception_is_isolated(db_path, monkeypatch):
    from graph.nodes import experience
    import graph.pipeline as gp
    broken = [s if s.name != "recency" else type(s)("recency", s.plan, lambda ctx, labels: 1 / 0)
              for s in experience.SUBCATEGORIES]
    monkeypatch.setitem(gp.DIMENSIONS, "experience", broken)
    _, done = onboard_and_score(db_path)
    rec = done["subcategory_scores"]["recency"]
    assert rec["status"] == "error" and "ZeroDivisionError" in rec["details"]["reason"]
    assert done["subcategory_scores"]["relevance"]["status"] == "scored" and done["final"] is not None


# --- Rule labels and missing evidence ---------------------------------------------------------

CLEANED = "Cleaned survey data with pandas for the marketing team."


def _criterion(db_path, name):
    return next(c for c in db.get_baseline(connect(db_path), DS).criteria if c.name == name)


def _skill_rows(db_path, crit_id, question="skill_evidence"):
    return connect(db_path).execute(
        "SELECT method, label, quote, used FROM judgments WHERE subject_id = ? AND question = ? ORDER BY rowid",
        (crit_id, question)).fetchall()


def test_unmatched_skill_goes_to_claude_with_all_bullets(db_path):
    main, claude = FakeJudge(script=best), FakeJudge(script=lambda q, c: QUESTIONS[q].none_option)
    p = pipeline(db_path, main, claude_judge=claude)
    p.confirm_review(p.start_onboarding(inputs(), DS)["thread_id"])
    wrangling = _criterion(db_path, "Data wrangling")          # aliases: data cleaning, data preparation, ETL
    forecasting = _criterion(db_path, "Forecasting")           # "forecasting store demand" → alias/name match
    claude_ctx = {c["_subject_id"]: c for q, c in claude.calls if q == "skill_evidence"}
    main_ids = {c["_subject_id"] for q, c in main.calls if q == "skill_evidence"}
    assert wrangling.id in claude_ctx and wrangling.id not in main_ids
    assert forecasting.id in main_ids and forecasting.id not in claude_ctx
    assert CLEANED in claude_ctx[wrangling.id]["bullets"]          # all bullets, not a filtered subset
    assert len(claude_ctx[wrangling.id]["bullets"]) >= 4


def test_cleaned_survey_data_earns_credit_for_data_cleaning(db_path):
    def claude_script(q, c):
        if q in ("skill_evidence", "applied_level") and c["criterion"].startswith("Data wrangling"):
            return ("bullet" if q == "skill_evidence" else "skill_task", CLEANED)
        return QUESTIONS[q].none_option

    p = pipeline(db_path, claude_judge=FakeJudge(script=claude_script))
    done = p.confirm_review(p.start_onboarding(inputs(), DS)["thread_id"])
    items = done["subcategory_scores"]["role_skills"]["details"]["required"]["items"]
    assert items["Data wrangling"] == "bullet"
    (row,) = _skill_rows(db_path, _criterion(db_path, "Data wrangling").id)
    assert (row["method"], row["label"], row["quote"], row["used"]) == ("claude", "bullet", CLEANED, 1)


def test_unmatched_label_without_quote_retries_then_counts_as_missing(db_path):
    claude = FakeJudge(script=lambda q, c: "bullet" if q == "skill_evidence" else QUESTIONS[q].none_option)
    p = pipeline(db_path, claude_judge=claude)
    done = p.confirm_review(p.start_onboarding(inputs(), DS)["thread_id"])
    wid = _criterion(db_path, "Data wrangling").id
    assert sum(1 for q, c in claude.calls if q == "skill_evidence" and c["_subject_id"] == wid) == 2
    rows = _skill_rows(db_path, wid)
    assert [(r["method"], r["label"], r["used"]) for r in rows] == [
        ("claude", "bullet", 0), ("claude", "bullet", 0), ("rule", "missing", 1)]
    assert done["subcategory_scores"]["role_skills"]["details"]["required"]["items"]["Data wrangling"] == "missing"
    # a re-run reuses the rule outcome: no new calls
    again = FakeJudge(script=lambda q, c: AssertionError("no calls"))
    pipeline(db_path, again, claude_judge=again).score(done["candidate_id"], DS)
    assert again.calls == []


def test_profile_without_any_text_gets_missing_by_rule(db_path):
    from extraction.records import Profile, RoleRec
    from extraction import store
    conn = connect(db_path)
    cid = store.create_candidate(conn, "Bare")
    store.save_profile(conn, cid, Profile(roles=[RoleRec("Assistant", None, None, None, None, True, None,
                                                         "Assistant", None)]))
    judge = FakeJudge(script=lambda q, c: QUESTIONS[q].none_option)
    pipeline(db_path, judge, claude_judge=judge).score(cid, DS)
    assert not any(q in ("skill_evidence", "applied_level", "tool_evidence") for q, _ in judge.calls)


def test_missing_sources_get_guidance(db_path):
    _, done = onboard_and_score(db_path)
    subs = done["subcategory_scores"]
    for name in ("consistency", "work_samples"):
        assert subs[name]["status"] == "missing" and subs[name]["guidance"], name
    # the resume's certificate has a credential ID and matches a baseline alias → 0.5, not missing
    vs = subs["validation_strength"]
    assert vs["status"] == "scored" and vs["score"] == 0.5 and vs["details"]["certifications"][0]["match"] == "matched"


def test_validation_strength_from_form_reference_and_certificate(db_path):
    _, done = onboard_and_score(
        db_path, references=[ReferenceForm("Sam Lee", "manager", None, "Jane built our churn model.")],
        certifications=[CertificationForm("Google Data Analytics Certificate", verify_url="https://v/1")])
    vs = done["subcategory_scores"]["validation_strength"]
    assert vs["status"] == "scored" and vs["score"] == 1.0
    cert = vs["details"]["certifications"][0]
    assert cert["match"] == "matched" and cert["criterion"] == "Data science or analytics certification"


def test_progress_lines_reported_per_step(db_path):
    lines = []
    p = pipeline(db_path)
    r = p.start_onboarding(inputs(), DS, on_progress=lines.append)
    assert lines[:3] == ["Reading documents", "Extracting with the model", "Checking quotes and dates"]
    p.confirm_review(r["thread_id"], on_progress=lines.append)
    assert "Judging relevance of roles and projects" in lines
    assert "Expertise › Role-specific skills done" in lines and "Trust › Evidence quality done" in lines
    assert sum(" done" in ln for ln in lines) == 19 and lines[-1] == "Ranking gaps and writing the plan"


def test_rerun_extraction_replaces_profile_for_same_candidate(db_path):
    p, first = onboard_and_score(db_path)
    cid = first["candidate_id"]
    conn = connect(db_path)
    from extraction import store
    store.save_role(conn, cid, title="Edited role", employer=None, industry=None, start_date=date(2020, 1, 1),
                    end_date=date(2020, 6, 1), is_current=False, team_size=None, bullets=["x"])
    r = p.start_onboarding(OnboardingInputs(resume=Upload("cv.pdf", make_pdf(SAMPLE_RESUME_LINES)),
                                            candidate_id=cid), DS)
    assert r["candidate_id"] == cid
    titles = [x.title for x in store.load_profile(conn, cid).roles]
    assert "Edited role" not in titles and titles == ["Data Analyst", "Analytics Intern"]
    assert len([e for e in store.list_evidence(conn, cid) if e["type"] == "resume"]) == 1
