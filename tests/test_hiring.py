"""specs/16: hiring workflow per application. Fake models, recorded IMAP messages; no network."""
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from backend import hiring as hr
from backend import mailer, replies
from backend import scheduling as sch
from backend import workflows as wf
from backend.hiring_flow import HiringFlow
from backend.main import create_app
from core import config, db
from core.llm import LLMError
from graph.context import Deps
from judges.fake_judge import FakeJudge
from tests import ui_fakes

DS = "baseline-data-scientist-entry"
NOW = datetime(2026, 10, 15, 15, 0, tzinfo=timezone.utc)          # a Thursday
ET = sch.zone("ET")


class Model:
    """Fake models for every role, by output type. `fail` makes every call raise (model unavailable)."""

    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def parse(self, *, model, system, user, output_type):
        self.calls.append((output_type.__name__, model))
        if self.fail:
            raise LLMError("quota")
        name = output_type.__name__
        if name == "SchedulingDraft":
            eid = user.split("- id: ", 1)[1].split(" |", 1)[0]
            line = next(ln for ln in user.splitlines() if ln.startswith(f"- id: {eid}"))
            source = line.split(" | ")[1].split(": ", 1)[-1]
            return wf.SchedulingDraft(subject="Next step", opening="Thank you for applying. We'd like to invite you "
                                      "to the next stage.", personal_line=f"Your work on {source} stood out.",
                                      evidence_id=eid, closing="We look forward to speaking with you.")
        if name == "ReplyReading":
            text = user.split("<candidate_reply>")[1].split("</candidate_reply>")[0].strip()
            if "works" in text:
                return replies.ReplyReading(label="slot_chosen", quote=text.split(".")[0],
                                            times=[replies.ReplyTime(quote="Tuesday 11:00", start="2026-10-20T11:00",
                                                                     timezone=None)])
            if "next week" in text:
                return replies.ReplyReading(label="times_proposed", quote=text)
            if "another offer" in text:
                return replies.ReplyReading(label="decline", quote="I've accepted another offer")
            if "?" in text:
                return replies.ReplyReading(label="question", quote=text, question=text)
            return replies.ReplyReading(label="unclear", quote=text)
        if name == "FeedbackSummary":
            ids = re.findall(r'<scorecard id="([^"]+)">', user)
            return hr.FeedbackSummary(
                agreements=[hr.Point(text="Both found the SQL work solid.",
                                     quotes=[hr.Cite(scorecard_id=i, quote="solid SQL") for i in ids])],
                disagreements=[hr.Point(text="We recommend advancing her.", quotes=[hr.Cite(scorecard_id=ids[0],
                                                                                       quote="solid SQL")])],
                open_questions=[hr.Point(text="Invented point.", quotes=[hr.Cite(scorecard_id=ids[0],
                                                                                 quote="never said this")])])
        if name == "MessageDraft":
            purpose = user.split("<purpose>")[1].split("</purpose>")[0].strip()
            return hr.MessageDraft(subject=f"Re: {purpose}", body=f"A short {purpose} message for the candidate.")
        if name == "RejectionDraft":
            from tests.test_workflows import good_rejection
            return good_rejection(user)
        raise AssertionError(name)


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    import importlib.util
    monkeypatch.setattr(sch, "now", lambda: NOW)
    path = str(tmp_path / "h.db")
    spec = importlib.util.spec_from_file_location("seed", config.ROOT / "scripts" / "seed_recruiter_demo.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    seed.seed(path)
    return path


@pytest.fixture
def delivered(monkeypatch):
    """Every email handed to the provider."""
    out = []
    real = mailer.deliver

    def record(o):
        real(o)
        out.append(o)
    monkeypatch.setattr(mailer, "deliver", record)
    return out


def client(path, model=None):
    return TestClient(create_app(Deps(path, FakeJudge(script=ui_fakes.best), planner=ui_fakes.AlwaysPlanner(),
                                      writer=model)))


def ds(api):
    job = next(j for j in api.get("/jobs").json() if j["baseline_id"] == DS)
    return job, api.get(f"/jobs/{job['id']}/applicants").json()["applicants"]


def act(api, app_id, **answer):
    r = api.post(f"/applications/{app_id}/workflow/action", json=answer)
    assert r.status_code == 200, r.text
    return r.json()


def approve(api, app_id):
    """The recruiter approves the draft shown at the approve step."""
    v = api.get(f"/applications/{app_id}/workflow").json()
    assert v["step"]["step"] == "approve_email", v["step"]
    return act(api, app_id, action="send", email_id=v["step"]["email_id"])


def started(api, path, confirm=True):
    job, rows = ds(api)
    a = rows[0]["application_id"]
    v = api.post(f"/applications/{a}/workflow", json={"confirm_move": confirm}).json()
    return job, a, v


def reply(api, app_id, text):
    r = api.post(f"/applications/{app_id}/workflow/reply", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def to_feedback(api, a):
    """Invite → "Tuesday 11:00 works" → confirm the candidate's slot → send confirmation → interview passes."""
    approve(api, a)
    v = reply(api, a, "Tuesday 11:00 works for me. Thanks!")
    slot = v["step"]["slots"][0]
    v = act(api, a, action="confirm", start=slot["start"])
    v = approve(api, a)
    assert v["step"]["step"] == "scheduled"
    api.app.state.rs.hiring.tick(at=NOW + timedelta(days=10))
    return api.get(f"/applications/{a}/workflow").json()


def scorecard(api, a, who, ratings, notes, rec="yes"):
    return api.post(f"/applications/{a}/workflow/scorecards", json={
        "interviewer": who, "notes": notes, "recommendation": rec,
        "ratings": [{"area": k, "rating": r, "notes": n} for k, (r, n) in ratings.items()]})


# --- §0: start from any candidate; the workflow stays reachable ---------------------------------------------------

def test_start_from_a_candidate_who_is_not_shortlisted_asks_first(seeded, delivered):
    api = client(seeded, Model())
    job, rows = ds(api)
    a = rows[0]["application_id"]
    assert rows[0]["status"] == "new"
    r = api.post(f"/applications/{a}/workflow", json={})
    assert r.status_code == 409 and r.json()["detail"] == "Move to Shortlisted and start?"
    v = api.post(f"/applications/{a}/workflow", json={"confirm_move": True}).json()
    assert v["application"]["board_stage"] == "Shortlisted" and v["step"]["step"] == "approve_email"
    assert v["step"]["email"]["status"] == "draft" and not delivered


def test_bulk_start_without_ticking_shortlisted_candidates(seeded):
    api = client(seeded)
    job, rows = ds(api)
    ids = [r["application_id"] for r in rows[:3]]
    api.patch(f"/applications/{ids[0]}", json={"status": "shortlisted"})
    r = api.post(f"/jobs/{job['id']}/workflows", json={"application_ids": ids})
    assert r.status_code == 409 and rows[1]["name"] in r.json()["detail"] and rows[0]["name"] not in r.json()["detail"]
    out = api.post(f"/jobs/{job['id']}/workflows", json={"application_ids": ids, "confirm_move": True}).json()
    assert out["started"] == ids
    assert {t["application_id"] for t in api.get("/recruiter/tasks").json()} == set(ids)


def test_after_sending_the_workflow_page_shows_where_it_is(seeded):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    v = approve(api, a)
    assert v["step"]["step"] == "await_reply" and v["workflow"]["status"] == "waiting_candidate"
    assert v["thread"][0]["direction"] == "out" and "[RS-" in v["thread"][0]["subject"]
    assert v["application"]["board_stage"] == "Scheduling"
    again = api.get(f"/applications/{a}/workflow").json()              # any time later
    assert again["step"]["step"] == "await_reply"


# --- §3 graph paths -------------------------------------------------------------------------------------------

def test_slot_chosen_to_feedback_to_advance(seeded, delivered):
    model = Model()
    api = client(seeded, model)
    _, a, v = started(api, seeded)
    assert v["step"]["title"] == "Approve scheduling email"
    approve(api, a)
    v = reply(api, a, "Tuesday 11:00 works for me. Thanks!")
    assert v["step"]["step"] == "confirm_slot" and v["step"]["title"] == "Reply received – pick a slot"
    assert v["step"]["reply"]["reading"]["label"] == "slot_chosen"
    first = v["step"]["slots"][0]
    assert first["source"] == "candidate" and first["fits"] and first["start"].startswith("2026-10-20T11:00")
    assert len([s for s in v["step"]["slots"] if s["fits"]]) == 3
    v = act(api, a, action="confirm", start=first["start"])
    assert v["step"]["step"] == "approve_email" and v["step"]["purpose"] == "confirmation"
    assert v["step"]["email"]["attachments"][0]["filename"] == "invite.ics"
    v = approve(api, a)
    assert v["step"]["step"] == "scheduled" and v["workflow"]["badge"]["label"].startswith("Scheduled Tue Oct 20, 11:00")
    assert v["application"]["board_stage"] == "Phone screen"
    ics = delivered[-1].attachments[0]["text"]
    assert "DTSTART:20261020T150000Z" in ics and "METHOD:REQUEST" in ics
    api.app.state.rs.hiring.tick(at=NOW + timedelta(days=10))          # the interview time passed
    v = api.get(f"/applications/{a}/workflow").json()
    assert v["step"]["step"] == "enter_feedback" and v["workflow"]["badge"]["label"] == "Feedback due"
    assert v["step"]["focus"]                                            # the stage's focus areas
    assert api.post(f"/applications/{a}/workflow/action", json={"action": "summarize"}).status_code == 422
    area = v["step"]["focus"][0]
    scorecard(api, a, "Priya", {area: (3, "solid SQL and clear explanations")}, "Ask about scale next round?")
    scorecard(api, a, "Sam", {area: (4, "solid SQL, fast")}, "Good.", "strong_yes")
    v = act(api, a, action="summarize")
    s = v["step"]["summary"]
    assert v["step"]["step"] == "decide" and s["method"] == "model" and s["recommendations"]["yes"] == 1
    assert [p["text"] for p in s["agreements"]] == ["Both found the SQL work solid."]
    assert s["disagreements"] == [] and s["open_questions"] == []        # recommending / unquoted points dropped
    assert len(s["notes"]) == 2
    v = act(api, a, action="advance")
    assert v["step"]["step"] == "approve_email" and v["workflow"]["round"] == 1
    assert "interview" in v["step"]["email"]["body"].lower()
    timeline = {t["key"]: t["status"] for t in v["timeline"]}
    assert timeline["0:decision"] == "done" and timeline["1:scheduling"] == "current" and timeline["hired"] == "upcoming"
    assert len(delivered) == 2


def test_times_proposed_next_week_offers_slots_from_next_monday(seeded):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    approve(api, a)
    v = reply(api, a, "Thanks for the invite! Can we do next week instead?")
    assert v["step"]["step"] == "confirm_slot"
    starts = [datetime.fromisoformat(s["start"]) for s in v["step"]["slots"]]
    assert all(s.date() >= sch.next_monday(NOW) for s in starts) and len(starts) == 3


def test_question_is_answered_after_approval(seeded, delivered):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    approve(api, a)
    v = reply(api, a, "Will the interview be on video?")
    assert v["step"]["step"] == "approve_email" and v["step"]["purpose"] == "answer"
    inbound = v["thread"][-1]
    assert v["step"]["email"]["in_reply_to"] == inbound["message_id"] or inbound["message_id"] is None
    assert len(delivered) == 1
    v = approve(api, a)
    assert v["step"]["step"] == "await_reply" and len(delivered) == 2


def test_decline_waits_for_confirmation_then_closes(seeded, delivered):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    approve(api, a)
    v = reply(api, a, "Thank you. I've accepted another offer, so I'd like to withdraw.")
    assert v["step"]["step"] == "confirm_withdrawal" and v["application"]["board_stage"] == "Scheduling"
    v = act(api, a, action="confirm")
    assert v["workflow"]["status"] == "closed" and v["workflow"]["outcome"] == "withdrew"
    assert v["application"]["board_stage"] == "Not moving forward" and len(delivered) == 1
    assert v["timeline"][-1]["name"] == "Withdrew"


def test_unclear_reply_lets_the_recruiter_choose(seeded):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    approve(api, a)
    v = reply(api, a, "Hmm, let me think about it.")
    assert v["step"]["step"] == "choose" and {o["key"] for o in v["step"]["options"]} == {"slots", "answer",
                                                                                           "withdrawal", "wait"}
    assert api.post(f"/applications/{a}/workflow/action", json={"action": "choose", "option": "hired"}).status_code == 422
    v = act(api, a, action="choose", option="slots")
    assert v["step"]["step"] == "confirm_slot"


def test_no_reply_drafts_a_nudge_for_approval(seeded, delivered):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    approve(api, a)
    flow = api.app.state.rs.hiring
    assert flow.tick(at=datetime.now(timezone.utc) + timedelta(days=1)) == []
    assert flow.tick(at=datetime.now(timezone.utc) + timedelta(days=config.NUDGE_DAYS + 0.1)) == [a]
    v = api.get(f"/applications/{a}/workflow").json()
    assert v["step"]["purpose"] == "nudge" and v["step"]["title"] == "No reply – send a nudge?"
    assert len(delivered) == 1                                              # drafted, not sent
    assert any(t["title"].startswith("No reply – send a nudge?") for t in api.get("/recruiter/tasks").json())


def test_feedback_then_not_moving_forward_uses_the_rejection_draft(seeded, delivered):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    v = to_feedback(api, a)
    scorecard(api, a, "Priya", {v["step"]["focus"][0]: (1, "could not explain joins")}, "No.", "no")
    act(api, a, action="summarize")
    v = act(api, a, action="reject")
    assert v["step"]["purpose"] == "not_moving_forward" and v["step"]["email"]["kind"] == "not_moving_forward"
    n = len(delivered)
    v = approve(api, a)
    assert v["workflow"]["status"] == "closed" and v["workflow"]["outcome"] == "not_moving_forward"
    assert v["application"]["board_stage"] == "Not moving forward" and len(delivered) == n + 1


def test_last_round_advance_drafts_the_offer_then_hired(seeded):
    api = client(seeded, Model())
    job, a, _ = started(api, seeded)
    stages = api.get(f"/jobs/{job['id']}/settings").json()["stages"]
    for s in stages:                                                        # keep one interview round
        if s["name"] in ("Interview", "Hiring manager review"):
            api.delete(f"/jobs/{job['id']}/stages/{s['id']}")
    v = to_feedback(api, a)
    scorecard(api, a, "Priya", {v["step"]["focus"][0]: (4, "excellent")}, "Great.", "strong_yes")
    act(api, a, action="summarize")
    v = act(api, a, action="advance")
    assert v["step"]["purpose"] == "offer" and not hr.OFFER_TERMS.search(v["step"]["email"]["body"])
    v = approve(api, a)
    assert v["step"]["step"] == "choose" and v["application"]["board_stage"] == "Offer"
    v = act(api, a, action="choose", option="hired")
    assert v["workflow"]["outcome"] == "hired" and v["application"]["board_stage"] == "Hired"
    assert {t["key"]: t["status"] for t in v["timeline"]}["hired"] == "current"


def test_hold_pauses_and_nothing_moves_until_resumed(seeded):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    v = act(api, a, action="hold")
    assert v["workflow"]["status"] == "paused" and v["workflow"]["badge"]["label"] == "Paused"
    assert api.post(f"/applications/{a}/workflow/action", json={"action": "send", "email_id": v["step"]["email_id"]}
                    ).status_code == 422
    assert api.get("/recruiter/tasks").json() == []
    v = act(api, a, action="resume")
    assert v["workflow"]["status"] == "needs_action"


# --- Approval, idempotency, durability ------------------------------------------------------------------------------

def test_no_email_leaves_without_an_approve_step_on_any_path(seeded, delivered):
    """Every path above, counted: each delivered email is a draft the recruiter approved, once."""
    api = client(seeded, Model())
    approved = []
    real = api.app.state.rs.hiring.act

    def spy(app_id, answer, actor="recruiter"):
        if answer.get("action") == "send":
            assert actor == "recruiter"
            approved.append(answer["email_id"])
        return real(app_id, answer, actor)
    api.app.state.rs.hiring.act = spy
    job, rows = ds(api)
    ids = [r["application_id"] for r in rows[:5]]
    api.post(f"/jobs/{job['id']}/workflows", json={"application_ids": ids, "confirm_move": True})
    for a in ids:
        approve(api, a)
    reply(api, ids[0], "Tuesday 11:00 works for me.")
    act(api, ids[0], action="confirm", start=api.get(f"/applications/{ids[0]}/workflow").json()["step"]["slots"][0]["start"])
    approve(api, ids[0])
    reply(api, ids[1], "Will it be on video?")
    reply(api, ids[2], "I've accepted another offer.")
    act(api, ids[2], action="confirm")
    reply(api, ids[3], "Hmm.")
    api.app.state.rs.hiring.tick(at=datetime.now(timezone.utc) + timedelta(days=30))       # nudge for ids[4]
    sent = {r[0] for r in db.connect(seeded).execute("SELECT id FROM emails WHERE status = 'sent'")}
    assert sent == set(approved) and len(delivered) == len(approved) == 6       # 5 invites, 1 confirmation
    pending = db.connect(seeded).execute("SELECT COUNT(*) FROM emails WHERE status = 'draft'").fetchone()[0]
    assert pending == 2                                                      # answer for ids[1], nudge for ids[4]


def test_send_is_idempotent(seeded, delivered):
    api = client(seeded, Model())
    _, a, v = started(api, seeded)
    eid = v["step"]["email_id"]
    act(api, a, action="send", email_id=eid)
    r = api.post(f"/applications/{a}/workflow/action", json={"action": "send", "email_id": eid})
    assert r.status_code == 422 and len(delivered) == 1
    assert api.post(f"/emails/{eid}/send").status_code == 422
    with pytest.raises(ValueError):
        wf.send_email(db.connect(seeded), eid)
    assert len(delivered) == 1


def test_a_restart_resumes_at_the_same_step(seeded):
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)
    approve(api, a)
    reply(api, a, "Tuesday 11:00 works for me.")
    restarted = client(seeded, Model())                                     # new server: only the SQLite files
    v = restarted.get(f"/applications/{a}/workflow").json()
    assert v["step"]["step"] == "confirm_slot"
    v = act(restarted, a, action="confirm", start=v["step"]["slots"][0]["start"])
    assert v["step"]["purpose"] == "confirmation"
    fresh = HiringFlow(seeded, None)                                         # a third process
    assert fresh.graph.get_state(fresh._cfg(hr.get(db.connect(seeded), a))).interrupts


# --- Model fallbacks -------------------------------------------------------------------------------------------------

def test_without_the_models_code_rules_and_templates_are_used(seeded):
    api = client(seeded, Model(fail=True))
    _, a, v = started(api, seeded)
    assert v["step"]["email"]["template"]
    approve(api, a)
    v = reply(api, a, replies.CANNED["slot"])
    reading = v["step"]["reply"]["reading"]
    assert reading["method"] == replies.CODE_RULES and reading["label"] == "slot_chosen"
    assert "Reply model unavailable" in reading["notes"][0]


@pytest.mark.parametrize("key,label", [("slot", "slot_chosen"), ("next_week", "times_proposed"),
                                       ("question", "question"), ("decline", "decline")])
def test_code_rules_on_the_canned_replies(key, label):
    r = replies.code_reading(replies.CANNED[key], "America/New_York", NOW)
    assert r["label"] == label and r["quote"] in replies.CANNED[key]


def test_code_summary_agreements_disagreements_questions():
    cards = [{"id": "a", "notes": "Probe system design next round.", "recommendation": "yes",
              "ratings": [{"area": "SQL", "rating": 4, "notes": "Strong joins."},
                          {"area": "Python", "rating": 1, "notes": "Struggled with pandas."}]},
             {"id": "b", "notes": "", "recommendation": "no",
              "ratings": [{"area": "SQL", "rating": 3, "notes": "Good."}, {"area": "Python", "rating": 4, "notes": ""}]}]
    s = hr.code_summary(cards)
    assert [p["text"] for p in s["agreements"]] == ["SQL: interviewers agree, rated 3–4 of 4."]
    assert [p["text"] for p in s["disagreements"]] == ["Python: ratings differ (1–4 of 4)."]
    assert s["open_questions"][0]["quotes"] == [{"scorecard_id": "a", "quote": "Probe system design next round."}]


# --- Slots and .ics -------------------------------------------------------------------------------------------------

def test_slots_respect_windows_and_existing_bookings():
    windows = sch.parse_availability("Mon–Wed 10:00–14:00 ET")
    booked = [(datetime(2026, 10, 19, 10, 0, tzinfo=ET), datetime(2026, 10, 19, 11, 0, tzinfo=ET))]
    slots = sch.propose(windows, 30, booked, at=NOW)
    assert [s.start for s in slots] == [datetime(2026, 10, 19, 11, 0, tzinfo=ET), datetime(2026, 10, 20, 10, 0, tzinfo=ET),
                                        datetime(2026, 10, 21, 10, 0, tzinfo=ET)]
    for s in slots:
        assert sch.fits(s.start, timedelta(minutes=30), windows) and sch.free(s.start, timedelta(minutes=30), booked)
    late = sch.propose(windows, 30, [], preferred=[datetime(2026, 10, 22, 11, 0, tzinfo=ET)], at=NOW)
    assert late[-1].source == "candidate" and not late[-1].fits and late[-1].note == "outside the availability windows"
    assert sch.parse_availability("whenever") == []


def test_ics_is_valid():
    inv = sch.Invite(uid="u-1@rightstaff.local", start=datetime(2026, 10, 20, 11, 0, tzinfo=ET),
                     end=datetime(2026, 10, 20, 11, 30, tzinfo=ET), summary="Phone screen: Data Scientist at Acme, "
                     "a long title that needs folding over the 75 octet limit", description="30-minute call; bring\nnotes",
                     organizer="r@example.com", organizer_name="Hiring team", attendee="demo@example.org",
                     attendee_name="Maya Torres")
    text = sch.IcsCalendar().ics(inv, stamp=NOW)
    lines = text.split("\r\n")
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert all(len(ln.encode()) <= 75 for ln in lines)
    unfolded = text.replace("\r\n ", "").split("\r\n")
    assert unfolded[0] == "BEGIN:VCALENDAR" and unfolded[-2] == "END:VCALENDAR"
    props = dict(ln.split(":", 1) for ln in unfolded if ":" in ln and not ln.startswith(("BEGIN", "END")))
    for key in ("VERSION", "PRODID", "METHOD", "UID", "DTSTAMP", "DTSTART", "DTEND", "SUMMARY"):
        assert key in props
    assert props["DTSTART"] == "20261020T150000Z" and props["DTEND"] == "20261020T153000Z"
    assert props["DESCRIPTION"] == "30-minute call\\; bring\\nnotes"
    assert unfolded.count("BEGIN:VEVENT") == unfolded.count("END:VEVENT") == 1


# --- §4 IMAP (recorded messages) -------------------------------------------------------------------------------------

def raw_mail(subject, body, message_id, in_reply_to=None):
    head = f"From: Demo <demo@example.org>\r\nTo: demo@example.org\r\nSubject: {subject}\r\nMessage-ID: {message_id}\r\n" \
           f"Date: Thu, 15 Oct 2026 16:00:00 +0000\r\n"
    if in_reply_to:
        head += f"In-Reply-To: {in_reply_to}\r\nReferences: {in_reply_to}\r\n"
    return (head + "Content-Type: text/plain; charset=utf-8\r\n\r\n" + body).encode()


class FakeImap:
    def __init__(self, messages):
        self.messages, self.log = messages, []

    def login(self, user, password):
        self.log.append("login")

    def select(self, box, readonly=False):
        assert readonly
        self.log.append("select")

    def search(self, charset, *criteria):
        self.log.append(criteria)
        return "OK", [" ".join(str(i + 1) for i in range(len(self.messages))).encode()]

    def fetch(self, num, what):
        assert "PEEK" in what                                                 # never marks a message read
        raw = self.messages[int(num) - 1]
        self.log.append(what)
        if "HEADER.FIELDS" in what:                                           # headers first
            return "OK", [(b"1 (BODY[HEADER.FIELDS] {n})", raw.split(b"\r\n\r\n", 1)[0] + b"\r\n\r\n"), b")"]
        return "OK", [(b"1 (BODY[] {n})", raw), b")"]

    def logout(self):
        self.log.append("logout")


def test_imap_replies_are_matched_by_token_and_read(seeded, monkeypatch):
    monkeypatch.setattr(config, "IMAP_USER", "demo@example.org")
    monkeypatch.setattr(config, "IMAP_PASSWORD", "x")
    api = client(seeded, Model())
    job, rows = ds(api)
    a, b = rows[0]["application_id"], rows[1]["application_id"]
    api.post(f"/jobs/{job['id']}/workflows", json={"application_ids": [a, b], "confirm_move": True})
    approve(api, a)
    approve(api, b)
    conn = db.connect(seeded)
    ours = hr.last_out(conn, a)
    our_b = hr.last_out(conn, b)
    box = FakeImap([
        raw_mail(ours["subject"], "copy of our own email", ours["message_id"]),            # ours: skipped
        raw_mail(ours["subject"], "our copy, Message-ID rewritten", "<rewritten@gmail>"),   # ours by subject
        raw_mail(f"Re: {ours['subject']}", "Tuesday 11:00 works for me.\r\n\r\nOn Thu, Oct 15, 2026 at 9:00 AM "
                 "Hiring team <demo@example.org>\r\nwrote:\r\n> Hi Maya", "<r1@mail.example>", ours["message_id"]),
        raw_mail("Re: no token here", "Will it be on video?", "<r2@mail.example>", our_b["message_id"]),  # by header
        raw_mail("Unrelated newsletter [RS-ffffff]", "Hello", "<n1@mail.example>"),          # unknown token
        raw_mail("A newsletter", "Not for us", "<n2@mail.example>"),                        # headers only: not read
    ])
    out = api.app.state.rs.hiring.poll_inbox(replies.ImapInbox(lambda: box))
    assert [x["application_id"] for x in out["matched"]] == [a, b] and out["fetched"] == 5 and out["error"] is None
    assert sorted(x["why"] for x in out["skipped"]) == ["already stored", "no workflow with this token", "our own sent copy"]
    assert ("SINCE",) == box.log[2][:1]                                       # searched by date only
    status = api.get("/workflows/inbox").json()
    assert status["last_poll"]["fetched"] == 5
    va = api.get(f"/applications/{a}/workflow").json()
    assert va["step"]["step"] == "confirm_slot" and va["thread"][-1]["body"] == "Tuesday 11:00 works for me."
    assert va["thread"][-1]["source"] == "imap"
    vb = api.get(f"/applications/{b}/workflow").json()
    assert vb["step"]["purpose"] == "answer"
    assert api.app.state.rs.hiring.poll_inbox(replies.ImapInbox(lambda: box))["matched"] == []   # once only
    assert box.log[-1] == "logout"


# --- §5 Board moves -------------------------------------------------------------------------------------------------

def test_board_drag_updates_the_workflow_and_sends_nothing(seeded, delivered):
    api = client(seeded, Model())
    job, a, v = started(api, seeded)
    stages = {s["name"]: s["id"] for s in api.get(f"/jobs/{job['id']}/settings").json()["stages"]}
    api.patch(f"/applications/{a}/stage", json={"stage_id": stages["Interview"]})
    v = api.get(f"/applications/{a}/workflow").json()
    assert v["step"]["step"] == "choose" and v["step"]["title"] == "Send stage email? (Interview)"
    assert v["workflow"]["round"] == 1 and not delivered
    assert v["drafts"] == []                                               # the pending invite was withdrawn
    skipped = [t for t in v["timeline"] if t["status"] == "skipped"]
    assert [t["key"] for t in skipped] == ["1:scheduling", "1:scheduled"]
    assert any(t["title"].startswith("Send stage email? (Interview)") for t in api.get("/recruiter/tasks").json())
    board = api.get(f"/jobs/{job['id']}/pipeline").json()["stages"]
    card = next(c for s in board for c in s["cards"] if c["application_id"] == a)
    assert card["workflow"]["label"] == "Needs your action"
    # A candidate without a workflow, dragged to Offer: the workflow starts there and asks
    other = ds(api)[1][3]["application_id"]
    api.patch(f"/applications/{other}/stage", json={"stage_id": stages["Offer"]})
    vo = api.get(f"/applications/{other}/workflow").json()
    assert vo["step"]["title"] == "Send stage email? (offer note)" and not delivered


def test_errors_reach_the_browser_with_cors_headers(seeded, monkeypatch):
    """A server error must not look like "Failed to fetch": the 500 carries CORS headers and a message."""
    api = client(seeded, Model())
    monkeypatch.setattr(hr, "view", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    job, rows = ds(api)
    r = api.get(f"/applications/{rows[0]['application_id']}/workflow", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 500 and r.json()["detail"] and r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_workflows_do_not_need_the_judge(seeded, monkeypatch):
    """Starting a workflow builds only the email model, never the scoring deps (no Jev key needed)."""
    from backend import main
    monkeypatch.delenv("RIGHTSTAFF_DEPS_FACTORY", raising=False)
    monkeypatch.setattr(config, "DB_PATH", seeded)
    monkeypatch.setattr(main, "default_deps", lambda: (_ for _ in ()).throw(ValueError("JUDGE=jev needs JEV_API_KEY")))
    monkeypatch.setattr("core.llm.RoutedLLM.parse", lambda self, **kw: (_ for _ in ()).throw(LLMError("offline")))
    api = TestClient(main.create_app())
    job, rows = ds(api)
    r = api.post(f"/applications/{rows[0]['application_id']}/workflow", json={"confirm_move": True})
    assert r.status_code == 200 and r.json()["step"]["email"]["template"]


def test_a_reply_that_arrives_at_another_step_shows_in_tasks(seeded, monkeypatch):
    """IMAP replies that come while the workflow waits for something else are kept and listed in Tasks."""
    monkeypatch.setattr(config, "IMAP_USER", "demo@example.org")
    monkeypatch.setattr(config, "IMAP_PASSWORD", "x")
    api = client(seeded, Model())
    _, a, _ = started(api, seeded)                                          # waiting for the invite's approval
    token = hr.token_for(a)
    box = FakeImap([raw_mail(f"Re: Next step [RS-{token}]", "Can we talk tomorrow?", "<r9@mail.example>")])
    out = api.app.state.rs.hiring.poll_inbox(replies.ImapInbox(lambda: box))
    assert [x["application_id"] for x in out["matched"]] == [a]
    titles = [t["title"] for t in api.get("/recruiter/tasks").json()]
    assert any(t.startswith("Reply received") for t in titles)
    v = api.get(f"/applications/{a}/workflow").json()
    assert v["step"]["unread"] == 1 and v["thread"][-1]["source"] == "imap"


def test_imap_errors_are_reported_not_raised(seeded, monkeypatch):
    monkeypatch.setattr(config, "IMAP_USER", "demo@example.org")
    monkeypatch.setattr(config, "IMAP_PASSWORD", "x")
    api = client(seeded, Model())

    def broken():
        raise OSError("login failed")
    out = api.app.state.rs.hiring.poll_inbox(replies.ImapInbox(broken))
    assert out["error"] == "OSError: login failed" and api.get("/workflows/inbox").json()["last_poll"]["error"]
