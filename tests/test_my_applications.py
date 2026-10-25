"""Candidate experience, phase C (specs/17 §7): My applications (statuses, timeline, reasons), the closure principle,
Mark done, and the Market template labels. Offline."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from workflows import hiring as hr
from services import my_applications as ma
from workflows import engine as wf
from core import config, db
from tests.test_candidate import env, scored  # noqa: F401  (fixture)
from tests.test_candidate_jobs import DS, dj, seeded_env  # noqa: F401  (fixture)


def applied(env):  # noqa: F811
    cid, runs = scored(env, [DS])
    job = dj("cedarpine-ds1")
    env.api.tasks.wait(env.api.post(f"/candidates/{cid}/apply", json={"job_id": job}).json()["task_id"])
    app_id = env.api.get(f"/jobs/{job}/applicants").json()["applicants"][0]["application_id"]
    return cid, app_id, job, runs


def view(env, cid):
    return env.api.get(f"/candidates/{cid}/applications").json()[0]


def move_to(env, app_id, name):
    with db.connect(env.path) as c:
        app = dict(c.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
        stage = next(s for s in wf.stages(c, app["job_id"]) if s["name"] == name)
        wf.move(c, app, stage["id"])


def states(v):
    return {t["key"]: t["state"] for t in v["timeline"]}


# --- Statuses and timeline ---------------------------------------------------------------------------------------

def test_status_follows_the_recruiter_stages(seeded_env):
    cid, app_id, job, _ = applied(seeded_env)
    v = view(seeded_env, cid)
    assert v["status"] == "Applied" and states(v)["applied"] == "current" and states(v)["viewed"] == "upcoming"
    seeded_env.api.post(f"/applications/{app_id}/opened")
    v = view(seeded_env, cid)
    assert v["status"] == "Viewed by recruiter" and states(v)["applied"] == "done" and v["timeline"][1]["date"]
    for name, status in (("Shortlisted", "Shortlisted"), ("Phone screen", "Interview"), ("Offer", "Offer"),
                         ("Hired", "Hired")):
        move_to(seeded_env, app_id, name)
        assert view(seeded_env, cid)["status"] == status, name
    v = view(seeded_env, cid)
    assert v["final"] and v["timeline"][-1]["key"] == "hired" and all(t["state"] == "done" for t in v["timeline"])


def test_interview_shows_the_round_from_the_workflow(seeded_env):
    cid, app_id, job, _ = applied(seeded_env)
    with db.connect(seeded_env.path) as c:
        c.execute("INSERT INTO workflows (application_id, job_id, stage, round, phase, step, status, created_at, "
                  "updated_at) VALUES (?, ?, 'Interview · Scheduling', 1, 'scheduling', 'approve_email', 'needs_action', "
                  "?, ?)", (app_id, job, db.now_iso(), db.now_iso()))
        c.commit()
    v = view(seeded_env, cid)
    assert v["status"] == "Interview" and v["round"] == "Round 2"
    assert next(t for t in v["timeline"] if t["key"] == "interview")["label"] == "Interview · Round 2"


def test_declined_after_an_offer(seeded_env):
    cid, app_id, job, _ = applied(seeded_env)
    move_to(seeded_env, app_id, "Offer")
    with db.connect(seeded_env.path) as c:
        c.execute("INSERT INTO workflows (application_id, job_id, stage, phase, step, status, outcome, created_at, "
                  "updated_at) VALUES (?, ?, 'Closed', 'closed', 'done', 'closed', 'declined', ?, ?)",
                  (app_id, job, db.now_iso(), db.now_iso()))
        c.commit()
    v = view(seeded_env, cid)
    assert v["status"] == "Declined" and v["timeline"][-1]["label"] == "Declined"
    assert states(v)["offer"] == "done" and states(v)["interview"] == "not_reached"


def sent_rejection(env, app_id, job, reasons):
    with db.connect(env.path) as c:
        c.execute("INSERT INTO emails (id, application_id, job_id, kind, status, subject, body, reasons_json, created_at, "
                  "sent_at) VALUES ('e1', ?, ?, 'not_moving_forward', 'sent', 's', 'b', ?, ?, ?)",
                  (app_id, job, json.dumps({"reasons": reasons, "note": None}), db.now_iso(), db.now_iso()))
        c.execute("UPDATE applications SET status = 'not_moving_forward' WHERE id = ?", (app_id,))
        c.commit()


def test_not_moving_forward_shows_the_sent_reasons_linked_to_the_plan(seeded_env):
    cid, app_id, job, _ = applied(seeded_env)
    with db.connect(seeded_env.path) as c:
        crit = next(x for x in db.get_baseline(c, c.execute("SELECT baseline_id FROM jobs WHERE id = ?", (job,)).fetchone()[0]
                                                ).criteria if x.kind == "tool")
    sent_rejection(seeded_env, app_id, job, [
        {"id": f"must:{crit.id}", "source": "must-have", "title": crit.name, "detail": f"{crit.name} does not appear.",
         "advice": "Show it in a project.", "selected": True},
        {"id": "gap:outcomes", "source": "gap", "title": "Outcomes", "detail": "Shown only weakly so far.", "advice": "Add results.",
         "selected": True},
        {"id": "gap:impact", "source": "gap", "title": "Impact", "detail": "x", "advice": "y", "selected": False}])
    v = view(seeded_env, cid)
    assert v["status"] == "Not moving forward" and v["final"]
    assert [r["title"] for r in v["reasons"]] == [crit.name, "Outcomes"]                    # selected ones only
    assert v["reasons"][0]["link"] == f"/scores?role={DS}&tab=plan#gap-tools"
    assert v["reasons"][1]["link"] == f"/scores?role={DS}&tab=plan#gap-outcomes"


def test_not_moving_forward_waits_for_the_email_to_be_sent(seeded_env):
    """A board move to Not moving forward without the email sent yet is not shown to the candidate."""
    cid, app_id, job, _ = applied(seeded_env)
    move_to(seeded_env, app_id, "Shortlisted")
    move_to(seeded_env, app_id, "Not moving forward")
    assert view(seeded_env, cid)["status"] == "Shortlisted"


def test_no_scores_ranks_notes_or_scorecards_in_the_candidate_view(seeded_env):
    cid, app_id, job, _ = applied(seeded_env)
    with db.connect(seeded_env.path) as c:
        app = dict(c.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone())
        wf.log_activity(c, app, "note", "SECRET-NOTE recruiter thinks")
        c.execute("INSERT INTO scorecards (id, application_id, stage, interviewer, ratings_json, notes, recommendation, "
                  "created_at) VALUES ('s1', ?, 'Interview', 'Ann', '[]', 'SECRET-CARD', 'no', ?)", (app_id, db.now_iso()))
        c.commit()
    body = seeded_env.api.get(f"/candidates/{cid}/applications").text
    keys = set(view(seeded_env, cid))
    assert "SECRET" not in body and not {"match", "score", "rank", "percentile", "confidence", "notes"} & keys
    assert "match" not in body.casefold() and "scorecard" not in body.casefold()


# --- Closure principle -------------------------------------------------------------------------------------------

def test_closure_task_after_closure_days_and_nothing_is_sent(seeded_env):
    cid, app_id, job, _ = applied(seeded_env)
    old = (datetime.now(timezone.utc) - timedelta(days=config.CLOSURE_DAYS + 3)).isoformat(timespec="seconds")
    with db.connect(seeded_env.path) as c:
        c.execute("UPDATE applications SET applied_at = ? WHERE id = ?", (old, app_id))
        c.commit()
        emails = c.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
    tasks = [t for t in seeded_env.api.get("/recruiter/tasks").json() if t["kind"] == "closure"]
    name = seeded_env.api.get(f"/candidates/{cid}/onboarding").json()["candidate"]["name"]
    assert [t["title"] for t in tasks] == [f"{name} has waited {config.CLOSURE_DAYS + 3} days for an answer"]
    assert tasks[0]["application_id"] == app_id
    v = view(seeded_env, cid)
    assert v["waiting"] and v["waiting_text"] == ma.WAITING_TEXT
    with db.connect(seeded_env.path) as c:
        assert c.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == emails                # never sends
    move_to(seeded_env, app_id, "Shortlisted")                                                 # a stage change answers it
    assert not [t for t in seeded_env.api.get("/recruiter/tasks").json() if t["kind"] == "closure"]
    assert not view(seeded_env, cid)["waiting"]


def test_no_closure_task_for_closed_applications(seeded_env):
    cid, app_id, job, _ = applied(seeded_env)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat(timespec="seconds")
    with db.connect(seeded_env.path) as c:
        c.execute("UPDATE applications SET applied_at = ?, status = 'not_moving_forward' WHERE id = ?", (old, app_id))
        c.commit()
        assert ma.closure_tasks(c) == []


# --- Mark done ---------------------------------------------------------------------------------------------------

def test_mark_done_moves_to_the_next_action_changes_no_score_and_undoes(env):  # noqa: F811
    cid, runs = scored(env, [DS])
    card = env.api.get(f"/candidates/{cid}/baseline-scores").json()[0]
    first = card["next_action"]
    assert first and not card["done_actions"]
    after = env.api.post(f"/candidates/{cid}/plan-done", json={"baseline_id": DS, "title": first["title"]}).json()
    assert after["next_action"] is None or after["next_action"]["title"] != first["title"]
    assert [a["title"] for a in after["done_actions"]] == [first["title"]] and after["final"] == card["final"]
    with db.connect(env.path) as c:
        assert c.execute("SELECT COUNT(*) FROM candidate_plan_done WHERE candidate_id = ?", (cid,)).fetchone()[0] == 1
    undone = env.api.post(f"/candidates/{cid}/plan-done", json={"baseline_id": DS, "title": first["title"],
                                                                "done": False}).json()
    assert undone["next_action"]["title"] == first["title"] and undone["done_actions"] == []


# --- Market templates ---------------------------------------------------------------------------------------------

def test_market_templates_say_what_they_are_based_on(env):  # noqa: F811
    from services import jobs as jb
    with db.connect(env.path) as c:
        for vid, ver, test, title in (("v1", 1, 1, "AI Engineer"), ("v2", 2, 0, "Data Scientist")):
            c.execute("INSERT INTO baselines (id, title, level, required_years, expected_reach, is_mock, "
                      "market_version_id) VALUES (?, ?, 'entry', 1, 2, 0, ?)", (f"b-{vid}", title, vid))
            c.execute("INSERT INTO market_versions (id, role, level, version, status, test_version, baseline_id, postings, "
                      "companies) VALUES (?, 'x', 'entry', ?, 'published', ?, ?, ?, ?)",
                      (vid, ver, test, f"b-{vid}", 1 if test else 25, 1 if test else 9))
        c.commit()
        market = [t for t in jb.templates(c) if t["company"] == "Market template"]
    assert [t["title"] for t in market] == ["AI Engineer (Market v1) · Based on 1 posting from 1 company",
                                            "Data Scientist (Market v2) · Based on 25 postings from 9 companies"]
