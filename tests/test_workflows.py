"""specs/13 §2–§5: decision workflows, email sending, candidate median. FakeJudge and fake writers; no network."""
import pytest
from fastapi.testclient import TestClient

from backend import mailer
from backend import workflows as wf
from backend.main import create_app
from core import config, db
from core.llm import LLMError
from graph.context import Deps
from judges.fake_judge import FakeJudge
from tests import ui_fakes

DS = "baseline-data-scientist-entry"


class Writer:
    """Fake email model: returns the queued drafts in order (or raises), recording each call."""

    def __init__(self, *drafts):
        self.drafts, self.calls = list(drafts), []

    def parse(self, *, model, system, user, output_type):
        self.calls.append((model, user))
        d = self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]
        if isinstance(d, Exception):
            raise d
        return d(user) if callable(d) else d


def good_rejection(user):
    ids = [line.split("id: ", 1)[1].split(" |", 1)[0] for line in user.splitlines() if line.startswith("- id: ")]
    return wf.RejectionDraft(
        subject="Your application for Data Scientist", opening="Thank you for applying. We have decided not to move "
        "forward with your application for this role.",
        areas=[wf.Area(reason_id=i, text=f"Strengthen area {n + 1} with work that shows it.") for n, i in enumerate(ids[:2])],
        next_step_type="build_project", next_step="Build a project showing an A/B test with a measured result.",
        closing="Thank you again for your time.")


@pytest.fixture
def seeded(tmp_path):
    import importlib.util
    path = str(tmp_path / "w.db")
    spec = importlib.util.spec_from_file_location("seed", config.ROOT / "scripts" / "seed_recruiter_demo.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    seed.seed(path)
    return path


def client(path, writer=None):
    return TestClient(create_app(Deps(path, FakeJudge(script=ui_fakes.best), planner=ui_fakes.AlwaysPlanner(),
                                      writer=writer)))


def ds_rows(api):
    job = next(j for j in api.get("/jobs").json() if j["baseline_id"] == DS)
    return job, api.get(f"/jobs/{job['id']}/applicants").json()["applicants"]


def by_name(rows, name):
    return next(r for r in rows if r["name"] == name)


def sent_count(path):
    return db.connect(path).execute("SELECT COUNT(*) FROM emails WHERE status = 'sent'").fetchone()[0]


# --- 4. Demo redirect and refusal -----------------------------------------------------------------------------

def test_demo_redirect_rewrites_recipient_subject_and_body(monkeypatch):
    monkeypatch.setattr(config, "DEMO_EMAIL_TO", "demo@example.org")
    out = mailer.address("maya.torres@example.com", "Maya Torres", "Next step", "Hello")
    assert out.actual_to == "demo@example.org" and out.intended_to == "maya.torres@example.com"
    assert out.subject == "[RightStaff demo → Maya Torres] Next step"
    assert out.body.startswith("Intended recipient: Maya Torres <maya.torres@example.com>")


def test_any_other_recipient_is_refused_while_the_redirect_is_set(monkeypatch):
    monkeypatch.setattr(config, "DEMO_EMAIL_TO", "demo@example.org")
    with pytest.raises(mailer.SendRefused):
        mailer.deliver(mailer.Outgoing("x@example.com", "x@example.com", "s", "b", "outbox"))
    mailer.deliver(mailer.Outgoing("x@example.com", "DEMO@example.org", "s", "b", "outbox"))     # allowed


def test_smtp_without_settings_is_refused_and_nothing_connects(monkeypatch):
    monkeypatch.setattr(config, "DEMO_EMAIL_TO", "demo@example.org")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    monkeypatch.setattr("smtplib.SMTP", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(mailer.SendRefused, match="SMTP is not configured"):
        mailer.deliver(mailer.Outgoing(None, "demo@example.org", "s", "b", "smtp"))


# --- 2. Not moving forward: reasons, draft, review, send ------------------------------------------------------

def test_drafting_sends_nothing_and_changes_no_status(seeded):
    api = client(seeded, Writer(good_rejection))
    _, rows = ds_rows(api)
    app = by_name(rows, "Keyword-heavy")
    out = api.post(f"/applications/{app['application_id']}/not-moving-forward").json()
    assert out["email"]["status"] == "draft" and sent_count(seeded) == 0
    assert api.get(f"/applications/{app['application_id']}").json()["status"] == "new"
    api.put(f"/emails/{out['email']['id']}", json={"body": out["email"]["body"] + "\nEdited."})     # Save draft
    assert sent_count(seeded) == 0
    assert api.post(f"/emails/{out['email']['id']}/cancel").json()["status"] == "cancelled"
    assert sent_count(seeded) == 0 and api.get(f"/applications/{app['application_id']}").json()["status"] == "new"


def test_send_moves_to_not_moving_forward_and_logs_the_email(seeded, monkeypatch):
    monkeypatch.setattr(config, "DEMO_EMAIL_TO", "demo@example.org")
    api = client(seeded, Writer(good_rejection))
    _, rows = ds_rows(api)
    app = by_name(rows, "Keyword-heavy")
    draft = api.post(f"/applications/{app['application_id']}/not-moving-forward").json()["email"]
    sent = api.post(f"/emails/{draft['id']}/send").json()
    assert sent["status"] == "sent" and sent["actual_to"] == "demo@example.org" and sent["provider"] == "outbox"
    assert sent["subject"].startswith("[RightStaff demo → Keyword-heavy]")
    assert api.get(f"/applications/{app['application_id']}").json()["status"] == "not_moving_forward"
    kinds = [a["kind"] for a in api.get(f"/applications/{app['application_id']}/activity").json()]
    assert sorted(kinds) == ["decision", "email", "stage"]
    log = api.get(f"/applications/{app['application_id']}/activity").json()
    assert any(a.get("email", {}) and a["email"]["body"] == sent["body"] for a in log)       # full text logged
    assert api.post(f"/emails/{draft['id']}/send").status_code == 422                          # not twice


def test_reasons_are_preselected_from_must_haves_gaps_and_missing_evidence(seeded):
    conn = db.connect(seeded)
    app = dict(conn.execute("SELECT a.* FROM applications a JOIN candidates c ON c.id = a.candidate_id "
                            "WHERE c.name = 'Keyword-heavy'").fetchone())
    from backend import recruiter as rc
    reasons = wf.build_reasons(conn, app, rc.get_job(conn, app["job_id"]))
    assert {r["source"] for r in reasons} >= {"must-have", "gap"}
    assert [r["selected"] for r in reasons][:3] == [True, True, True] and not any(r["selected"] for r in reasons[3:])
    text = " ".join(r["detail"] + r["advice"] for r in reasons)
    assert not wf.banned_issues(text)                                                         # no scores in reasons


def test_grounding_every_area_maps_to_a_selected_reason_else_redraft_then_template(seeded):
    def unmapped(user):
        d = good_rejection(user)
        d.areas[0].reason_id = "gap:invented"
        return d

    def leaky(user):
        d = good_rejection(user)
        d.closing = "You ranked in the top 40% of other candidates, so we will keep your resume on file."
        return d
    for bad in (unmapped, leaky):
        writer = Writer(bad)
        api = client(seeded, writer)
        _, rows = ds_rows(api)
        app = by_name(rows, "Career switcher")
        e = api.post(f"/applications/{app['application_id']}/not-moving-forward").json()["email"]
        assert len(writer.calls) == 2                                     # one redraft with the errors
        assert "failed these checks" in writer.calls[1][1]
        assert e["template"] and e["label"] == "Template draft" and e["checks"]["errors"] == []
        assert e["checks"]["model_errors"]


def test_selected_reasons_only_and_model_output_passes_checks(seeded):
    writer = Writer(good_rejection)
    api = client(seeded, writer)
    _, rows = ds_rows(api)
    app = by_name(rows, "Career switcher")
    first = api.post(f"/applications/{app['application_id']}/not-moving-forward").json()
    pick = [r["id"] for r in first["reasons"]][-2:]
    out = api.post(f"/applications/{app['application_id']}/not-moving-forward", json={"reason_ids": pick}).json()
    assert [r["id"] for r in out["reasons"] if r["selected"]] == pick
    prompt = writer.calls[-1][1]
    unselected = [r["id"] for r in out["reasons"] if r["id"] not in pick]
    assert all(f"id: {i} " not in prompt for i in unselected)            # the model sees only selected reasons
    e = out["email"]
    assert not e["template"] and e["checks"]["errors"] == [] and e["checks"]["words"] <= 150
    assert {a["reason_id"] for a in e["checks"]["areas"]} <= set(pick)
    assert "Career switcher" not in prompt and "Keyword-heavy" not in prompt      # no names in model input
    old = first["email"]["id"]
    assert api.get(f"/emails/{old}").json()["status"] == "cancelled"              # replaced draft


def test_model_unavailable_gives_a_template_draft(seeded):
    api = client(seeded, Writer(LLMError("daily limit reached")))
    _, rows = ds_rows(api)
    e = api.post(f"/applications/{by_name(rows, 'Resume only')['application_id']}/not-moving-forward").json()["email"]
    assert e["template"] and "not to move forward" in e["body"] and e["checks"]["errors"] == []
    assert wf.word_count(e["body"]) <= config.REJECTION_MAX_WORDS


def test_send_after_a_restart_still_works(seeded):
    api = client(seeded, Writer(good_rejection))
    _, rows = ds_rows(api)
    app = by_name(rows, "Keyword-heavy")
    draft = api.post(f"/applications/{app['application_id']}/not-moving-forward").json()["email"]
    restarted = client(seeded, Writer(good_rejection))                    # new server: no paused graph in memory
    assert restarted.post(f"/emails/{draft['id']}/send").json()["status"] == "sent"


# --- 3. Interview plan, pipeline, loop ------------------------------------------------------------------------

def test_default_plan_and_editing(seeded):
    api = client(seeded)
    job, _ = ds_rows(api)
    st = api.get(f"/jobs/{job['id']}/settings").json()["stages"]
    assert [s["name"] for s in st] == [*wf.DEFAULT_STAGES, "Not moving forward"] and st[-1]["terminal"]
    musts = {c["id"]: c["name"] for c in api.get(f"/jobs/{job['id']}").json()["baseline"]["criteria"]}
    phone = next(s for s in st if s["name"] == "Phone screen")
    assert phone["focus"] == [musts[m] for m in job["must_haves"]]
    st = api.post(f"/jobs/{job['id']}/stages", json={"name": "Take-home"}).json()
    take = next(s for s in st if s["name"] == "Take-home")
    st = api.patch(f"/jobs/{job['id']}/stages/{take['id']}", json={"name": "Take-home task"}).json()
    ids = [s["id"] for s in st if not s["terminal"]]
    ids.insert(3, ids.pop(ids.index(take["id"])))
    st = api.put(f"/jobs/{job['id']}/stages/order", json={"ids": ids}).json()
    assert [s["name"] for s in st][3] == "Take-home task"
    assert api.delete(f"/jobs/{job['id']}/stages/{st[-1]['id']}").status_code == 422        # terminal
    st = api.delete(f"/jobs/{job['id']}/stages/{take['id']}").json()
    assert "Take-home task" not in [s["name"] for s in st]


def test_stage_moves_are_logged(seeded):
    api = client(seeded)
    job, rows = ds_rows(api)
    a = rows[0]["application_id"]
    assert api.patch(f"/applications/{a}", json={"status": "shortlisted"}).json()["status"] == "shortlisted"
    board = api.get(f"/jobs/{job['id']}/pipeline").json()["stages"]
    interview = next(s for s in board if s["name"] == "Interview")
    assert api.patch(f"/applications/{a}/stage", json={"stage_id": interview["id"]}).json()["status"] == "in_loop"
    log = [x["summary"] for x in api.get(f"/applications/{a}/activity").json()]
    assert log == ["Moved from New to Shortlisted", "Moved from Shortlisted to Interview"]
    board = api.get(f"/jobs/{job['id']}/pipeline").json()["stages"]
    assert [c["application_id"] for c in next(s for s in board if s["name"] == "Interview")["cards"]] == [a]


def test_interview_loop_drafts_reviewed_then_sent_move_to_scheduling(seeded, monkeypatch):
    monkeypatch.setattr(config, "DEMO_EMAIL_TO", "demo@example.org")

    def sched(user):
        eid = user.split("- id: ", 1)[1].split(" |", 1)[0]
        line = next(ln for ln in user.splitlines() if ln.startswith(f"- id: {eid}"))
        source = line.split(" | ")[1].split(": ", 1)[-1]
        return wf.SchedulingDraft(subject="Next step", opening="Thank you for applying. We'd like to invite you to "
                                  "the next stage.", personal_line=f"Your work on {source} stood out.",
                                  evidence_id=eid, closing="We look forward to speaking with you.")
    api = client(seeded, Writer(sched))
    job, rows = ds_rows(api)
    picked = [r["application_id"] for r in rows[:2]]
    for a in picked:
        api.patch(f"/applications/{a}", json={"status": "shortlisted"})
    api.put(f"/jobs/{job['id']}/settings", json={"duration_minutes": 45, "availability": "Mon–Wed 10:00–14:00 ET"})
    out = api.post(f"/jobs/{job['id']}/interview-loop", json={"application_ids": picked}).json()
    assert len(out["drafts"]) == 2 and sent_count(seeded) == 0
    for d in out["drafts"]:
        assert not d["template"] and d["checks"]["errors"] == [] and d["checks"]["words"] <= 120
        assert "45-minute phone screen" in d["body"] and "Mon–Wed 10:00–14:00 ET" in d["body"] and job["title"] in d["body"]
    batch = api.get(f"/jobs/{job['id']}/interview-loop/{out['batch_id']}").json()["drafts"]
    assert {d["id"] for d in batch} == {d["id"] for d in out["drafts"]}
    api.post(f"/emails/{out['drafts'][0]['id']}/send")
    statuses = {a: api.get(f"/applications/{a}").json() for a in picked}
    stage_of = {a: s["stage"] for a, s in statuses.items()}
    sent_app = out["drafts"][0]["application_id"]
    assert stage_of[sent_app] == "Scheduling" and stage_of[[a for a in picked if a != sent_app][0]] == "Shortlisted"
    assert sent_count(seeded) == 1


def test_scheduling_line_must_cite_this_candidates_evidence(seeded):
    bad = wf.SchedulingDraft(subject="s", opening="o", personal_line="Your work on rockets stood out.",
                             evidence_id="not-an-id", closing="c")
    api = client(seeded, Writer(bad))
    job, rows = ds_rows(api)
    a = by_name(rows, "Strong early-career DS")["application_id"]
    api.patch(f"/applications/{a}", json={"status": "shortlisted"})
    d = api.post(f"/jobs/{job['id']}/interview-loop", json={"application_ids": [a]}).json()["drafts"][0]
    assert d["template"] and d["checks"]["errors"] == [] and d["checks"]["evidence_id"]
    assert "evidence_id must be one of" in " ".join(d["checks"]["model_errors"])


def test_loop_only_for_shortlisted(seeded):
    api = client(seeded)
    job, rows = ds_rows(api)
    assert api.post(f"/jobs/{job['id']}/interview-loop",
                    json={"application_ids": [rows[0]["application_id"]]}).status_code == 422


# --- 5. Median for the role ------------------------------------------------------------------------------------

def test_median_and_percentile_values(tmp_path, monkeypatch):
    from graph.pipeline import Pipeline
    from scoring import benchmarks as bm
    from tests.fixtures.archetypes import ARCHETYPES, TODAY
    monkeypatch.setattr(config, "MIN_POOL", 3)
    path = str(tmp_path / "m.db")
    conn = db.init_app_db(path)
    runs = {}
    for name in ("strong_ds", "keyword_heavy", "career_switcher", "resume_only"):
        cid, judge = ARCHETYPES[name](conn)
        judge.scripted = False
        runs[name] = Pipeline(Deps(path, judge, today=TODAY), print_timings=False).score(cid, DS)
    out = bm.for_run(conn, runs["career_switcher"]["run_id"])["percentiles"]
    others = sorted(r["final"] for n, r in runs.items() if n != "career_switcher")
    assert out["final"]["n"] == 3 and out["final"]["median"] == pytest.approx(others[1])
    assert out["final"]["value"] == pytest.approx(runs["career_switcher"]["final"])
    assert out["final"]["percentile"] == round(bm.percentile(out["final"]["value"], others))
    exp = sorted(r["dimension_scores"]["experience"] for n, r in runs.items() if n != "career_switcher")
    assert out["dimensions"]["experience"]["median"] == pytest.approx(exp[1])
