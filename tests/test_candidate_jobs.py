"""Candidate experience, phase B (specs/17 §4–§6, §9, §12): demo jobs, the dummy JobMatch, Home grouping and sorting,
Not interested, recruiter activity, job detail evidence matching and the apply sheet. Offline (FakeJudge)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from backend import candidate_jobs as cj
from backend import demo_jobs
from backend import jobs as jb
from core import config, db
from tests.test_candidate import DETAILS, env, roles, scored  # noqa: F401  (fixture)

DS, AI, PM = "baseline-data-scientist-entry", "baseline-ai-engineer-entry", "baseline-product-manager-entry"
SWE, BA, DA = "baseline-software-engineer-entry", "baseline-business-analyst-entry", "baseline-data-analyst-entry"


@pytest.fixture
def seeded_env(env):  # noqa: F811
    with db.connect(env.path) as c:
        env.seed = demo_jobs.seed(c)
    return env


def dj(key: str) -> str:
    return demo_jobs.job_id(key)


def counts(path):
    with db.connect(path) as c:
        return {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("runs", "scores", "judgments", "gap_reports")}


# --- §9 Seeded demo jobs -----------------------------------------------------------------------------------------

def test_fifteen_demo_jobs_three_per_original_role_two_per_new_role(seeded_env):
    assert seeded_env.seed["added"] == 15
    with db.connect(seeded_env.path) as c:
        rows = [jb.get(c, r[0]) for r in c.execute("SELECT id FROM jobs WHERE data_source = 'demo'")]
        assert len(rows) == 15 and all(j["status"] == "open" for j in rows)
        by_role = {}
        for j in rows:
            by_role.setdefault(j["role_baseline_id"], []).append(j)
        assert {k: len(v) for k, v in by_role.items()} == {DS: 3, AI: 3, PM: 3, SWE: 2, BA: 2, DA: 2}
        assert sum(1 for j in rows if cj.salary(j["details"]) is None) == 2
        now = datetime.now(timezone.utc)
        ages = [(now - datetime.fromisoformat(j["created_at"])).days for j in rows]
        assert min(ages) >= 1 and max(ages) <= 40
        assert all(c.execute("SELECT COUNT(*) FROM job_events WHERE job_id = ? AND demo = 1", (j["id"],)).fetchone()[0]
                   for j in rows)
        lumen = jb.get(c, dj("lumen-ai-applied-llm"))
        own = db.get_baseline(c, lumen["baseline_id"])
        role = db.get_baseline(c, AI)
        names = {x.name for x in own.criteria}
        assert "Vector databases" in names and "Cloud ML certification" not in names        # small differences
        assert len(names & {x.name for x in role.criteria}) == len(role.criteria) - 1
        assert c.execute("SELECT owner_job_id FROM baselines WHERE id = ?", (own.id,)).fetchone()[0] == lumen["id"]
        assert own.id not in {b.id for b in db.list_baselines(c)}                           # never a role baseline
        assert demo_jobs.seed(c)["added"] == 0                                              # idempotent


# --- §6 Dummy JobMatch ---------------------------------------------------------------------------------------------

def test_jobmatch_is_deterministic_and_clamped():
    assert cj.jobmatch_value("c1", "j1", 70) == cj.jobmatch_value("c1", "j1", 70)
    values = [cj.jobmatch_value(f"c{i}", "j", 70) for i in range(200)]
    assert min(values) >= 58 and max(values) <= 78                                          # 70 + [-12, +8]
    none = [cj.jobmatch_value(f"c{i}", "j", None) for i in range(200)]
    assert min(none) >= 40 and max(none) <= 70                                              # 55 ± 15
    assert all(cj.jobmatch_value(f"c{i}", "j", 100) <= 98 for i in range(200))
    assert all(cj.jobmatch_value(f"c{i}", "j", 0) >= 5 for i in range(200))
    assert cj.must_haves_preview(84, 4) == 3 and cj.must_haves_preview(5, 4) == 0 and cj.must_haves_preview(98, 3) == 3


def test_jobmatch_uses_the_same_role_baseline_score_and_is_never_stored(seeded_env):
    cid, runs = scored(seeded_env, [DS])
    before = counts(seeded_env.path)
    home = seeded_env.api.get(f"/candidates/{cid}/jobs").json()
    again = seeded_env.api.get(f"/candidates/{cid}/jobs").json()
    assert [j["jobmatch"] for j in home["jobs"]] == [j["jobmatch"] for j in again["jobs"]]
    final = seeded_env.api.get(f"/runs/{runs[DS]}").json()["final"]
    ds_job = next(j for j in home["jobs"] if j["id"] == dj("cedarpine-ds1"))
    assert ds_job["jobmatch"]["value"] == cj.jobmatch_value(cid, ds_job["id"], final)
    pm_job = next(j for j in home["jobs"] if j["id"] == dj("larkspur-apm-payments"))
    assert pm_job["jobmatch"]["value"] == cj.jobmatch_value(cid, pm_job["id"], None)     # no PM baseline score
    assert ds_job["jobmatch"]["preview"] is True and ds_job["jobmatch"]["must_haves_total"] == 4
    assert counts(seeded_env.path) == before                                                 # nothing written


def test_jobmatch_flag_off(seeded_env, monkeypatch):
    cid, _ = scored(seeded_env, [DS])
    monkeypatch.setattr(config, "JOBMATCH_PREVIEW", False)
    home = seeded_env.api.get(f"/candidates/{cid}/jobs").json()
    assert home["preview"] is False and all(j["jobmatch"] is None and j["group"] is None for j in home["jobs"])


def test_jobmatch_never_in_recruiter_responses(seeded_env):
    api = seeded_env.api
    cid, _ = scored(seeded_env, [DS])
    job = dj("cedarpine-ds1")
    api.tasks.wait(api.post(f"/candidates/{cid}/apply", json={"job_id": job}).json()["task_id"])
    api.get(f"/candidates/{cid}/jobs/{job}")
    app_id = api.get(f"/jobs/{job}/applicants").json()["applicants"][0]["application_id"]
    for path in [f"/jobs/{job}/applicants", f"/applications/{app_id}", f"/jobs/{job}/pipeline", "/recruiter/jobs",
                 f"/jobs/{job}", f"/applications/{app_id}/workflow", "/recruiter/tasks"]:
        body = api.get(path).text.casefold()
        assert "jobmatch" not in body and "preview" not in body, path


# --- §4 Home: grouping, sorting, target roles first, Not interested ------------------------------------------------

def test_groups_follow_the_config_thresholds():
    assert [cj.group_of(v) for v in (98, 75, 74, 55, 54, 5, None)] == \
        ["strong", "strong", "close", "close", "stretch", "stretch", None]


def test_sorting_target_first_then_group_then_match_then_newest():
    def row(i, target, value, days):
        return {"id": i, "target": target, "group": cj.group_of(value), "jobmatch": {"value": value},
                "posted_at": (datetime(2026, 10, 16, tzinfo=timezone.utc) - timedelta(days=days)).isoformat()}
    rows = [row("other-strong", False, 90, 1), row("t-close-new", True, 60, 1), row("t-close-old", True, 60, 9),
            row("t-strong", True, 80, 30), row("t-stretch", True, 20, 1), row("t-close-high", True, 70, 30)]
    assert [r["id"] for r in sorted(rows, key=cj.sort_key)] == \
        ["t-strong", "t-close-high", "t-close-new", "t-close-old", "t-stretch", "other-strong"]


def test_home_lists_open_recruiter_jobs_target_roles_first(seeded_env):
    cid, _ = scored(seeded_env, [DS])
    home = seeded_env.api.get(f"/candidates/{cid}/jobs").json()
    with db.connect(seeded_env.path) as c:
        assert home["total_open"] == len(jb.listing(c, "open"))
        open_ids = {j["id"] for j in jb.listing(c, "open")}
    assert {j["id"] for j in home["jobs"]} <= open_ids                                     # recruiter jobs only
    flags = [j["target"] for j in home["jobs"]]
    assert flags == sorted(flags, reverse=True) and flags.count(True) == 3
    assert {j["role_baseline_id"] for j in home["jobs"] if j["target"]} == {DS}
    assert home["profile_confirmed"] is True and home["hidden"] == 0


def test_not_interested_hides_with_optional_reason_and_changes_no_score(seeded_env):
    api = seeded_env.api
    cid, runs = scored(seeded_env, [DS])
    job = dj("tidewater-junior-ds")
    final = api.get(f"/runs/{runs[DS]}").json()["final"]
    assert api.post(f"/candidates/{cid}/jobs/{job}/hide", json={"reason": "bad"}).status_code == 422
    assert api.post(f"/candidates/{cid}/jobs/{job}/hide", json={"reason": "salary"}).json()["reason"] == "salary"
    home = api.get(f"/candidates/{cid}/jobs").json()
    assert job not in {j["id"] for j in home["jobs"]} and home["hidden"] == 1
    assert api.post(f"/candidates/{cid}/jobs/{dj('cedarpine-ds1')}/hide", json={}).json()["reason"] is None
    assert api.get(f"/runs/{runs[DS]}").json()["final"] == final
    api.post(f"/candidates/{cid}/jobs/{job}/unhide")
    assert job in {j["id"] for j in api.get(f"/candidates/{cid}/jobs").json()["jobs"]}


def test_save_marks_the_job(seeded_env):
    api = seeded_env.api
    cid, _ = scored(seeded_env, [DS])
    job = dj("cedarpine-ds1")
    api.post(f"/candidates/{cid}/jobs/{job}/save", json={"saved": True})
    assert next(j for j in api.get(f"/candidates/{cid}/jobs").json()["jobs"] if j["id"] == job)["saved"]
    assert api.get(f"/candidates/{cid}/jobs/{job}").json()["saved"] is True


def test_work_mode_from_application_details_prefills_the_filter(seeded_env):
    api = seeded_env.api
    cid, _ = scored(seeded_env, [DS])
    assert api.get(f"/candidates/{cid}/jobs").json()["defaults"]["workplace"] is None
    api.put(f"/candidates/{cid}/application-details", json=DETAILS)                   # work_mode: hybrid
    assert api.get(f"/candidates/{cid}/jobs").json()["defaults"]["workplace"] == "hybrid"


# --- §4.1 Recruiter activity -----------------------------------------------------------------------------------------

def test_activity_signals_from_seeded_and_real_events(seeded_env):
    api = seeded_env.api
    cid, _ = scored(seeded_env, [DS])
    rows = {j["id"]: j for j in api.get(f"/candidates/{cid}/jobs").json()["jobs"]}
    assert rows[dj("cedarpine-ds1")]["activity"]["label"] == "Recruiter active yesterday"
    assert rows[dj("brightline-associate-ds")]["activity"]["reviewing"] is True                 # stage moved 5 days ago
    assert rows[dj("tidewater-junior-ds")]["activity"]["reviewing"] is False
    old = api.get(f"/candidates/{cid}/jobs").json()
    harbor = next(j for j in old["jobs"] if j["id"] == dj("harborline-ai-new-grad"))
    assert harbor["activity"]["low"] and harbor["activity"]["label"] == "Low recent activity"   # 34 days
    api.tasks.wait(api.post(f"/candidates/{cid}/apply", json={"job_id": dj("tidewater-junior-ds")}).json()["task_id"])
    app_id = api.get(f"/jobs/{dj('tidewater-junior-ds')}/applicants").json()["applicants"][0]["application_id"]
    assert api.post(f"/applications/{app_id}/opened").status_code == 204
    t = next(j for j in api.get(f"/candidates/{cid}/jobs").json()["jobs"] if j["id"] == dj("tidewater-junior-ds"))
    assert t["activity"]["label"] == "Recruiter active today" and t["applied"]["status"] == "Applied"


def test_a_stage_move_marks_reviewing(seeded_env):
    with db.connect(seeded_env.path) as c:
        job = jb.get(c, dj("tidewater-junior-ds"))
        now = datetime.now(timezone.utc)
        assert not cj.activity(c, job, now)["reviewing"]
        c.execute("INSERT INTO activity (id, application_id, job_id, kind, summary, detail_json, created_at) VALUES "
                  "('a1', NULL, ?, 'stage', 'moved', '{}', ?)", (job["id"], (now - timedelta(days=13)).isoformat()))
        c.commit()
        assert cj.activity(c, job, now)["reviewing"]
        assert not cj.activity(c, job, now + timedelta(days=2))["reviewing"]               # older than 14 days


# --- §5 Job detail ------------------------------------------------------------------------------------------------

def test_closest_evidence_only_on_exact_name_or_alias_match_and_no_model_calls(seeded_env):
    api = seeded_env.api
    cid, _ = scored(seeded_env, [DS])
    with db.connect(seeded_env.path) as c:                     # a job whose names differ: alias match vs near miss
        role = db.get_baseline(c, DS)
        own = role.model_copy(update={"id": "b-alias", "title": "Analyst", "criteria": [
            role.criteria[8].model_copy(update={"id": None, "name": "Python programming", "aliases": ["Python"]}),
            role.criteria[9].model_copy(update={"id": None, "name": "SQL querying", "aliases": ["Postgres"]}),
            role.criteria[0].model_copy(update={"id": None, "name": "Statistical analysis", "aliases": []})]})
        db.create_baseline(c, own)
        c.execute("INSERT INTO jobs (id, baseline_id, title, company, weights_json, must_haves_json, created_at, status, "
                  "details_json, role_baseline_id) VALUES ('j-alias', 'b-alias', 'Analyst', 'Acme', ?, '[]', ?, 'open', "
                  "'{}', ?)", (json.dumps(config.default_weights()), db.now_iso(), DS))
        c.commit()
    calls = (len(seeded_env.judge.calls), len(seeded_env.llm.calls), len(seeded_env.planner.prompts))
    d = api.get(f"/candidates/{cid}/jobs/j-alias").json()
    ev = {r["name"]: r["evidence"] for r in d["requirements"]}
    assert ev["Python programming"] and ev["Python programming"]["matched"] == "Python"           # alias → name
    assert ev["Python programming"]["caption"] == "From your Data Scientist baseline score"
    assert ev["SQL querying"] is None                                                             # no exact match
    assert ev["Statistical analysis"] is not None                                                 # same name
    detail = api.get(f"/candidates/{cid}/jobs/{dj('cedarpine-ds1')}").json()
    by = {r["name"]: r["evidence"] for r in detail["requirements"]}
    assert by["Insurance or risk modeling"] is None and by["Python"] is not None
    assert (len(seeded_env.judge.calls), len(seeded_env.llm.calls), len(seeded_env.planner.prompts)) == calls


def test_detail_shows_jobmatch_baseline_score_raise_and_recruiter_view(seeded_env):
    api = seeded_env.api
    cid, runs = scored(seeded_env, [DS])
    d = api.get(f"/candidates/{cid}/jobs/{dj('cedarpine-ds1')}").json()
    assert d["jobmatch"]["preview"] and d["role"]["title"] == "Data Scientist"
    assert d["role"]["baseline_score"] == api.get(f"/runs/{runs[DS]}").json()["final"]
    assert d["raise"] and d["raise"][0]["title"] == "Build an A/B test project" and not d["raise_fallback"]  # covers Python
    assert d["job"]["salary"] == {"min": 95000, "max": 115000, "currency": "USD"}
    rv = json.dumps(d["recruiter_view"])
    assert "Resume" in d["recruiter_view"]["sources"] and "jobmatch" not in rv and "score" not in rv
    assert d["applied"] is None and d["profile_confirmed"] is True
    other = api.get(f"/candidates/{cid}/jobs/{dj('larkspur-apm-payments')}").json()   # no PM baseline score
    assert other["role"]["baseline_score"] is None and other["raise"] == [] and other["evidence_matches"] == 0
    assert all(r["evidence"] is None for r in other["requirements"])


def test_detail_hides_drafts_and_closed_jobs_not_applied_to(seeded_env):
    api = seeded_env.api
    cid, _ = scored(seeded_env, [DS])
    job = dj("tidewater-junior-ds")
    api.post(f"/jobs/{job}/status", json={"status": "closed"})
    assert api.get(f"/candidates/{cid}/jobs/{job}").status_code == 404
    assert job not in {j["id"] for j in api.get(f"/candidates/{cid}/jobs").json()["jobs"]}


# --- Phase B review: activity, apply needs a confirmed profile, fixed demo dates -----------------------------------

def test_posting_counts_once_and_editing_the_job_does_not(seeded_env):
    with db.connect(seeded_env.path) as c:
        job = jb.get(c, dj("harborline-ai-new-grad"))                       # posted 38 days ago, last action 34
        now = datetime.now(timezone.utc)
        assert cj.activity(c, job, now)["low"]
        c.execute("UPDATE jobs SET updated_at = ? WHERE id = ?", (now.isoformat(), job["id"]))   # an edit
        c.execute("INSERT INTO activity (id, application_id, job_id, kind, summary, detail_json, created_at) VALUES "
                  "('n1', NULL, ?, 'note', 'a note', '{}', ?)", (job["id"], now.isoformat()))     # not an applicant action
        c.commit()
        assert cj.activity(c, jb.get(c, job["id"]), now)["label"] == "Low recent activity"
        c.execute("INSERT INTO activity (id, application_id, job_id, kind, summary, detail_json, created_at) VALUES "
                  "('s1', NULL, ?, 'stage', 'moved', '{}', ?)", (job["id"], now.isoformat()))
        c.commit()
        assert cj.activity(c, jb.get(c, job["id"]), now)["label"] == "Recruiter active today"
        fresh = jb.get(c, dj("cedarpine-ds1"))
        c.execute("DELETE FROM job_events WHERE job_id = ?", (fresh["id"],))
        c.commit()
        assert cj.activity(c, fresh, now)["days"] == 1                     # posting itself (1 day ago) counts


def test_apply_needs_a_confirmed_profile(seeded_env):
    api = seeded_env.api
    from tests.test_candidate import new_profile
    cid, read = new_profile(api)
    api.tasks.wait(read)
    api.put(f"/candidates/{cid}/targets", json={"baseline_ids": [DS]})
    r = api.post(f"/candidates/{cid}/apply", json={"job_id": dj("cedarpine-ds1")})
    assert r.status_code == 409 and "Confirm your profile first" in r.json()["detail"]
    with db.connect(seeded_env.path) as c:
        assert c.execute("SELECT COUNT(*) FROM applications WHERE candidate_id = ?", (cid,)).fetchone()[0] == 0


def test_the_first_demo_jobs_get_fixed_dates_and_activity(env):  # noqa: F811
    from backend import recruiter as rc
    now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
    with db.connect(env.path) as c:
        ids = {co: rc.create_job(c, DS, "Data Scientist", co) for co in ("Northwind Analytics", "Quarry Labs", "Fernway Health")}
        demo_jobs.seed(c, now=now)
        north = jb.get(c, ids["Northwind Analytics"])
        assert north["data_source"] == "demo" and north["published_at"] == (now - timedelta(days=6)).isoformat()
        assert cj.activity(c, north, now)["label"] == "Recruiter active yesterday" and cj.activity(c, north, now)["reviewing"]
        events = c.execute("SELECT COUNT(*) FROM job_events WHERE demo = 1").fetchone()[0]
        demo_jobs.seed(c, now=now + timedelta(days=3))                     # idempotent: not re-dated
        assert jb.get(c, ids["Quarry Labs"])["published_at"] == (now - timedelta(days=14)).isoformat()
        assert c.execute("SELECT COUNT(*) FROM job_events WHERE demo = 1").fetchone()[0] == events
