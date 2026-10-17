"""Phase 7: Streamlit pages run headless (AppTest) with fake deps. No network."""
import sys
import pytest
from streamlit.testing.v1 import AppTest

from core import config, db
from extraction import store
from extraction.onboard import OnboardingInputs, Upload
from graph.pipeline import Pipeline
from tests import ui_fakes
from extraction import schemas
from tests.helpers import SAMPLE_RESUME_LINES, FakeLLM, make_pdf, sample_resume_extract, src

DS = "baseline-data-scientist-entry"
APP_DIR = config.ROOT / "legacy" / "app"     # the retired Streamlit UI (phase 10)
if str(APP_DIR.parent) not in sys.path:
    sys.path.insert(0, str(APP_DIR.parent))


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setenv("RIGHTSTAFF_DEPS_FACTORY", "tests.ui_fakes:make_deps")
    db.init_app_db(path).close()
    ui_fakes.CALLS.update(judge=0, planner=0)
    return path


def scored_candidate(path, with_drop=False):
    """Onboard + score the sample resume with fakes; returns (candidate_id, run_id)."""
    extract = sample_resume_extract()
    if with_drop:
        extract.claims.append(schemas.Claim(kind="tool", name="Spark", bullet_ref=None, source=src("Spark")))
    deps = ui_fakes.make_deps()
    deps.llm = FakeLLM({"resume": extract})
    p = Pipeline(deps)
    r = p.start_onboarding(OnboardingInputs(resume=Upload("cv.pdf", make_pdf(SAMPLE_RESUME_LINES)), name="Jane"), DS)
    done = p.confirm_review(r["thread_id"])
    Pipeline.wait_for_plan(done["run_id"])           # the plan is written after the score
    return done["candidate_id"], done["run_id"]


def page(name, **state):
    path = APP_DIR / "Home.py" if name == "Home" else APP_DIR / "pages" / name
    at = AppTest.from_file(str(path), default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def no_errors(at):
    assert not at.exception, at.exception
    assert not [e.value for e in at.error], [e.value for e in at.error]


# --- Home & Baselines --------------------------------------------------------------------------------

def test_home_lists_runs_including_deleted_baseline(env):
    cid, _ = scored_candidate(env)
    conn = db.connect(env)
    db.delete_baseline(conn, DS)
    at = page("Home", candidate_id=cid)
    no_errors(at)
    df = at.dataframe[0].value
    assert list(df["baseline"]) == ["(deleted)"] and list(df["status"]) == ["done"]


def test_baselines_page_lists_seeds_and_warns_on_level_years(env):
    at = page("1_Baselines.py")
    no_errors(at)
    assert set(at.dataframe[0].value["title"]) == {"AI Engineer", "Data Scientist", "Product Manager"}
    form_inputs = {t.label: t for t in at.text_input}
    form_inputs["Title"].set_value("QA Analyst")
    form_inputs["Level"].set_value("senior")       # senior with 1 required year (the default)
    at.run()
    assert any("treats this baseline as entry level" in w.value for w in at.warning)


def test_create_baseline_through_form(env):
    at = page("1_Baselines.py")
    {t.label: t for t in at.text_input}["Title"].set_value("QA Analyst")
    next(b for b in at.button if b.label == "Save baseline").click()
    at.run()
    no_errors(at)
    titles = [b.title for b in db.list_baselines(db.connect(env))]
    assert "QA Analyst" in titles


# --- Score ---------------------------------------------------------------------------------------------

def test_score_page_shows_scores_and_judgments(env):
    cid, run_id = scored_candidate(env)
    at = page("3_Score.py", candidate_id=cid, baseline_id=DS)
    no_errors(at)
    metrics = {m.label: m.value for m in at.metric}
    assert set(metrics) == {"Final score", "Experience", "Expertise", "Authority", "Trust"}
    expected = db.connect(env)
    from graph.pipeline import rescore
    assert metrics["Final score"] == f"{rescore(expected, run_id).final:.1f}"
    labels = [e.label for e in at.expander]
    assert any(lbl.startswith("Role-specific skills") for lbl in labels)
    assert any("Graded by a reasoning model" in c.value for c in at.caption)


def test_weight_slider_reaggregates_without_model_calls(env):
    cid, run_id = scored_candidate(env)
    at = page("3_Score.py", candidate_id=cid, baseline_id=DS)
    before = next(m.value for m in at.metric if m.label == "Final score")
    calls = dict(ui_fakes.CALLS)
    at.slider(key="w__trust").set_value(0).run()
    no_errors(at)
    after = next(m.value for m in at.metric if m.label == "Final score")
    assert after != before
    assert ui_fakes.CALLS == calls                                     # no judge or planner call
    from graph.pipeline import rescore
    w = config.default_weights()
    w["dimensions"]["trust"] = 0
    assert after == f"{rescore(db.connect(env), run_id, w).final:.1f}"


def test_rejudge_clears_and_rescores(env):
    cid, _ = scored_candidate(env)
    at = page("3_Score.py", candidate_id=cid, baseline_id=DS)
    before = ui_fakes.CALLS["judge"]
    next(b for b in at.button if b.label.startswith("Re-judge")).click()
    at.run()
    no_errors(at)
    assert ui_fakes.CALLS["judge"] > before                            # judgments asked again
    runs = db.list_runs(db.connect(env), cid)
    assert len(runs) == 2


# --- Gaps ----------------------------------------------------------------------------------------------

def test_gaps_page_shows_ranked_gaps_and_plan_cards(env):
    cid, _ = scored_candidate(env)
    at = page("4_Gaps.py", candidate_id=cid, baseline_id=DS)
    no_errors(at)
    assert all(v.startswith("up to ") for v in at.dataframe[0].value["potential"])
    assert any(m.value.startswith("### 1. Build an A/B test project") for m in at.markdown)
    assert any("if fully closed" in c.value for c in at.caption)
    planner_calls = ui_fakes.CALLS["planner"]
    at.slider(key="w__expertise").set_value(90).run()
    no_errors(at)
    assert ui_fakes.CALLS["planner"] == planner_calls                  # re-priced without a model call
    next(b for b in at.button if b.label == "Regenerate plan").click()
    at.run()
    assert ui_fakes.CALLS["planner"] == planner_calls + 1


# --- Onboarding review -----------------------------------------------------------------------------------

def test_review_shows_profile_dropped_items_and_add_back(env):
    cid, _ = scored_candidate(env, with_drop=True)
    at = page("2_Onboarding.py", candidate_id=cid, baseline_id=DS)
    no_errors(at)
    assert any(e.label.startswith("Analytics Intern") and "⚠️" in e.label for e in at.expander)
    assert any("Start date had only a year" in w.value for w in at.warning)
    assert any(s.value.startswith("Dropped items (1)") for s in at.subheader)
    next(b for b in at.button if b.label == "Add back").click()
    at.run()
    no_errors(at)
    claims = store.load_profile(db.connect(env), cid).claims
    assert any(c.name_raw == "Spark" and c.source == "user_edit" for c in claims)


def test_review_shows_the_normalized_source_text(env):
    cid, _ = scored_candidate(env)
    at = page("2_Onboarding.py", candidate_id=cid, baseline_id=DS)
    no_errors(at)
    assert any(s.value == "Source text" for s in at.subheader)
    resume = next(e for e in at.expander if e.label.startswith("Resume"))
    stored = db.connect(env).execute("SELECT raw_text FROM evidence_items WHERE candidate_id = ? AND type = 'resume'",
                                     (cid,)).fetchone()[0]
    assert [x.value.strip() for x in resume.text] == [stored.strip()]


def test_review_edit_role_marks_user_edit(env):
    cid, _ = scored_candidate(env)
    at = page("2_Onboarding.py", candidate_id=cid, baseline_id=DS)
    title = next(t for t in at.text_input if t.label == "Title" and t.value == "Analytics Intern")
    title.set_value("Data Analytics Intern")
    form_id = title.form_id
    next(b for b in at.button if b.label == "Save role" and b.form_id == form_id).click()
    at.run()
    no_errors(at)
    roles = {r.title: r for r in store.load_profile(db.connect(env), cid).roles}
    assert roles["Data Analytics Intern"].source == "user_edit"
    assert "Analytics Intern" not in roles


def test_reset_to_table_restores_default_weights(env):
    cid, _ = scored_candidate(env)
    at = page("3_Score.py", candidate_id=cid, baseline_id=DS)
    original = next(m.value for m in at.metric if m.label == "Final score")
    at.slider(key="w__trust").set_value(0).run()
    at.slider(key="w__experience__relevance").set_value(5).run()
    assert next(m.value for m in at.metric if m.label == "Final score") != original
    next(b for b in at.button if b.label == "Reset to table").click()
    at.run()
    no_errors(at)
    assert at.slider(key="w__trust").value == 20
    assert at.slider(key="w__experience__relevance").value == 35
    assert next(m.value for m in at.metric if m.label == "Final score") == original


def test_sliders_are_whole_numbers_and_trust_defaults_keep_the_table_ratios(env):
    cid, run_id = scored_candidate(env)
    at = page("3_Score.py", candidate_id=cid, baseline_id=DS)
    no_errors(at)
    trust = {s: at.slider(key=f"w__trust__{s}").value for s in config.SUBCATEGORIES["trust"]}
    assert trust == {"proof_density": 30, "validation_strength": 30, "consistency": 25, "evidence_quality": 10}
    assert all(isinstance(sl.value, int) and sl.step == 1 for sl in at.slider)
    assert any(c.value.startswith("31.6% of trust") for c in at.caption)
    from graph.pipeline import rescore
    final = next(m.value for m in at.metric if m.label == "Final score")
    assert final == f"{rescore(db.connect(env), run_id).final:.1f}"   # same score as the decimal table


def test_graph_view_tracks_a_real_run(env):
    from app.components.graph_view import GraphView
    view = GraphView()                                  # no placeholder: just collect states
    deps = ui_fakes.make_deps()
    p = Pipeline(deps)
    r = p.start_onboarding(OnboardingInputs(resume=Upload("cv.pdf", make_pdf(SAMPLE_RESUME_LINES))), DS,
                           on_event=view.on_event)
    assert view.state["ingest"] == view.state["extract"] == view.state["validate"] == "done"
    assert view.state["review"] == "waiting"
    p.confirm_review(r["thread_id"], on_event=view.on_event)
    view.finish()
    subs = [s for s in config.SUBCATEGORY_DIMENSION]
    assert all(view.state[s] in ("scored", "missing", "na") for s in subs), {s: view.state.get(s) for s in subs}
    assert view.state["recognition"] == "missing" and view.state["progression"] == "scored"
    assert view.state["aggregate"] == view.state["gap_analysis"] == "done"
    assert view.counts() == {"done": 19, "running": 0, "pending": 0}
    dot = view.dot()
    assert dot.startswith("digraph G") and "cluster_expertise" in dot and '"gap_analysis"' in dot


def test_graph_view_marks_skipped_steps_on_a_scoring_only_run(env):
    from app.components.graph_view import GraphView
    cid, _ = scored_candidate(env)
    view = GraphView()
    Pipeline(ui_fakes.make_deps()).score(cid, DS, on_event=view.on_event)
    view.finish()
    assert view._status("ingest") == "skipped" and view._status("prepare_match") == "done"
    assert 'style="rounded,filled,dashed"' in view.dot()


def test_home_shows_pipeline_diagram(env):
    at = page("Home")
    no_errors(at)
    assert any(e.label == "How the pipeline works (LangGraph)" for e in at.expander)
