"""specs/18 Phase 2 flags (all default off): grouped item judging, the repository cap, Gemma / Groq in routine
judging. Off = today's behaviour; on = the stated call pattern. FakeJudge, offline."""
from datetime import date

from core import config, db, routing
from extraction import store
from extraction.records import BulletRec, ProjectRec
from graph.context import Deps, _capped
from graph.pipeline import Pipeline
from judges.claude_judge import batch_output_model
from judges.fake_judge import FakeJudge
from tests import ui_fakes
from tests.fixtures.archetypes import ARCHETYPES, TODAY

DS = "baseline-data-scientist-entry"


def quoted(q, ctx):
    """First option, with a quote from the input so batched answers pass the quote check."""
    from judges.questions import QUESTIONS
    text = str(ctx.get("item") or ctx.get("work_sample") or "")
    return (list(QUESTIONS[q].options)[0], text[:20]) if text else ui_fakes.best(q, ctx)


def test_flags_are_off_by_default():
    assert not (config.GROUPED_ITEM_JUDGING or config.REPO_CAP or config.GEMMA_JUDGE or config.GROQ_JUDGE)
    assert config.MAX_REPOS == 8


def test_grouped_item_judging_batches_complexity_only(tmp_path, monkeypatch):
    def run(on: bool):
        monkeypatch.setattr(config, "GROUPED_ITEM_JUDGING", on)
        path = str(tmp_path / f"g{on}.db")
        conn = db.init_app_db(path)
        cid, _ = ARCHETYPES["strong_ds"](conn)
        judge = FakeJudge(script=quoted)
        Pipeline(Deps(path, judge, today=TODAY, planner=None)).score(cid, DS, plan=False)
        return {k for k, _ in judge.batches}, {k for g in judge.groups for k in g}, \
            [k for k, _ in judge.calls].count("work_sample_relevant")
    off, on = run(False), run(True)
    assert "complexity" not in off[0] and "complexity" in on[0]
    assert "work_sample_relevant" not in on[0] and on[2] == off[2]           # work samples: unchanged path
    fields = list(batch_output_model("complexity").model_fields["answers"].annotation.__args__[0].model_fields)
    assert fields.index("reasoning") < fields.index("label")                      # complexity: reason first


def test_repo_cap_judges_the_most_relevant_repositories(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MAX_REPOS", 2)
    conn = db.init_app_db(str(tmp_path / "r.db"))
    baseline = db.get_baseline(conn, DS)
    cid = store.create_candidate(conn, "Ada")
    profile = store.load_profile(conn, cid)
    for i, (name, desc, pushed) in enumerate([("garden", "Watering plans", date(2026, 9, 1)),
                                              ("churn", "Churn model in Python and SQL with A/B testing", date(2025, 1, 1)),
                                              ("notes", "Course notes", date(2026, 10, 1)),
                                              ("forecast", "Demand forecasting in Python", date(2024, 1, 1))]):
        profile.projects.append(ProjectRec(kind="repo", name=name, description=desc, url=None, start_date=None,
                                           end_date=pushed, quote=name, evidence_id=None, is_fork=False, own_commits=3,
                                           bullets=[BulletRec(text=desc, location=None, evidence_id=None)]))
    kept = [p.name for p in _capped(profile, baseline, {}).projects]
    assert kept == ["churn", "forecast"]                                       # by relevance, not by recency
    monkeypatch.setattr(config, "MAX_REPOS", 3)
    assert [p.name for p in _capped(profile, baseline, {}).projects] == ["churn", "notes", "forecast"]   # tie: latest push


def test_routine_judging_turns_and_groq_never_in_grouped_calls(monkeypatch):
    single = config.ROUTES["judge_single"]
    grouped = config.ROUTES["judge_grouped"]
    assert routing.routine_order("judge_single", single) == single                 # flags off: today's order
    monkeypatch.setattr(config, "GROQ_JUDGE", True)
    firsts = {routing.routine_order("judge_single", single)[0] for _ in range(10)}
    assert any(m.startswith("groq/") for m in firsts) and any("flash-lite" in m for m in firsts)
    assert all(not routing.routine_order("judge_grouped", grouped)[0].startswith("groq/") for _ in range(10))
    assert routing.routine_order("extract_web", config.ROUTES["extract_web"]) == config.ROUTES["extract_web"]
    monkeypatch.setattr(config, "GROQ_JUDGE", False)
    monkeypatch.setattr(config, "GEMMA_JUDGE", True)
    assert any("/gemma-" in routing.routine_order("judge_grouped", grouped)[0] for _ in range(10))
