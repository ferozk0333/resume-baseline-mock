"""Phase 10 API (specs/10 §6): every endpoint with FakeJudge and a fake LLM. No network."""
import json

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from core import config, db
from graph.context import Deps
from tests import ui_fakes
from tests.helpers import SAMPLE_RESUME_LINES, FakeLLM, make_pdf, sample_resume_extract

DS = "baseline-data-scientist-entry"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    path = str(tmp_path / "api.db")
    db.init_app_db(path).close()
    ui_fakes.CALLS.update(judge=0, planner=0)
    from judges.fake_judge import FakeJudge
    deps = Deps(path, FakeJudge(script=ui_fakes.best), llm=FakeLLM({"resume": sample_resume_extract()}),
                planner=ui_fakes.AlwaysPlanner())
    app = create_app(deps)
    with TestClient(app) as client:
        client.tasks = app.state.tasks
        yield client


def wait(api, task_id):
    return api.tasks.wait(task_id)


def onboard(api, **form):
    cid = api.post("/candidates", json={"name": "Jane", "target_role": "Data Scientist"}).json()["id"]
    files = {"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")}
    r = api.post(f"/candidates/{cid}/onboard", data={"baseline_id": DS, **form}, files=files)
    assert r.status_code == 202, r.text
    task = wait(api, r.json()["task_id"])
    return cid, task


def scored(api):
    cid, _ = onboard(api)
    task = wait(api, api.post(f"/candidates/{cid}/score", json={"baseline_id": DS}).json()["task_id"])
    assert task["status"] == "done", task
    from graph.pipeline import Pipeline
    Pipeline.wait_for_plan(task["result"]["run_id"])
    return cid, task["result"]["run_id"]


# --- Baselines ---------------------------------------------------------------------------------------------

def test_baseline_crud_and_reset(api):
    seeds = api.get("/baselines").json()
    assert {b["id"] for b in seeds} >= {DS}
    new = {**api.get(f"/baselines/{DS}").json(), "id": None, "title": "Analyst"}
    new["criteria"] = [{**c, "id": None} for c in new["criteria"]]
    created = api.post("/baselines", json=new).json()
    assert created["title"] == "Analyst" and created["id"] != DS
    updated = api.put(f"/baselines/{created['id']}", json={**created, "title": "Senior Analyst"}).json()
    assert updated["title"] == "Senior Analyst"
    assert api.delete(f"/baselines/{created['id']}").status_code == 204
    assert api.get(f"/baselines/{created['id']}").status_code == 404
    assert DS in api.post("/baselines/reset-seed").json()
    assert api.post("/baselines", json={"title": ""}).status_code == 422


# --- Onboarding, SSE, review ------------------------------------------------------------------------------

def test_onboard_streams_progress_and_stops_at_review(api):
    cid, task = onboard(api)
    assert task["status"] == "done" and task["message"] == "Ready for review"
    with api.stream("GET", f"/tasks/{task['id']}/events") as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [line.split(": ", 1)[1] for line in body.splitlines() if line.startswith("event: ")]
    assert events[0] == "progress" and events[-1] == "done"
    assert "Extracting with the model" in body
    profile = api.get(f"/candidates/{cid}/profile").json()
    assert [r["title"] for r in profile["profile"]["roles"]] == ["Data Analyst", "Analytics Intern"]
    assert profile["sources"][0]["type"] == "resume" and profile["blocker"] is None


def test_review_edits_add_edit_delete_and_add_back(api):
    from extraction import schemas
    from judges.fake_judge import FakeJudge
    extract = sample_resume_extract()
    extract.claims.append(schemas.Claim(kind="tool", name="Spark", bullet_ref=None,
                                        source=schemas.Source(quote="Spark", location="")))
    api.app.state.rs.deps.llm = FakeLLM({"resume": extract})
    cid, _ = onboard(api)
    prof = api.get(f"/candidates/{cid}/profile").json()
    role = prof["profile"]["roles"][0]
    r = api.patch(f"/candidates/{cid}/profile", json={"op": "edit", "item": "role", "id": role["id"], "data": {
        "title": "Senior Data Analyst", "employer": "Acme Retail", "start_date": "2023-01", "is_current": True,
        "bullets": [b["text"] for b in role["bullets"]] + ["Shipped a pricing dashboard."]}})
    assert r.status_code == 200
    roles = r.json()["profile"]["roles"]
    assert roles[0]["title"] == "Senior Data Analyst" and len(roles[0]["bullets"]) == 3
    added = api.patch(f"/candidates/{cid}/profile", json={"op": "add", "item": "claim",
                                                          "data": {"kind": "tool", "name": "dbt"}}).json()
    assert any(c["name_raw"] == "dbt" for c in added["profile"]["claims"])
    dropped = added["dropped"][0]
    back = api.patch(f"/candidates/{cid}/profile", json={"op": "add_back", "item": "dropped", "id": dropped["id"]})
    assert any(c["name_raw"] == "Spark" for c in back.json()["profile"]["claims"])
    cert = back.json()["profile"]["certifications"][0]
    gone = api.patch(f"/candidates/{cid}/profile", json={"op": "delete", "item": "certification", "id": cert["id"]})
    assert gone.json()["profile"]["certifications"] == []
    bad = api.patch(f"/candidates/{cid}/profile", json={"op": "add", "item": "role", "data": {"title": ""}})
    assert bad.status_code == 422


def test_demo_videos_are_validated_and_stored(api):
    cid, _ = onboard(api, demo_videos=json.dumps(["https://youtu.be/dQw4w9WgXcQ",
                                                  "https://www.loom.com/share/0123456789abcdef0123456789abcdef"]))
    links = api.get(f"/candidates/{cid}/profile").json()["media_links"]
    assert [(m["kind"], m["embed_url"]) for m in links] == [
        ("youtube", "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"),
        ("loom", "https://www.loom.com/embed/0123456789abcdef0123456789abcdef")]
    cid2 = api.post("/candidates", json={"name": "B"}).json()["id"]
    r = api.post(f"/candidates/{cid2}/onboard", data={"baseline_id": DS, "demo_videos": '["https://vimeo.com/1"]'},
                 files={"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")})
    assert r.status_code == 422 and "YouTube or Loom" in r.json()["detail"]


def test_unknown_ids_and_missing_resume(api):
    assert api.get("/candidates/nope/profile").status_code == 404
    assert api.get("/runs/nope").status_code == 404
    assert api.get("/tasks/nope").status_code == 404
    cid = api.post("/candidates", json={"name": "C"}).json()["id"]
    assert api.post(f"/candidates/{cid}/onboard", data={"baseline_id": DS}).status_code == 422


# --- Scoring, rescore, plan, rejudge ----------------------------------------------------------------------

def test_score_returns_run_with_dimensions_subcategories_and_evidence(api):
    cid, run_id = scored(api)
    run = api.get(f"/runs/{run_id}").json()
    assert 0 <= run["final"] <= 100 and set(run["dimensions"]) == set(config.DIMENSIONS)
    assert len(run["subcategories"]) == 19
    rel = next(s for s in run["subcategories"] if s["key"] == "ownership")
    assert rel["formula"] and rel["details"]["judgments"][0]["method"]
    assert any(r["id"] == run_id for r in api.get(f"/candidates/{cid}/runs").json())


def test_rescore_makes_no_model_calls(api):
    _, run_id = scored(api)
    before = dict(ui_fakes.CALLS)
    w = config.default_weights()
    w["dimensions"]["authority"] = 0
    out = api.post(f"/runs/{run_id}/rescore", json={"weights": w}).json()
    assert ui_fakes.CALLS == before
    assert out["weights"]["dimensions"]["authority"] == 0
    assert all(g["dimension"] != "authority" for g in out["gaps"])
    assert api.post(f"/runs/{run_id}/rescore", json={"weights": {"dimensions": {}}}).status_code == 422


def test_plan_is_ready_after_scoring_and_can_be_regenerated(api):
    _, run_id = scored(api)
    plan = api.get(f"/runs/{run_id}/plan").json()
    assert plan["status"] == "ready" and plan["actions"][0]["title"] == "Build an A/B test project"
    assert plan["actions"][0]["points_text"].startswith("up to ")
    task = wait(api, api.post(f"/runs/{run_id}/plan/regenerate", json={"weights": config.default_weights()})
                .json()["task_id"])
    assert task["status"] == "done" and ui_fakes.CALLS["planner"] == 2


def test_rejudge_clears_judgments_and_scores_again(api):
    _, run_id = scored(api)
    calls = ui_fakes.CALLS["judge"]
    task = wait(api, api.post(f"/runs/{run_id}/rejudge").json()["task_id"])
    assert task["status"] == "done" and task["result"]["run_id"] != run_id
    assert ui_fakes.CALLS["judge"] > calls


def test_errors_are_clean_messages(api, monkeypatch):
    cid = api.post("/candidates", json={"name": "D"}).json()["id"]
    r = api.post(f"/candidates/{cid}/score", json={"baseline_id": DS})
    assert r.status_code == 409 and r.json()["detail"]
    import graph.pipeline as gp
    monkeypatch.setattr(gp.Pipeline, "start_onboarding", lambda *a, **k: 1 / 0)
    cid, task = onboard(api)
    assert task["status"] == "error" and "ZeroDivisionError" in task["error"] and "Traceback" not in task["error"]
