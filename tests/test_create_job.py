"""specs/14: create a job. Fixed JD fixture with a fake model; FakeJudge; no network."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from core import config, db
from core.llm import LLMError
from extraction import job_reader as jr
from graph.context import Deps
from judges.fake_judge import FakeJudge
from tests import ui_fakes
from tests.helpers import SAMPLE_RESUME_LINES, FakeLLM, brightwater_extract as fake_extract, make_pdf, \
    sample_resume_extract

JD = (Path(__file__).parent / "fixtures" / "jd" / "brightwater.txt").read_text()
JD_PDF = Path(__file__).parent / "fixtures" / "jd" / "AI_Engineer_JD_Brightwater.pdf"


class JobModel:
    def __init__(self, out=None, fail=False):
        self.out, self.fail, self.calls = out, fail, []

    def parse(self, *, model, system, user, output_type):
        self.calls.append(model)
        if self.fail:
            raise LLMError("daily quota reached")
        assert "<job_description>" in user and output_type is jr.JobExtract
        return self.out or fake_extract()


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    path = str(tmp_path / "j.db")
    db.init_app_db(path).close()
    return make(path, JobModel())


def make(path, job_llm):
    app = create_app(Deps(path, FakeJudge(script=ui_fakes.best), llm=FakeLLM({"resume": sample_resume_extract()}),
                          planner=ui_fakes.AlwaysPlanner(), job_llm=job_llm))
    client = TestClient(app)
    client.tasks = app.state.tasks
    return client


def read(api, **kw):
    r = api.post("/jobs/drafts/read", data={"text": JD} if not kw else {}, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def reqs(form):
    return {r["name"]: r for r in form["draft"]["requirements"]}


# --- Years and level ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,years", [("3+ years of experience", 3), ("2–4 years", 2), ("2-4 years", 2),
                                        ("new grad", 0), ("0–1 years", 0), ("1+ years of professional experience", 1),
                                        ("one year", 1), ("5 or more years", 5), ("no number here", None)])
def test_years(text, years):
    assert jr.parse_years(text) == years


@pytest.mark.parametrize("text,years,level", [("Early career", 1, "entry"), ("Senior", None, "senior"),
                                              ("Staff Engineer", None, "lead"), ("Mid-level", None, "mid"),
                                              (None, 0, "entry"), (None, 4, "mid"), (None, 7, "senior"), (None, 12, "lead")])
def test_level(text, years, level):
    assert jr.map_level(text, years) == level


# --- Reading a JD (fake model) ----------------------------------------------------------------------------------------

def test_extraction_is_checked_split_and_sectioned():
    out = jr.check(fake_extract(), JD)
    names = [r["name"] for r in out["requirements"]]
    assert "Docker" in names and "CI/CD" in names and "Docker and basic CI/CD" not in names        # shared split rules
    assert {"pgvector", "Pinecone", "Weaviate"} <= set(names)
    assert "Hyperparameter tuning" in names
    by = {r["name"]: r for r in out["requirements"]}
    assert by["Hyperparameter tuning"]["priority"] == "preferred"            # the Preferred section wins
    assert by["Python"]["priority"] == "required" and by["Docker"]["priority"] == "preferred"
    cert = by["Cloud ML certification"]
    assert cert["kind"] == "certification" and len(cert["aliases"]) == 2 and cert["priority"] == "preferred"
    assert "Kubernetes" not in names                                         # quote not in the JD
    assert "401(k) plan" not in names and "Team of 6 engineers" not in names   # benefits and team size never count
    reasons = {d["text"]: d["reason"] for d in out["dropped"]}
    assert reasons["Kubernetes"] == "quote not found in the JD" and "never a requirement" in reasons["401(k) plan"]
    assert out["details"]["min_years"] == 1 and out["details"]["level"] == "entry"
    assert out["details"]["workplace"] == "hybrid" and out["good"]["expected_reach"] == 3
    assert all(r["from_jd"] and r["quote"] for r in out["requirements"])


def test_read_creates_a_prefilled_draft_with_must_haves(api):
    form = read(api)
    d = form["draft"]
    assert d["step"] == 2 and d["source"] == "jd" and d["extraction"]["status"] == "ok"
    assert d["details"]["title"] == "AI Engineer" and d["details"]["company"] == "Brightwater Systems"
    musts = [r["name"] for r in d["requirements"] if r["must_have"]]
    assert musts == [r["name"] for r in d["requirements"] if r["priority"] == "required"][:4]
    assert d["detail_quotes"]["title"] == "AI Engineer" and form["jd_text"].startswith("# AI Engineer")
    assert api.get("/recruiter/jobs?status=draft").json()[0]["title"] == "AI Engineer"


def test_pdf_upload_uses_the_document_normalizer(api):
    form = read(api, files={"file": ("jd.pdf", JD_PDF.read_bytes(), "application/pdf")})
    assert form["has_jd_file"] and "Minimum qualifications" in form["jd_text"]


def test_fallback_when_the_model_is_unavailable(tmp_path):
    path = str(tmp_path / "f.db")
    db.init_app_db(path).close()
    for llm in (JobModel(fail=True), None):
        api = make(path, llm)
        form = read(api)
        d = form["draft"]
        assert d["extraction"]["status"] == "unavailable" and "could" in d["extraction"]["notice"] or \
            "needs a model" in d["extraction"]["notice"]
        assert d["requirements"] == [] and d["details"]["title"] == "" and "Brightwater" in form["jd_text"]


# --- Autosave, resume, publish ------------------------------------------------------------------------------------------

def test_autosave_and_resume_at_the_same_step(api):
    form = api.post("/jobs/drafts").json()
    d = form["draft"]
    d["details"].update(title="Data Analyst", company="Acme")
    d["step"] = 3
    d["requirements"] = [{"name": "SQL", "kind": "tool", "priority": "required", "must_have": True}]
    api.put(f"/jobs/{form['job_id']}/draft", json={"draft": d})
    again = api.get(f"/jobs/{form['job_id']}/draft").json()
    assert again["draft"]["step"] == 3 and again["draft"]["details"]["title"] == "Data Analyst"
    assert again["draft"]["requirements"][0]["name"] == "SQL"
    assert [j["title"] for j in api.get("/recruiter/jobs?status=draft").json()] == ["Data Analyst"]


def test_publish_opens_the_job_for_candidates_with_its_own_baseline(api):
    form = read(api)
    job_id = form["job_id"]
    out = api.post(f"/jobs/{job_id}/publish").json()
    assert out["status"] == "open" and out["applicants"] == 0
    assert [j["id"] for j in api.get("/recruiter/jobs?status=open").json()] == [job_id]
    posting = api.get("/open-jobs").json()[0]
    assert posting["title"] == "AI Engineer" and "Python" in posting["required"] and "Docker" in posting["preferred"]
    job = api.get(f"/jobs/{job_id}").json()
    assert job["baseline"]["level"] == "entry" and job["baseline"]["required_years"] == 1
    assert job["baseline"]["expected_reach"] == 3 and len(job["must_haves"]) == 4
    assert job["baseline_id"] not in {b["id"] for b in api.get("/baselines").json()}     # never a candidate "role"
    stages = api.get(f"/jobs/{job_id}/settings").json()["stages"]
    assert [s["name"] for s in stages][:3] == ["Shortlisted", "Scheduling", "Phone screen"]
    assert len(stages[2]["focus"]) == 4                                                  # from the must-haves


def test_publish_needs_the_starred_fields(api):
    form = api.post("/jobs/drafts").json()
    r = api.post(f"/jobs/{form['job_id']}/publish")
    assert r.status_code == 422 and "Title is required." in r.json()["detail"]


def test_warnings_are_not_blocking(api):
    form = api.post("/jobs/drafts").json()
    d = form["draft"]
    d["details"].update(title="ML Engineer", company="Acme", level="entry", min_years=4)
    d["requirements"] = [{"name": f"Skill {i}", "kind": "skill", "priority": "required"} for i in range(13)]
    w = api.put(f"/jobs/{form['job_id']}/draft", json={"draft": d}).json()["warnings"]
    assert any("13 required items" in x for x in w) and any("entry" in x for x in w)
    assert any("What good looks like" in x for x in w)
    assert api.post(f"/jobs/{form['job_id']}/publish").status_code == 200


def test_copy_an_existing_job_or_template(api):
    seed = next(t for t in api.get("/jobs/templates").json() if t["kind"] == "template")
    form = api.post("/jobs/drafts/copy", json={"kind": "template", "id": seed["id"]}).json()
    assert form["draft"]["details"]["title"] == seed["title"] and form["draft"]["requirements"]
    assert sum(r["must_have"] for r in form["draft"]["requirements"]) == 4


# --- Candidates apply; editing; closing --------------------------------------------------------------------------------

def candidate(api):
    cid = api.post("/candidates", json={"name": "Jane Doe", "target_role": "AI Engineer"}).json()["id"]
    files = {"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")}
    r = api.post(f"/candidates/{cid}/onboard", data={"baseline_id": "baseline-ai-engineer-entry"}, files=files)
    assert api.tasks.wait(r.json()["task_id"])["status"] == "done"
    assert api.post(f"/candidates/{cid}/apply", json={"job_id": "x"}).status_code == 409    # review not confirmed
    confirm = api.post(f"/candidates/{cid}/score", json={"baseline_id": "baseline-ai-engineer-entry"}).json()
    assert api.tasks.wait(confirm["task_id"])["status"] == "done"
    return cid


def published(api):
    form = read(api)
    api.post(f"/jobs/{form['job_id']}/publish")
    return form["job_id"]


def test_apply_scores_against_the_job_and_reaches_the_recruiter(api):
    job_id = published(api)
    cid = candidate(api)
    task = api.tasks.wait(api.post(f"/candidates/{cid}/apply", json={"job_id": job_id}).json()["task_id"])
    assert task["status"] == "done", task
    rows = api.get(f"/jobs/{job_id}/applicants").json()["applicants"]
    assert [r["name"] for r in rows] == ["Jane Doe"] and rows[0]["scored"] and rows[0]["match"] is not None
    run = db.connect(api.app.state.rs.db_path).execute("SELECT baseline_id FROM runs WHERE id = ?",
                                                       (task["result"]["run_id"],)).fetchone()
    assert run[0] == api.get(f"/jobs/{job_id}").json()["baseline_id"]


def test_editing_requirements_warns_and_rescores(api):
    job_id = published(api)
    cid = candidate(api)
    api.tasks.wait(api.post(f"/candidates/{cid}/apply", json={"job_id": job_id}).json()["task_id"])
    form = api.get(f"/jobs/{job_id}/draft").json()
    assert form["draft"]["source"] == "edit" and form["status"] == "open"
    d = form["draft"]
    d["details"]["location"] = "Remote, US"                                        # details: no model calls
    api.put(f"/jobs/{job_id}/draft", json={"draft": d})
    assert api.get(f"/jobs/{job_id}/edit-impact").json() == {"rescore": False, "applicants": 0, "calls": 0}
    assert api.post(f"/jobs/{job_id}/save", json={}).json()["rescoring"] == 0
    d = api.get(f"/jobs/{job_id}/draft").json()["draft"]
    d["requirements"].append({"name": "Kubernetes", "kind": "tool", "priority": "preferred"})
    api.put(f"/jobs/{job_id}/draft", json={"draft": d})
    r = api.post(f"/jobs/{job_id}/save", json={})
    assert r.status_code == 409 and r.json()["detail"]["applicants"] == 1 and r.json()["detail"]["calls"] > 0
    assert "Kubernetes" not in api.get("/open-jobs").json()[0]["preferred"]          # not saved yet
    out = api.post(f"/jobs/{job_id}/save", json={"confirm": True}).json()
    assert out["rescoring"] == 1
    for t in out["tasks"]:
        assert api.tasks.wait(t)["status"] == "done"
    assert "Kubernetes" in api.get("/open-jobs").json()[0]["preferred"]


def test_closed_jobs_leave_the_candidate_side_but_keep_applications(api):
    job_id = published(api)
    cid = candidate(api)
    api.tasks.wait(api.post(f"/candidates/{cid}/apply", json={"job_id": job_id}).json()["task_id"])
    api.post(f"/jobs/{job_id}/status", json={"status": "closed"})
    assert api.get("/open-jobs").json() == []
    assert api.post(f"/candidates/{cid}/apply", json={"job_id": job_id}).status_code == 422
    assert len(api.get(f"/jobs/{job_id}/applicants").json()["applicants"]) == 1
    assert [j["status"] for j in api.get("/recruiter/jobs").json()] == ["closed"]
