"""Phase 6: gap ranking (code) and the gap report (fake reporter; live test separate)."""
from datetime import date

import pytest

from core import config, db
from core.llm import LLMError
from extraction.records import Profile, RoleRec
from graph.context import Deps
from graph.pipeline import Pipeline, run_gaps
from judges.fake_judge import FakeJudge
from scoring.formulas import SubScore
from scoring.gaps import gap_details, rank_gaps, top_gaps
from tests.fixtures.archetypes import TODAY, keyword_heavy
from tests.helpers import INJECTION

DS = "baseline-data-scientist-entry"


def subs(**overrides):
    out = {s: SubScore("scored", 1.0) for s in config.SUBCATEGORY_DIMENSION}
    out.update(overrides)
    return out


# --- Ranking math (04 §7) --------------------------------------------------------------------

def test_gain_is_effective_weight_times_shortfall_after_na_renormalization():
    s = subs(relevance=SubScore("scored", 0.5), progression=SubScore("na", None),
             recognition=SubScore("missing", 0.0, guidance="Add awards."))
    gaps = {g.subcategory: g for g in rank_gaps(s)}
    rel = gaps["relevance"]
    assert rel.effective_weight == pytest.approx(0.30 * 35 / 90)       # progression's weight redistributed
    assert rel.gain == pytest.approx(100 * 0.30 * 35 / 90 * 0.5)
    assert gaps["recognition"].gain == pytest.approx(100 * 0.20 * 0.10) and gaps["recognition"].status == "missing"
    assert gaps["recognition"].guidance == "Add awards."
    assert list(gaps) == ["relevance", "recognition"]                   # sorted by gain, full scores skipped
    assert rel.gain_text == f"up to {rel.gain:.1f} points"


def test_na_and_error_are_never_gaps():
    s = subs(scope=SubScore("na", None), depth=SubScore("error", None), recency=SubScore("scored", 0.2))
    names = [g.subcategory for g in rank_gaps(s)]
    assert names == ["recency"]


def test_weight_change_reranks_without_any_model():
    s = subs(relevance=SubScore("scored", 0.0), proof_density=SubScore("scored", 0.0))
    assert rank_gaps(s)[0].subcategory == "relevance"                   # 0.3 × 0.35 > 0.2 × 0.3158
    w = config.default_weights()
    w["dimensions"]["experience"] = 0
    assert [g.subcategory for g in rank_gaps(s, w)] == ["proof_density"]


def test_top_gaps_returns_five_largest():
    s = {name: SubScore("scored", 0.0) for name in config.SUBCATEGORY_DIMENSION}
    top = top_gaps(s)
    assert len(top) == 5 and top == rank_gaps(s)[:5]
    assert all(a.gain >= b.gain for a, b in zip(top, top[1:]))


# --- Details passed to the report ---------------------------------------------------------------

def test_details_list_unmet_criteria_and_evidence():
    details = {
        "required": {"score": 0.5, "items": {"Python": "used", "SQL": "listed"}, "failed": []},
        "preferred": {"score": 0.0, "items": {"Git": "missing"}, "failed": []},
        "judgments": [
            {"question": "tool_evidence", "label": "used", "quote": "Built X in Python", "subject": "Python: ..."},
            {"question": "tool_evidence", "label": "listed", "quote": None, "subject": "SQL: ..."},
            {"question": "tool_evidence", "label": "missing", "quote": None, "subject": "Git: ..."},
        ],
    }
    d = gap_details("tools", {"details": details})
    assert d["unmet_criteria"] == [{"criterion": "SQL", "priority": "required", "label": "listed"},
                                   {"criterion": "Git", "priority": "preferred", "label": "missing"}]
    assert d["best_evidence"][0]["quote"] == "Built X in Python"
    assert d["best_evidence"][1]["quote"] == "SQL: ..."                  # no quote → judged text instead
    assert d["weakest_items"] == [{"question": "tool_evidence", "label": "missing", "item": "Git: ..."}]


def test_validation_strength_gap_lists_relevant_certifications(tmp_path):
    conn = db.init_app_db(tmp_path / "t.db")
    d = gap_details("validation_strength", {"details": {}}, db.get_baseline(conn, DS))
    assert d["relevant_certifications"][0].startswith("Data science or analytics certification")


# --- Improvement plan -------------------------------------------------------------------------

from scoring.plan import Action, Plan, build_user_message, generate_plan, load_plan, validate_action  # noqa: E402


class ScriptedPlanner:
    """Fake StructuredLLM: returns the scripted plans in order (or raises), records each call."""

    def __init__(self, *plans):
        self.plans, self.calls = list(plans), []

    def parse(self, *, model, system, user, output_type):
        assert output_type is Plan
        self.calls.append({"model": model, "system": system, "user": user})
        p = self.plans.pop(0)
        if isinstance(p, Exception):
            raise p
        return p


def act(type_="build_project", title="Churn experiment project", addresses=("applied_knowledge",),
        covers=("Experimentation / A/B testing",), what="Build it.", builds_on="Python and SQL"):
    return Action(type=type_, title=title, what_to_do=what, must_show=["public repo with README"],
                  addresses=list(addresses), covers_criteria=list(covers), builds_on=builds_on)


def plan(*actions, quick_wins=(), not_actionable=()):
    return Plan(actions=list(actions), quick_wins=list(quick_wins), not_actionable=list(not_actionable))


GAP_SUBS = dict(applied_knowledge=SubScore("scored", 0.0), tools=SubScore("scored", 0.5),
                proof_density=SubScore("scored", 0.0), validation_strength=SubScore("missing", 0.0, guidance="g"),
                recognition=SubScore("missing", 0.0, guidance="g"))   # exactly five gaps → all passed to the model


@pytest.fixture
def conn(tmp_path):
    c = db.init_app_db(tmp_path / "t.db")
    yield c
    c.close()


def _profile():
    return Profile(roles=[RoleRec(title=f"Analyst {INJECTION}", employer="Foo", industry=None,
                                  start_date=date(2024, 1, 1), end_date=None, is_current=True, team_size=None,
                                  quote="x", evidence_id=None)])


def _gen(conn, planner, weights=None, baseline_id=DS, regenerate=False):
    return generate_plan(conn, "run-1", subs(**GAP_SUBS), weights or config.default_weights(),
                         db.get_baseline(conn, baseline_id), _profile(), planner, regenerate=regenerate)


def test_plan_prompt_has_role_gaps_and_candidate_in_tags(conn):
    planner = ScriptedPlanner(plan(act()))
    _gen(conn, planner)
    call = planner.calls[0]
    assert call["model"] == config.REASONING_MODEL and "career strategist" in call["system"]
    assert INJECTION not in call["system"]
    user = call["user"]
    for tag in ("<target_role>", "<scores>", "<gaps>", "<action_types>", "<candidate>"):
        assert tag in user
    assert INJECTION in user[user.index("<candidate>"): user.index("</candidate>")]
    role = user[user.index("<target_role>"): user.index("</target_role>")]
    assert "Data science or analytics certification (also: Google Data Analytics" in role
    assert "Complexity expectation:" in role and "Scope expectation:" in role
    gaps = user[user.index("<gaps>"): user.index("</gaps>")]
    assert '"key": "applied_knowledge"' in gaps and "potential" not in gaps   # no point numbers for the model


def test_points_are_code_sums_and_actions_ordered(conn):
    a1 = act(title="Small", addresses=["recognition"], type_="contribute_or_compete", covers=[])
    a2 = act(title="Big", addresses=["applied_knowledge", "tools", "proof_density"],
             covers=["Experimentation / A/B testing", "SQL"])
    res = _gen(conn, ScriptedPlanner(plan(a1, a2)))
    gains = {g.subcategory: g.gain for g in rank_gaps(subs(**GAP_SUBS))}
    assert [p.action.title for p in res.actions] == ["Big", "Small"]
    assert res.actions[0].points == pytest.approx(gains["applied_knowledge"] + gains["tools"] + gains["proof_density"])
    assert res.actions[1].points == pytest.approx(gains["recognition"])
    assert f"up to {res.actions[0].points:.1f} points if fully closed" in res.markdown
    assert "not a promised score" in res.markdown and not res.dropped


@pytest.mark.parametrize("bad,expect", [
    (act(covers=["Spark"]), "'Spark' is not a skill, tool or certification in the baseline"),
    (act(type_="earn_certification", title="AWS Solutions Architect", addresses=["validation_strength"], covers=[]),
     "not one of the baseline's certification criteria"),
    (act(addresses=["progression"]), "not among the gaps given"),
    (act(type_="get_reference", addresses=["tools"], covers=[], title="Ask your manager"),
     "cannot earn points for ['tools']"),
    (act(type_="get_reference", addresses=["validation_strength"], covers=[], title="Ask a datathon organizer",
         what="Ask the organizer or a teammate to vouch for you."), "manager or supervisor"),
])
def test_invalid_actions_rejected_after_one_retry(conn, bad, expect):
    good = act(title="Valid")
    planner = ScriptedPlanner(plan(bad, good), plan(bad, good))
    res = _gen(conn, planner)
    assert len(planner.calls) == 2 and expect in planner.calls[1]["user"]       # retry carries the error
    assert [p.action.title for p in res.actions] == ["Valid"]
    assert len(res.dropped) == 1 and expect in res.dropped[0]["errors"][0]


def test_retry_can_fix_the_plan_and_first_errors_are_kept(conn):
    planner = ScriptedPlanner(plan(act(covers=["Spark"])), plan(act(covers=["SQL"])))
    res = _gen(conn, planner)
    assert [p.action.covers_criteria for p in res.actions] == [["SQL"]] and not res.dropped
    assert "Spark" in res.first_attempt_errors[0]["errors"][0]
    assert load_plan(conn, "run-1", subs(**GAP_SUBS), config.default_weights(),
                     db.get_baseline(conn, DS)).first_attempt_errors == res.first_attempt_errors


def test_alias_names_are_mapped_to_baseline_names():
    b = db.get_baseline(db.init_app_db(":memory:"), DS)
    ok, errors = validate_action(act(covers=["sklearn", "A/B testing"]), {"applied_knowledge"}, b)
    assert not errors and ok.covers_criteria == ["scikit-learn", "Experimentation / A/B testing"]
    ok, errors = validate_action(act(type_="earn_certification", title="Google Data Analytics Certificate",
                                     addresses=["validation_strength"], covers=[]), {"validation_strength"}, b)
    assert not errors


def test_baseline_without_certification_criteria_yields_no_certification(conn):
    ds = db.get_baseline(conn, DS)
    no_certs = ds.model_copy(update={"id": None, "title": "DS no certs",
                                     "criteria": [c.model_copy(update={"id": None}) for c in ds.criteria
                                                  if c.kind != "certification"]})
    bid = db.create_baseline(conn, no_certs)
    cert = act(type_="earn_certification", title="Google Data Analytics Certificate",
               addresses=["validation_strength"], covers=[])
    planner = ScriptedPlanner(plan(cert, act()), plan(cert, act()))
    res = _gen(conn, planner, baseline_id=bid)
    assert "none (recommend no certification)" in planner.calls[0]["user"]
    assert all(p.action.type != "earn_certification" for p in res.actions)
    assert "no certification criteria" in res.dropped[0]["errors"][0]


def test_weight_change_reprices_stored_plan_without_model_call(conn):
    a1 = act(title="Recognition", addresses=["recognition"], type_="contribute_or_compete", covers=[])
    a2 = act(title="Skills", addresses=["applied_knowledge"])
    first = _gen(conn, ScriptedPlanner(plan(a1, a2)))
    assert [p.action.title for p in first.actions] == ["Skills", "Recognition"]
    w = config.default_weights()
    w["dimensions"]["expertise"], w["dimensions"]["authority"] = 1, 90
    silent = ScriptedPlanner()                                   # would fail if called
    again = generate_plan(conn, "run-1", subs(**GAP_SUBS), w, db.get_baseline(conn, DS), _profile(), silent)
    assert again.cached and silent.calls == []
    gains = {g.subcategory: g.gain for g in rank_gaps(subs(**GAP_SUBS), w)}
    assert [p.action.title for p in again.actions] == ["Recognition", "Skills"]
    assert again.actions[0].points == pytest.approx(gains["recognition"])
    assert again.actions[0].points != pytest.approx(first.actions[1].points)
    assert load_plan(conn, "run-1", subs(**GAP_SUBS), w, db.get_baseline(conn, DS)).markdown == again.markdown


def test_regenerate_calls_model_again_and_failure_is_recorded(conn):
    planner = ScriptedPlanner(plan(act(title="One")), plan(act(title="Two")), LLMError("Sonnet unavailable"))
    _gen(conn, planner)
    assert [p.action.title for p in _gen(conn, planner, regenerate=True).actions] == ["Two"]
    failed = _gen(conn, planner, regenerate=True)
    assert failed.error and "unavailable" in failed.error and failed.gaps
    assert [p.action.title for p in _gen(conn, ScriptedPlanner()).actions] == ["Two"]  # last good plan kept


def test_quick_wins_capped_at_three(conn):
    res = _gen(conn, ScriptedPlanner(plan(act(), quick_wins=["a", "b", "c", "d"], not_actionable=["Progression"])))
    assert res.quick_wins == ["a", "b", "c"] and res.dropped[0]["type"] == "quick_win"
    assert "### Not closable by a near-term action\n- Progression" in res.markdown


def test_no_planner_still_ranks(conn):
    res = _gen(conn, None)
    assert res.gaps and res.markdown is None and res.error


# --- In the graph ----------------------------------------------------------------------------------

def test_pipeline_ranks_gaps_and_writes_plan(tmp_path):
    from graph.pipeline import current_plan, regenerate_plan
    path = str(tmp_path / "g.db")
    c = db.init_app_db(path)
    cid, judge = keyword_heavy(c)
    planner = ScriptedPlanner(plan(act(addresses=["applied_knowledge"])), plan(act(title="Again",
                                                                                    addresses=["applied_knowledge"])))
    res = Pipeline(Deps(path, judge, today=TODAY, planner=planner)).score(cid, DS)
    # Finish late (specs/09 §2.6): the score comes back first; the plan is written in the background.
    assert res["final"] is not None and res["plan_status"] == "generating" and res["plan_markdown"] is None
    written = Pipeline.wait_for_plan(res["run_id"])
    assert written.error is None and "Churn experiment project" in written.markdown
    assert c.execute("SELECT plan_status FROM runs WHERE id = ?", (res["run_id"],)).fetchone()[0] == "ready"
    assert res["ranked_gaps"] and all(g["gain_text"].startswith("up to ") for g in res["ranked_gaps"])
    w = config.default_weights()
    w["dimensions"]["authority"] = 0
    assert all(g.dimension != "authority" for g in run_gaps(c, res["run_id"], w))
    repriced = current_plan(c, res["run_id"], w)
    assert repriced.cached and len(planner.calls) == 1
    assert regenerate_plan(c, res["run_id"], planner, w).actions[0].action.title == "Again"
    c.close()


def test_pipeline_without_planner_still_returns_gaps(tmp_path):
    path = str(tmp_path / "g.db")
    c = db.init_app_db(path)
    cid, judge = keyword_heavy(c)
    c.close()
    res = Pipeline(Deps(path, judge, today=TODAY)).score(cid, DS)
    assert res["ranked_gaps"] and res["plan_markdown"] is None and res["plan_error"]
