"""Onboarding wait screens (specs/17): reading and scoring progress from saved data, per-role failure and Retry, the
"Good to know" tips from config/wait_tips.yaml, and candidate-facing text without internal details. Offline."""
import json
import re
from pathlib import Path

import yaml

from services import wait_screens as ws
from core import config, db
from tests.helpers import SAMPLE_RESUME_LINES, make_pdf
from tests.test_candidate import env, roles, scored  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
DS, AI = "baseline-data-scientist-entry", "baseline-ai-engineer-entry"
FORBIDDEN = re.compile(r"of 19|judgment|weight|gemini|claude|anthropic|\bjev\b|groq|openai|\bgpt|haiku|sonnet|"
                       r"flash-lite|threshold|formula", re.I)


def read_with(api, extra_files=None, data=None):
    cid = api.post("/candidates", json={"name": "Ada"}).json()["id"]
    files = {"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf"), **(extra_files or {})}
    t = api.post(f"/candidates/{cid}/onboard", data={"onboarding": "true", **(data or {})}, files=files).json()["task_id"]
    api.tasks.wait(t)
    return cid, t


# --- Reading ---------------------------------------------------------------------------------------------------

def test_reading_lists_each_added_source_with_what_was_found(env):  # noqa: F811
    api = env.api
    cid, _ = read_with(api, {"linkedin_pdf": ("li.pdf", b"%PDF-1.4 not really a pdf", "application/pdf")})
    v = api.get(f"/candidates/{cid}/reading-progress").json()
    assert v["done"] and v["percent"] == 100 and v["stage_label"] == "Your documents are read"
    by = {s["type"]: s for s in v["sources"]}
    assert list(by) == ["resume", "linkedin_pdf"]
    with db.connect(env.path) as c:
        roles_n = c.execute("SELECT COUNT(*) FROM roles WHERE candidate_id = ?", (cid,)).fetchone()[0]
        skills_n = c.execute("SELECT COUNT(*) FROM claims WHERE candidate_id = ?", (cid,)).fetchone()[0]
        li_error = c.execute("SELECT error FROM evidence_items WHERE candidate_id = ? AND type = 'linkedin_pdf'",
                             (cid,)).fetchone()[0]
    assert by["resume"]["status"] == "done" and by["resume"]["counts"]["roles"] == roles_n > 0
    assert by["resume"]["counts"]["skills"] == skills_n > 0
    assert by["linkedin_pdf"]["status"] == "failed" and by["linkedin_pdf"]["error"] == li_error   # the reader's message


def test_reading_states_while_running():
    with db.connect(":memory:") as c:
        c.execute("INSERT INTO candidates (id, name) VALUES ('c', 'Ada')")
        task = {"status": "running", "step": "ingest", "created_at": "2026-01-01T00:00:00+00:00", "error": None,
                "result": {"sources": ["resume", "linkedin_pdf", "github"]}}
        v = ws.reading_view(c, "c", task)
        assert [s["status"] for s in v["sources"]] == ["reading", "waiting", "waiting"]
        assert v["stage_label"] == "Reading your resume" and not v["done"]
        c.execute("INSERT INTO evidence_items (id, candidate_id, type, status, created_at) VALUES "
                  "('e1', 'c', 'resume', 'parsed', '2026-01-01T00:00:01+00:00')")
        v = ws.reading_view(c, "c", task)
        assert [s["status"] for s in v["sources"]] == ["reading", "reading", "waiting"]
        assert v["stage_label"] == "Reading your LinkedIn profile"
        v = ws.reading_view(c, "c", {**task, "step": "extract"})
        assert v["stage_label"] == "Finding your roles, projects and skills" and v["percent"] == 45
        v = ws.reading_view(c, "c", {**task, "step": "validate"})
        assert v["stage_label"] == "Checking everything against your documents"
        v = ws.reading_view(c, "c", {**task, "status": "done", "step": "done",
                                     "result": {"sources": ["resume", "github"], "warnings": ["GitHub: user not found."]}})
        assert v["sources"][1] == {"type": "github", "label": "GitHub", "status": "failed", "error": "user not found.",
                                   "counts": None}


# --- Scoring -----------------------------------------------------------------------------------------------------

def test_scoring_rows_per_role_fill_by_dimension_and_ready(env):  # noqa: F811
    picks = roles(env.api)[:2]
    cid, runs = scored(env, picks)
    v = env.api.get(f"/candidates/{cid}/scoring-progress").json()
    assert [r["baseline_id"] for r in v["roles"]] == picks and v["roles"][0]["is_primary"]
    assert all(r["status"] == "ready" and set(r["dimensions"].values()) == {1.0} for r in v["roles"])
    assert v["all_ready"] and v["line"] == "Your scores are ready" and v["overall"] == 1.0


def test_scoring_partial_progress_from_saved_scores():
    with db.connect(":memory:") as c:
        db.seed_baselines(c)
        c.execute("INSERT INTO candidates (id, name) VALUES ('c', 'Ada')")
        c.execute("INSERT INTO candidate_targets VALUES ('c', ?, 1, 0, 'x'), ('c', ?, 0, 1, 'x')", (DS, AI))
        c.execute("INSERT INTO candidate_onboarding (candidate_id, step, confirmed_at) VALUES ('c', 'scores', "
                  "'2026-01-01T00:00:00+00:00')")
        c.execute("INSERT INTO runs (id, candidate_id, baseline_id, status, started_at) VALUES "
                  "('r1', 'c', ?, 'running', '2026-01-01T00:00:05+00:00')", (DS,))
        exp = config.SUBCATEGORIES["experience"]
        for sub in exp[:2] + config.SUBCATEGORIES["trust"]:
            c.execute("INSERT INTO scores (id, run_id, subcategory) VALUES (?, 'r1', ?)", (sub, sub))
        running = {"status": "running", "result": {"roles": [DS, AI]}}
        v = ws.scoring_view(c, "c", running)
        ds, ai = v["roles"]
        assert ds["status"] == "scoring" and ai["status"] == "waiting"
        assert ds["dimensions"]["experience"] == round(2 / len(exp), 3) and ds["dimensions"]["trust"] == 1.0
        assert ds["dimensions"]["expertise"] == 0.0 and v["line"] == "Scoring your profile for 2 roles"
        c.execute("UPDATE runs SET status = 'done' WHERE id = 'r1'")
        v = ws.scoring_view(c, "c", running)
        assert v["line"] == "Scoring your profile for 2 roles · Data Scientist ready" and not v["all_ready"]
        assert v["roles"][0]["dimensions"] == {d: 1.0 for d in config.SUBCATEGORIES}
        v = ws.scoring_view(c, "c", {"status": "done", "result": {"roles": [DS, AI], "failed": {AI: "boom"}}})
        assert v["roles"][1]["status"] == "failed" and not v["running"]


def test_a_failing_role_does_not_stop_the_others_and_retry_scores_only_it(env, monkeypatch):  # noqa: F811
    api = env.api
    picks = roles(api)[:2]
    pipe = api.app.state.rs.pipeline
    real = pipe.score

    def flaky(candidate_id, baseline_id, *a, **k):
        if baseline_id == picks[0]:
            raise RuntimeError("quota")
        return real(candidate_id, baseline_id, *a, **k)
    monkeypatch.setattr(pipe, "score", flaky)
    cid, t = read_with(api)
    api.put(f"/candidates/{cid}/targets", json={"baseline_ids": picks})
    task = api.tasks.wait(api.post(f"/candidates/{cid}/baseline-scores").json()["task_id"])
    assert task["status"] == "done" and list(task["result"]["failed"]) == [picks[0]]
    v = api.get(f"/candidates/{cid}/scoring-progress").json()
    assert [r["status"] for r in v["roles"]] == ["failed", "ready"] and not v["all_ready"]
    with db.connect(env.path) as c:
        other_run = c.execute("SELECT id FROM runs WHERE candidate_id = ? AND baseline_id = ?", (cid, picks[1])).fetchone()[0]
    monkeypatch.setattr(pipe, "score", real)
    r = api.post(f"/candidates/{cid}/baseline-scores", json={"baseline_ids": [picks[0]]})
    assert api.tasks.wait(r.json()["task_id"])["status"] == "done"
    v = api.get(f"/candidates/{cid}/scoring-progress").json()
    assert v["all_ready"] and [r["status"] for r in v["roles"]] == ["ready", "ready"]
    with db.connect(env.path) as c:
        assert [x[0] for x in c.execute("SELECT id FROM runs WHERE candidate_id = ? AND baseline_id = ?",
                                        (cid, picks[1]))] == [other_run]            # the other role was not re-scored
    assert api.post(f"/candidates/{cid}/baseline-scores", json={"baseline_ids": ["nope"]}).status_code == 422


# --- Tips -----------------------------------------------------------------------------------------------------------

def test_tips_come_from_the_config_file(env, monkeypatch, tmp_path):  # noqa: F811
    t = env.api.get("/config/wait-tips").json()
    assert [x["title"] for x in t["reading"]] == ["Show it once", "Only what you wrote", "More sources, more credibility",
                                                  "You stay in control"]
    assert len(t["scoring"]) == 8 and t["scoring"][0]["title"] == "What is a baseline score?"
    assert t["scoring"][2]["text"] == ("Packing a resume with keywords doesn't raise your score. Real evidence does: "
                                       "projects, outcomes, references.")
    f = tmp_path / "tips.yaml"
    f.write_text(yaml.safe_dump({"reading": [{"title": "A", "text": "B"}], "scoring": []}))
    monkeypatch.setattr(ws, "TIPS_FILE", f)
    assert env.api.get("/config/wait-tips").json() == {"reading": [{"title": "A", "text": "B", "icon": None}], "scoring": []}


# --- No internal details in candidate-facing text -----------------------------------------------------------------

def test_no_internal_details_in_tips_progress_or_wait_screen_text(env):  # noqa: F811
    cid, _ = scored(env, roles(env.api)[:2])
    texts = [json.dumps(ws.tips()), env.api.get(f"/candidates/{cid}/reading-progress").text,
             env.api.get(f"/candidates/{cid}/scoring-progress").text]
    with db.connect(env.path) as c:
        texts += [r[0] for r in c.execute("SELECT e.message FROM task_events e JOIN tasks t ON t.id = e.task_id "
                                          "WHERE t.candidate_id = ?", (cid,))]
    files = ["frontend/components/app/candidate/wait-progress.tsx", "frontend/components/app/candidate/good-to-know.tsx",
             "frontend/app/onboarding/page.tsx", "config/wait_tips.yaml"]
    for f in files:                            # visible strings only: JSX text and string literals, not identifiers
        src = (ROOT / f).read_text()
        texts += re.findall(r">([^<>{}]+)<", src) + re.findall(r'"([^"\n]{3,})"', src)
    for t in texts:
        if f.endswith("wait_tips.yaml") and t.lstrip().startswith("#"):
            continue
        hit = FORBIDDEN.search(t)
        assert hit is None, (hit.group(0), t[:120])
