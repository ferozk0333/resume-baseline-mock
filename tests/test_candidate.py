"""Candidate experience, phase A (specs/17 §2, §3, §8, §12): onboarding state, target roles, baseline scores with
a deferred plan, and the "fill once" application details. FakeJudge and fixtures only: no network."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from services import candidate as cx
from api.app import create_app
from core import config, db
from graph.context import Deps
from graph.pipeline import Pipeline
from judges.fake_judge import FakeJudge
from tests import ui_fakes
from tests.helpers import SAMPLE_RESUME_LINES, FakeLLM, make_pdf, sample_resume_extract

DS = "baseline-data-scientist-entry"
ROOT = Path(__file__).resolve().parent.parent

# Distinctive values: none may ever reach a model or a recruiter response.
SENTINELS = {"country": "Zyxlandia", "location": "Quuxville", "link": "https://sentinel-eeo.example/me"}
DETAILS = {
    "work_authorization": {"country": SENTINELS["country"], "authorized": "yes", "sponsorship_now": "no",
                           "sponsorship_future": "yes"},
    "questions": {"earliest_start": "2026-11-02", "notice_period": "2_weeks", "salary_min": 987654,
                  "salary_currency": "eur", "relocate": "no", "work_mode": "hybrid",
                  "locations": [SENTINELS["location"]], "links": [SENTINELS["link"]]},
    "eeo": {"gender": "non_binary", "race_ethnicity": "two_or_more", "veteran_status": "protected_veteran",
            "disability_status": "yes"},
}


class RecordingPlanner(ui_fakes.AlwaysPlanner):
    def __init__(self):
        self.prompts: list[str] = []

    def parse(self, *, model, system, user, output_type):
        self.prompts.append(system + user)
        return super().parse(model=model, system=system, user=user, output_type=output_type)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    path = str(tmp_path / "cx.db")
    db.init_app_db(path).close()
    judge = FakeJudge(script=ui_fakes.best)
    llm = FakeLLM({"resume": sample_resume_extract()})
    planner = RecordingPlanner()
    app = create_app(Deps(path, judge, llm=llm, planner=planner))
    with TestClient(app) as client:
        client.tasks = app.state.tasks
        yield type("Env", (), {"api": client, "judge": judge, "llm": llm, "planner": planner, "path": path})


ORIGINAL_ROLES = ("baseline-ai-engineer-entry", "baseline-data-scientist-entry", "baseline-product-manager-entry")


def roles(api) -> list[str]:
    """The three original roles, in the order the API lists them (a candidate targets at most 3)."""
    return [b["id"] for b in api.get("/baselines").json() if b["id"] in ORIGINAL_ROLES]


def new_profile(api, name="Ada"):
    cid = api.post("/candidates", json={"name": name}).json()["id"]
    files = {"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")}
    r = api.post(f"/candidates/{cid}/onboard", data={"onboarding": "true"}, files=files)
    assert r.status_code == 202, r.text
    return cid, r.json()["task_id"]


def scored(env, picks: list[str]):
    api = env.api
    cid, read = new_profile(api)
    assert api.put(f"/candidates/{cid}/targets", json={"baseline_ids": picks}).status_code == 200
    assert api.tasks.wait(read)["status"] == "done"
    assert api.put(f"/candidates/{cid}/onboarding", json={"step": "review"}).status_code == 200
    r = api.post(f"/candidates/{cid}/baseline-scores")
    assert r.status_code == 202, r.text
    task = api.tasks.wait(r.json()["task_id"])
    assert task["status"] == "done", task
    for run_id in task["result"]["runs"].values():
        Pipeline.wait_for_plan(run_id)
    return cid, task["result"]["runs"]


# --- Onboarding: resumable at every step ---------------------------------------------------------------------

def test_onboarding_is_resumable_at_each_step(env):
    api = env.api
    cid = api.post("/candidates", json={"name": "Ada"}).json()["id"]
    assert api.get(f"/candidates/{cid}/onboarding").json()["onboarding"]["step"] == "proof"
    files = {"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")}
    read = api.post(f"/candidates/{cid}/onboard", data={"onboarding": "true"}, files=files).json()["task_id"]
    state = api.get(f"/candidates/{cid}/onboarding").json()
    assert state["onboarding"]["step"] == "roles" and state["onboarding"]["read_task_id"] == read
    api.tasks.wait(read)
    state = api.get(f"/candidates/{cid}/onboarding").json()       # a reload re-attaches to the finished task
    assert state["read_task"]["status"] == "done" and state["awaiting_review"]
    assert api.put(f"/candidates/{cid}/onboarding", json={"step": "review"}).status_code == 409   # no role yet
    api.put(f"/candidates/{cid}/targets", json={"baseline_ids": [DS]})
    for step in ("review", "scores", "details", "done"):
        assert api.put(f"/candidates/{cid}/onboarding", json={"step": step}).json()["step"] == step
        assert api.get(f"/candidates/{cid}/onboarding").json()["onboarding"]["step"] == step
    assert api.get(f"/candidates/{cid}/onboarding").json()["onboarding"]["finished_at"]


def test_progress_lines_are_plain_language(env):
    api = env.api
    cid, read = new_profile(api)
    api.tasks.wait(read)
    msgs = [e["message"] for e in api.tasks.events_after(read, 0)]
    assert "Finding your roles, projects and skills…" in msgs
    assert not any("model" in m.lower() or "judg" in m.lower() for m in msgs), msgs
    api.put(f"/candidates/{cid}/targets", json={"baseline_ids": [DS]})
    t = api.post(f"/candidates/{cid}/baseline-scores").json()["task_id"]
    api.tasks.wait(t)
    msgs = [e["message"] for e in api.tasks.events_after(t, 0)]
    assert "Scoring your Data Scientist profile" in msgs, msgs
    assert not any("judg" in m.lower() or "model" in m.lower() or " of 19" in m for m in msgs), msgs


def test_roles_chosen_during_extraction_are_scored_primary_first(env):
    picks = roles(env.api)[:2]
    other, first = picks
    cid, runs = scored(env, [other, first])                 # picked while the documents were being read
    assert set(runs) == {other, first}
    with db.connect(env.path) as c:
        got = {r["baseline_id"] for r in c.execute("SELECT baseline_id FROM runs WHERE candidate_id = ?", (cid,))}
    assert got == {other, first}
    cards = env.api.get(f"/candidates/{cid}/baseline-scores").json()
    assert [c["baseline_id"] for c in cards] == [other, first] and cards[0]["is_primary"]
    assert env.api.get(f"/candidates/{cid}/onboarding").json()["onboarding"]["confirmed_at"]


def test_primary_plan_only_during_onboarding_other_roles_lazy(env):
    picks = roles(env.api)[:3]
    cid, runs = scored(env, picks)
    assert len(env.planner.prompts) == 1                    # the primary role's plan only
    cards = {c["baseline_id"]: c for c in env.api.get(f"/candidates/{cid}/baseline-scores").json()}
    assert cards[picks[0]]["plan_status"] == "ready" and cards[picks[0]]["actions"]
    assert all(cards[b]["plan_status"] == "deferred" and not cards[b]["actions"] for b in picks[1:])
    assert all(cards[b]["gaps"] for b in picks[1:])         # code-ranked gaps meanwhile
    r = env.api.post(f"/runs/{runs[picks[1]]}/plan").json()   # first open of that role's plan
    assert r["status"] == "generating" and r["task_id"]
    assert env.api.tasks.wait(r["task_id"])["status"] == "done"
    assert len(env.planner.prompts) == 2
    assert env.api.post(f"/runs/{runs[picks[1]]}/plan").json() == {"status": "ready", "task_id": None}
    assert len(env.planner.prompts) == 2                    # opened again: no new call
    with db.connect(env.path) as c:
        assert c.execute("SELECT plan_status FROM runs WHERE id = ?", (runs[picks[2]],)).fetchone()[0] == "deferred"


def test_adding_a_role_reuses_job_independent_judgments(env):
    picks = roles(env.api)
    cid, _ = scored(env, [picks[0]])
    env.api.put(f"/candidates/{cid}/onboarding", json={"step": "done"})
    with db.connect(env.path) as c:
        before = c.execute("SELECT COUNT(*) FROM judgments WHERE candidate_id = ? AND baseline_id IS NULL",
                           (cid,)).fetchone()[0]
    assert before > 0
    calls = len(env.judge.calls)
    r = env.api.post(f"/candidates/{cid}/targets", json={"baseline_id": picks[1]}).json()
    assert [t["baseline_id"] for t in r["targets"]] == [picks[0], picks[1]]
    task = env.api.tasks.wait(r["task_id"])
    assert task["status"] == "done"
    with db.connect(env.path) as c:
        after = c.execute("SELECT COUNT(*) FROM judgments WHERE candidate_id = ? AND baseline_id IS NULL",
                          (cid,)).fetchone()[0]
        plan = c.execute("SELECT plan_status FROM runs WHERE id = ?", (task["result"]["runs"][picks[1]],)).fetchone()[0]
    assert after == before                                  # nothing job-independent asked again
    asked = {k for k, _ in env.judge.calls[calls:]}
    from judges.questions import QUESTIONS
    assert asked and all(QUESTIONS[k].job_dependent for k in asked), asked
    assert plan == "deferred" and len(env.planner.prompts) == 1


def test_baseline_score_never_applies_to_a_job(env):
    from services import recruiter as rc
    with db.connect(env.path) as c:
        rc.create_job(c, DS, "Data Scientist", "Demo Co")
    cid, _ = scored(env, [DS])
    with db.connect(env.path) as c:
        assert c.execute("SELECT COUNT(*) FROM applications WHERE candidate_id = ?", (cid,)).fetchone()[0] == 0


# --- Target roles ------------------------------------------------------------------------------------------------

def test_targets_one_to_three_role_baselines_one_primary(env):
    api = env.api
    ids = roles(api)
    cid = api.post("/candidates", json={"name": "Ada"}).json()["id"]
    assert api.put(f"/candidates/{cid}/targets", json={"baseline_ids": []}).status_code == 422
    assert api.put(f"/candidates/{cid}/targets", json={"baseline_ids": [*ids, ids[0] + "-x"]}).status_code == 422
    assert api.put(f"/candidates/{cid}/targets", json={"baseline_ids": ["nope"]}).status_code == 422
    t = api.put(f"/candidates/{cid}/targets", json={"baseline_ids": ids[:2]}).json()
    assert [x["is_primary"] for x in t] == [True, False]
    assert api.get("/candidates").json()[0]["target_role"] == t[0]["title"]
    t = api.post(f"/candidates/{cid}/targets/{ids[1]}/primary").json()
    assert t[0]["baseline_id"] == ids[1] and t[0]["is_primary"]
    t = api.delete(f"/candidates/{cid}/targets/{ids[1]}").json()            # removing the primary
    assert [(x["baseline_id"], x["is_primary"]) for x in t] == [(ids[0], True)]
    assert api.delete(f"/candidates/{cid}/targets/{ids[0]}").status_code == 422   # keep one


def test_a_job_baseline_is_not_a_target_role(env):
    from services import recruiter as rc
    with db.connect(env.path) as c:
        c.execute("INSERT INTO baselines (id, title, level, required_years, expected_reach, is_mock, owner_job_id) "
                  "VALUES ('job-b', 'Job', 'entry', 1, 2, 0, 'j1')")
        c.commit()
        rc.create_job(c, DS, "x", "y")
        assert "job-b" not in cx.role_baselines(c)


# --- Results card ------------------------------------------------------------------------------------------------

def test_results_card_has_score_percentile_state_dimensions_and_top_actions(env):
    cid, runs = scored(env, [DS])
    card = env.api.get(f"/candidates/{cid}/baseline-scores").json()[0]
    assert card["status"] == "scored" and card["final"] is not None
    assert set(card["dimensions"]) == {"experience", "expertise", "authority", "trust"}
    assert card["percentile"]["shown"] is False and card["percentile"]["min_pool"] == config.MIN_POOL
    assert 1 <= len(card["actions"]) <= 3 and all("points_text" in a for a in card["actions"])
    assert card["thin"] == (card["final"] < cx.THIN_SCORE)
    text = json.dumps(card)
    assert "applied_knowledge" not in text and "role_skills" not in text      # no raw subcategory keys


# --- Application details: fill once; never to a model, a recruiter, scoring or pools -----------------------------

def test_details_default_to_decline_and_validate(env):
    api = env.api
    cid = api.post("/candidates", json={"name": "Ada"}).json()["id"]
    d = api.get(f"/candidates/{cid}/application-details").json()
    assert set(d["eeo"].values()) == {"decline"} and d["saved_at"] is None
    bad = {"eeo": {"gender": "robot"}}
    assert api.put(f"/candidates/{cid}/application-details", json=bad).status_code == 422
    assert api.put(f"/candidates/{cid}/application-details",
                   json={"questions": {"links": ["ftp://x"]}}).status_code == 422
    saved = api.put(f"/candidates/{cid}/application-details", json={"questions": {"salary_min": 90000}}).json()
    assert saved["saved_at"] and saved["eeo"]["gender"] == "decline" and saved["questions"]["salary_min"] == 90000
    saved = api.put(f"/candidates/{cid}/application-details", json=DETAILS).json()
    assert saved["questions"]["salary_currency"] == "EUR" and saved["eeo"] == DETAILS["eeo"]


def test_dismissing_hides_the_finish_card(env):
    api = env.api
    cid = api.post("/candidates", json={"name": "Ada"}).json()["id"]
    with db.connect(env.path) as c:
        assert cx.finish_profile_card(c, cid)
    d = api.post(f"/candidates/{cid}/application-details/dismiss").json()
    assert d["dismissed_at"] and d["saved_at"] is None
    with db.connect(env.path) as c:
        assert not cx.finish_profile_card(c, cid)


def _model_inputs(env) -> str:
    judged = [json.dumps(ctx, default=str) for _, ctx in env.judge.calls]
    return "\n".join([*judged, *(c["system"] + c["user"] for c in env.llm.calls), *env.planner.prompts])


def test_details_never_reach_a_model(env):
    api = env.api
    cid = api.post("/candidates", json={"name": "Ada"}).json()["id"]
    api.put(f"/candidates/{cid}/application-details", json=DETAILS)        # saved before anything is read
    files = {"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")}
    read = api.post(f"/candidates/{cid}/onboard", data={"onboarding": "true"}, files=files).json()["task_id"]
    api.put(f"/candidates/{cid}/targets", json={"baseline_ids": roles(api)[:2]})
    api.tasks.wait(read)
    t = api.post(f"/candidates/{cid}/baseline-scores").json()["task_id"]
    for run_id in api.tasks.wait(t)["result"]["runs"].values():
        Pipeline.wait_for_plan(run_id)
        r = api.post(f"/runs/{run_id}/plan").json()
        if r["task_id"]:
            api.tasks.wait(r["task_id"])
    sent = _model_inputs(env)
    assert env.llm.calls and env.judge.calls and len(env.planner.prompts) == 2
    for v in [*SENTINELS.values(), "987654", "non_binary", "protected_veteran", "two_or_more", "sponsorship"]:
        assert v not in sent, v


def test_details_never_in_recruiter_responses(env):
    from services import recruiter as rc
    api = env.api
    with db.connect(env.path) as c:
        job = rc.create_job(c, DS, "Data Scientist", "Demo Co")
    cid, _ = scored(env, [DS])
    api.put(f"/candidates/{cid}/application-details", json=DETAILS)
    t = api.post(f"/candidates/{cid}/apply", json={"job_id": job})
    assert t.status_code == 202, t.text
    api.tasks.wait(t.json()["task_id"])
    app_id = api.get(f"/jobs/{job}/applicants").json()["applicants"][0]["application_id"]
    paths = [f"/jobs/{job}/applicants", f"/applications/{app_id}", f"/jobs/{job}/compare?ids={app_id},{app_id}", f"/jobs/{job}/pipeline",
             f"/applications/{app_id}/activity", f"/applications/{app_id}/workflow", "/recruiter/jobs",
             "/recruiter/tasks", "/emails", f"/jobs/{job}", "/candidates", f"/candidates/{cid}/profile"]
    for path in paths:
        r = api.get(path)
        assert r.status_code in (200, 404), (path, r.status_code)
        body = r.text
        for v in [*SENTINELS.values(), "987654", "non_binary", "protected_veteran", "eeo", "work_authorization"]:
            assert v not in body, (path, v)


def test_details_never_change_scores_or_pools(env):
    from scoring import benchmarks
    cid, runs = scored(env, [DS])
    run_id = runs[DS]
    with db.connect(env.path) as c:
        before = (env.api.get(f"/runs/{run_id}").json(), benchmarks.for_run(c, run_id),
                  [m.final for m in benchmarks.baseline_pool(c, DS)])
    env.api.put(f"/candidates/{cid}/application-details", json=DETAILS)
    with db.connect(env.path) as c:
        after = (env.api.get(f"/runs/{run_id}").json(), benchmarks.for_run(c, run_id),
                 [m.final for m in benchmarks.baseline_pool(c, DS)])
    assert before == after


def test_only_the_candidate_module_reads_application_details():
    """Scoring, judging, extraction, pools, the plan, the recruiter side and the market never touch the table."""
    allowed = {ROOT / "services" / "candidate.py", ROOT / "core" / "db.py",
               ROOT / "scripts" / "purge_person.py"}                     # deletes a person's rows; reads nothing out
    hits = []
    for folder in ("api", "services", "workflows", "providers", "core", "scoring", "judges", "extraction", "graph",
                   "market", "ingest", "scripts"):
        for f in (ROOT / folder).rglob("*.py"):
            if f not in allowed and ("candidate_application_details" in (t := f.read_text()) or "eeo_json" in t):
                hits.append(str(f.relative_to(ROOT)))
    assert hits == []
