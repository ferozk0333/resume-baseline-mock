"""Hiring workflow per application (specs/16 §3): a LangGraph graph with a human step at every ⏸.

    START ─→ draft_invite → ⏸approve_email → send → check_inbox → ⏸await_reply
    ⏸await_reply ─(reply)→ read_reply ─┬─ slot_chosen / times_proposed → propose_slots → ⏸confirm_slot
                                        │      → draft_confirmation (.ics) → ⏸approve_email → send → ⏸scheduled
                                        ├─ question → draft_answer → ⏸approve_email → send → ⏸await_reply
                                        ├─ decline  → ⏸confirm_withdrawal → close
                                        └─ unclear  → ⏸choose (the recruiter picks the route)
    ⏸await_reply ─(no reply after NUDGE_DAYS)→ draft_nudge → ⏸approve_email → send → ⏸await_reply
    ⏸scheduled ─(interview time passes)→ request_feedback → ⏸enter_feedback → summarize_feedback → ⏸decide
    ⏸decide: advance → draft_invite (next interview) or draft_offer → ⏸approve_email → send → ⏸choose (outcome)
             not moving forward → draft_rejection (spec 13) → ⏸approve_email → send → close
    Board moves (any step) → stage_moved → ⏸choose ("Send stage email?"); nothing is sent.

Rules kept in code:
- The only node that delivers email is `send`, and it only sends the draft the recruiter approved at
  ⏸approve_email (its id is checked). `workflows.send_email` claims the draft first, so one draft is sent once.
- A reply's reading is shown, never acted on alone: every route ends at a ⏸ before anything is sent.
- One thread per application, checkpointed to SQLite: a restart resumes at the same step.
- A paused workflow ignores everything but Resume (and board moves, which only record where the card is).
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend import hiring as hr
from backend import recruiter as rc
from backend import replies
from backend import scheduling as sch
from backend import workflows as wf
from backend.rejection_flow import RejectionFlow, checkpointer
from core import config, db


class State(TypedDict, total=False):
    application_id: str
    job_id: str
    round: int
    draft_id: str | None
    purpose: str | None
    approved: str | None
    reply_id: str | None
    reading: dict | None
    slots: list[dict]
    chosen: dict | None
    interview_id: str | None
    summary: dict | None
    options: list[str]
    choose_title: str | None
    jump: str | None
    outcome: str | None
    error: str | None


# Recruiter actions per step; "choose" (pick any next step) and "jump" (board move) work at every step.
ALLOWED = {"approve_email": {"send", "discard"}, "await_reply": {"reply", "nudge", "propose"},
           "confirm_slot": {"confirm"}, "scheduled": {"done", "reschedule"}, "enter_feedback": {"summarize"},
           "decide": {"advance", "reject"}, "confirm_withdrawal": {"confirm", "not_withdrawal"}, "choose": set()}
OPTIONS = {"slots": ("propose_slots", "Propose interview slots"), "answer": ("draft_answer", "Draft an answer"),
           "withdrawal": ("confirm_withdrawal", "Treat as a withdrawal"), "wait": ("check_inbox", "Wait for a reply"),
           "invite": ("draft_invite", "Draft the stage invite"), "offer": ("draft_offer", "Draft the offer note"),
           "reject": ("draft_rejection", "Draft the not-moving-forward email"),
           "close": ("close", "Close without an email"), "hired": ("mark_hired", "Mark as hired"),
           "declined": ("close", "Candidate declined the offer"),
           "external": ("scheduled", "Interview arranged outside RightStaff"),
           "feedback": ("request_feedback", "Interview done: enter feedback")}
DISCARD_TO = {"nudge": "check_inbox", "answer": "check_inbox", "confirmation": "propose_slots",
              "offer": "decide", "not_moving_forward": "decide"}
REPLY_ROUTES = {"slot_chosen": "propose_slots", "times_proposed": "propose_slots", "question": "draft_answer",
                "decline": "confirm_withdrawal"}
NODES = ["draft_invite", "approve_email", "send", "check_inbox", "await_reply", "read_reply", "propose_slots",
         "confirm_slot", "draft_confirmation", "scheduled", "request_feedback", "enter_feedback", "summarize_feedback",
         "decide", "draft_offer", "draft_nudge", "draft_answer", "draft_rejection", "confirm_withdrawal", "choose",
         "stage_moved", "mark_hired", "close"]


class HiringFlow:
    def __init__(self, db_path: str, writer, saver=None, rejection: RejectionFlow | None = None):
        self.db_path, self.writer = db_path, writer
        self.saver = saver or checkpointer(db_path)
        self.rejection = rejection or RejectionFlow(db_path, writer, saver=self.saver)
        self.graph = self._build().compile(checkpointer=self.saver)
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    # --- helpers ----------------------------------------------------------------------------------------------
    def _conn(self):
        return db.connect(self.db_path)

    def _ctx(self, state: State):
        conn = self._conn()
        app = hr.app_row(conn, state["application_id"])
        return conn, app, rc.get_job(conn, app["job_id"])

    def _round_stage(self, conn, job_id: str, round_: int) -> dict | None:
        rs = hr.rounds(conn, job_id)
        return rs[round_] if rs and round_ < len(rs) else None

    def _ask(self, state: State, step: str, status: str, title: str, payload: dict | None = None, *,
             phase: str | None = None, due: datetime | None = None, round_: int | None = None) -> dict:
        conn = self._conn()
        hr.set_step(conn, state["application_id"], step=step, status=status, title=title, payload=payload or {},
                    phase=phase, due=due, round_=state.get("round", 0) if round_ is None else round_)
        return interrupt({"step": step, **(payload or {})}) or {}

    def _generic(self, state: State, ans: dict) -> Command | None:
        """Actions any step accepts: pick a next step, or a board move."""
        if ans.get("action") == "jump":
            return Command(goto="stage_moved", update={"jump": ans["stage_id"]})
        if ans.get("action") == "choose":
            node = OPTIONS[ans["option"]][0]
            update = {"outcome": {"declined": "declined", "close": "closed"}[ans["option"]]} \
                if ans["option"] in ("declined", "close") else {}
            if ans["option"] == "external":
                update["interview_id"] = None
            return Command(goto=node, update=update)
        return None

    def _move(self, conn, app: dict, stage_name: str, reason: str) -> None:
        target = next((s for s in wf.stages(conn, app["job_id"]) if s["name"] == stage_name), None)
        if target and target["id"] != app.get("stage_id"):
            wf.move(conn, app, target["id"], reason=reason)

    # --- drafting nodes (model or template; nothing is sent) ---------------------------------------------------
    def draft_invite(self, state: State) -> Command:
        conn, app, job = self._ctx(state)
        r = self._round_stage(conn, job["id"], state.get("round", 0))
        if r is None:
            return Command(goto="draft_offer")
        sched = next((s for s in wf.stages(conn, job["id"]) if s["name"] == "Scheduling"), r)
        email = wf.draft_scheduling(conn, app, job, sched, self.writer, meeting=r["name"], kind="scheduling",
                                    token=hr.token_for(app["id"]))
        hr.event(conn, app["id"], "drafted", {"purpose": "scheduling", "email_id": email["id"], "model": email["model"]})
        return Command(goto="approve_email", update={"draft_id": email["id"], "purpose": "scheduling", "error": None})

    def _draft(self, state: State, purpose: str, **kw) -> dict:
        conn, app, job = self._ctx(state)
        r = self._round_stage(conn, job["id"], state.get("round", 0))
        email = hr.draft_message(conn, app, job, purpose, r["name"] if r else "interview", self.writer,
                                 token=hr.token_for(app["id"]), **kw)
        hr.event(conn, app["id"], "drafted", {"purpose": purpose, "email_id": email["id"], "model": email["model"]})
        return {"draft_id": email["id"], "purpose": purpose, "error": None}

    def draft_nudge(self, state: State) -> dict:
        return self._draft(state, "nudge")

    def draft_offer(self, state: State) -> dict:
        return self._draft(state, "offer")

    def draft_answer(self, state: State) -> dict:
        conn = self._conn()
        msg = hr.get_message(conn, state["reply_id"]) if state.get("reply_id") else None
        question = (state.get("reading") or {}).get("question") or (msg or {}).get("body")
        return self._draft(state, "answer", candidate_message=question, in_reply_to=(msg or {}).get("message_id"))

    def draft_rejection(self, state: State) -> dict:
        out = self.rejection.draft(state["application_id"])
        conn = self._conn()
        hr.event(conn, state["application_id"], "drafted", {"purpose": "not_moving_forward",
                                                            "email_id": out["email"]["id"]})
        return {"draft_id": out["email"]["id"], "purpose": "not_moving_forward", "error": None}

    def draft_confirmation(self, state: State) -> dict:
        conn, app, job = self._ctx(state)
        r = self._round_stage(conn, job["id"], state.get("round", 0))
        tz = sch.zone(state["chosen"].get("timezone"))
        start = datetime.fromisoformat(state["chosen"]["start"]).astimezone(tz)
        end = datetime.fromisoformat(state["chosen"]["end"]).astimezone(tz)
        iv = hr.create_interview(conn, app, r["name"] if r else "Interview", start, end)
        email = hr.draft_confirmation(conn, app, job, iv, hr.token_for(app["id"]))
        hr.event(conn, app["id"], "slot_confirmed", {"label": hr.interview_label(iv), "interview_id": iv["id"]},
                 "recruiter")
        hr.event(conn, app["id"], "drafted", {"purpose": "confirmation", "email_id": email["id"]})
        return {"draft_id": email["id"], "purpose": "confirmation", "interview_id": iv["id"], "error": None}

    # --- ⏸ approve, then send ---------------------------------------------------------------------------------
    def approve_email(self, state: State) -> Command:
        purpose = state["purpose"]
        phase = {"offer": "offer", "not_moving_forward": None}.get(purpose, "scheduling")
        payload = {"email_id": state["draft_id"], "purpose": purpose, "error": state.get("error"),
                   "interview_id": state.get("interview_id") if purpose == "confirmation" else None}
        title = "No reply – send a nudge?" if purpose == "nudge" else f"Approve {hr.PURPOSE[purpose]}"
        ans = self._ask(state, "approve_email", "needs_action", title, payload, phase=phase)
        conn = self._conn()
        if ans.get("action") == "send":
            return Command(goto="send", update={"approved": state["draft_id"]})
        wf.cancel_email(conn, state["draft_id"])                      # leaving the step: the draft is not sent
        generic = self._generic(state, ans)
        if generic:
            return generic
        hr.event(conn, state["application_id"], "email_discarded", {"purpose": purpose}, "recruiter")
        if purpose == "scheduling":
            return Command(goto="choose", update={"options": ["invite", "reject", "close"],
                                                  "choose_title": "Invite discarded – choose the next step"})
        return Command(goto=DISCARD_TO[purpose])

    def send(self, state: State) -> Command:
        conn, app, job = self._ctx(state)
        email_id, purpose = state["approved"], state["purpose"]
        assert email_id == state["draft_id"], "only the approved draft is sent"
        try:
            sent = self.rejection.decide(email_id, "send") if purpose == "not_moving_forward" \
                else wf.send_email(conn, email_id)
        except ValueError as exc:
            sent = {"status": "failed", "error": str(exc)}
        if sent["status"] != "sent":
            hr.event(conn, app["id"], "send_failed", {"error": sent.get("error")})
            return Command(goto="approve_email", update={"approved": None, "error": sent.get("error")})
        hr.event(conn, app["id"], "email_sent", {"purpose": purpose, "email_id": email_id, "subject": sent["subject"],
                                                 "to": sent["actual_to"]}, "recruiter")
        update = {"approved": None, "draft_id": None, "error": None}
        r = self._round_stage(conn, job["id"], state.get("round", 0))
        if purpose == "scheduling":
            self._move(conn, app, "Scheduling", "scheduling email sent")
            return Command(goto="check_inbox", update=update)
        if purpose in ("nudge", "answer"):
            return Command(goto="check_inbox", update=update)
        if purpose == "confirmation":
            hr.set_interview(conn, state["interview_id"], "confirmed")
            if r:
                self._move(conn, app, r["name"], "interview confirmed")
            return Command(goto="scheduled", update=update)
        if purpose == "offer":
            self._move(conn, app, "Offer", "offer note sent")
            return Command(goto="choose", update=update | {"options": ["hired", "declined"],
                                                           "choose_title": "Offer outcome"})
        return Command(goto="close", update=update | {"outcome": "not_moving_forward"})

    # --- replies ------------------------------------------------------------------------------------------------
    def check_inbox(self, state: State) -> Command:
        conn = self._conn()
        new = hr.unread(conn, state["application_id"])
        if new:
            return Command(goto="read_reply", update={"reply_id": new[0]["id"]})
        return Command(goto="await_reply")

    def await_reply(self, state: State) -> Command:
        conn = self._conn()
        last = hr.last_out(conn, state["application_id"])
        due = datetime.fromisoformat(last["sent_at"]) + timedelta(days=config.NUDGE_DAYS) if last else None
        ans = self._ask(state, "await_reply", "waiting_candidate", "Waiting for the candidate's reply",
                        {"last_message_id": last["id"] if last else None}, phase="scheduling", due=due)
        generic = self._generic(state, ans)
        if generic:
            return generic
        if ans["action"] == "reply":
            return Command(goto="read_reply", update={"reply_id": ans["message_id"]})
        if ans["action"] == "nudge":
            return Command(goto="draft_nudge")
        return Command(goto="propose_slots", update={"reading": None})

    def read_reply(self, state: State) -> Command:
        conn, app, job = self._ctx(state)
        msg = hr.get_message(conn, state["reply_id"])
        s = wf.settings(conn, job["id"])
        windows = sch.parse_availability(s["availability"])
        tz = sch.default_zone(windows)
        last = hr.last_out(conn, app["id"])
        reading = replies.read_reply(self.writer, msg["body"], sent_context=(last or {}).get("body", ""),
                                     default_tz=tz)
        hr.save_reading(conn, msg["id"], reading)
        hr.event(conn, app["id"], "reply_read", {"label": reading["label"], "method": reading["method"],
                                                 "message_id": msg["id"]})
        update = {"reading": reading}
        if reading["label"] in REPLY_ROUTES:
            return Command(goto=REPLY_ROUTES[reading["label"]], update=update)
        return Command(goto="choose", update=update | {"options": ["slots", "answer", "withdrawal", "wait"],
                                                       "choose_title": "Reply received – choose the next step"})

    # --- slots --------------------------------------------------------------------------------------------------
    def propose_slots(self, state: State) -> dict:
        conn, app, job = self._ctx(state)
        s = wf.settings(conn, job["id"])
        reading = state.get("reading") or {}
        preferred = [datetime.fromisoformat(t["start"]).astimezone(sch.zone(t["timezone"]))
                     for t in reading.get("times", [])] \
            if reading.get("label") in ("slot_chosen", "times_proposed") else []
        after = sch.next_monday() if reading.get("next_week") else None
        slots = sch.propose(sch.parse_availability(s["availability"]), s["duration_minutes"],
                            hr.booked(conn, job["id"], exclude_app=app["id"]), preferred=preferred, after=after)
        hr.event(conn, app["id"], "slots_proposed", {"n": sum(1 for x in slots if x.fits)})
        return {"slots": [x.as_dict() for x in slots]}

    def confirm_slot(self, state: State) -> Command:
        reading = state.get("reading")
        title = "Reply received – pick a slot" if reading else "Pick an interview slot"
        ans = self._ask(state, "confirm_slot", "needs_action", title,
                        {"slots": state.get("slots", []), "reply_id": state.get("reply_id") if reading else None},
                        phase="scheduling")
        generic = self._generic(state, ans)
        if generic:
            return generic
        conn, app, job = self._ctx(state)
        s = wf.settings(conn, job["id"])
        tz = sch.zone(sch.default_zone(sch.parse_availability(s["availability"])))
        start = datetime.fromisoformat(ans["start"]).astimezone(tz)
        end = datetime.fromisoformat(ans["end"]).astimezone(tz) if ans.get("end") else \
            start + timedelta(minutes=s["duration_minutes"])
        return Command(goto="draft_confirmation", update={"chosen": {"start": start.isoformat(),
                                                                     "end": end.isoformat(), "timezone": tz.key}})

    # --- interview and feedback ---------------------------------------------------------------------------------
    def scheduled(self, state: State) -> Command:
        conn = self._conn()
        iv = hr.get_interview(conn, state.get("interview_id"))
        due = datetime.fromisoformat(iv["end"]) if iv else None
        title = f"Interview {hr.interview_label(iv)}" if iv else "Interview arranged outside RightStaff"
        ans = self._ask(state, "scheduled", "scheduled", title, {"interview_id": iv["id"] if iv else None},
                        phase="scheduled", due=due)
        generic = self._generic(state, ans)
        if generic:
            return generic
        if ans["action"] == "reschedule":
            if iv:
                hr.set_interview(conn, iv["id"], "cancelled")
            return Command(goto="propose_slots", update={"interview_id": None})
        if iv:
            hr.set_interview(conn, iv["id"], "done")
        return Command(goto="request_feedback")

    def request_feedback(self, state: State) -> dict:
        conn = self._conn()
        iv = hr.get_interview(conn, state.get("interview_id"))
        hr.event(conn, state["application_id"], "interview_done", {"interview_id": iv["id"] if iv else None})
        row = hr.get(conn, state["application_id"])
        if hr.phase_key(row["round"], row["phase"]) != hr.phase_key(state.get("round", 0), "interview"):
            hr.event(conn, state["application_id"], "phase", {"key": hr.phase_key(state.get("round", 0), "interview")},
                     at=iv["start"] if iv else None)
        return {}

    def enter_feedback(self, state: State) -> Command:
        conn, app, job = self._ctx(state)
        r = self._round_stage(conn, job["id"], state.get("round", 0))
        ans = self._ask(state, "enter_feedback", "needs_action", "Feedback due",
                        {"interview_id": state.get("interview_id"), "stage": r["name"] if r else None,
                         "focus": r["focus"] if r else []}, phase="feedback")
        return self._generic(state, ans) or Command(goto="summarize_feedback")

    def summarize_feedback(self, state: State) -> dict:
        conn, app, job = self._ctx(state)
        r = self._round_stage(conn, job["id"], state.get("round", 0))
        stage = r["name"] if r else "Interview"
        summary = hr.summarize(self.writer, stage, r["focus"] if r else [], hr.scorecards(conn, app["id"], stage))
        hr.event(conn, app["id"], "feedback_summarized", {"method": summary["method"], "stage": stage})
        return {"summary": summary}

    def decide(self, state: State) -> Command:
        conn, app, job = self._ctx(state)
        r = self._round_stage(conn, job["id"], state.get("round", 0))
        rs = hr.rounds(conn, job["id"])
        nxt = rs[state.get("round", 0) + 1]["name"] if state.get("round", 0) + 1 < len(rs) else "Offer"
        ans = self._ask(state, "decide", "needs_action", "Decide the next step",
                        {"summary": state.get("summary"), "stage": r["name"] if r else None, "next": nxt},
                        phase="decision")
        generic = self._generic(state, ans)
        if generic:
            return generic
        hr.event(conn, app["id"], "decision", {"decision": {"advance": f"advance to {nxt}",
                                                            "reject": "not moving forward"}[ans["action"]]},
                 "recruiter")
        if ans["action"] == "reject":
            return Command(goto="draft_rejection")
        if nxt == "Offer":
            return Command(goto="draft_offer")
        return Command(goto="draft_invite", update={"round": state.get("round", 0) + 1, "reading": None,
                                                    "interview_id": None, "summary": None})

    # --- other ⏸ steps ------------------------------------------------------------------------------------------
    def confirm_withdrawal(self, state: State) -> Command:
        ans = self._ask(state, "confirm_withdrawal", "needs_action", "Reply received – confirm withdrawal",
                        {"reply_id": state.get("reply_id")})
        generic = self._generic(state, ans)
        if generic:
            return generic
        if ans["action"] == "confirm":
            hr.event(self._conn(), state["application_id"], "withdrawal_confirmed", {}, "recruiter")
            return Command(goto="close", update={"outcome": "withdrew"})
        return Command(goto="choose", update={"options": ["slots", "answer", "wait"],
                                              "choose_title": "Not a withdrawal – choose the next step"})

    def choose(self, state: State) -> Command:
        options = state.get("options") or ["invite", "close"]
        ans = self._ask(state, "choose", "needs_action", state.get("choose_title") or "Choose the next step",
                        {"options": [{"key": o, "label": OPTIONS[o][1]} for o in options],
                         "reply_id": state.get("reply_id") if state.get("reading") else None,
                         "phase": hr.get(self._conn(), state["application_id"])["phase"]})
        hr.event(self._conn(), state["application_id"], "chosen",
                 {"option": OPTIONS.get(ans.get("option"), ("", ans.get("action")))[1]}, "recruiter")
        return self._generic(state, ans) or Command(goto="choose")

    def stage_moved(self, state: State) -> Command:
        """A board move: record it, then ask whether an email should follow ("Send stage email?")."""
        conn, app, job = self._ctx(state)
        stage = next(s for s in wf.stages(conn, job["id"]) if s["id"] == state["jump"])
        row = hr.get(conn, app["id"])
        rs = hr.rounds(conn, job["id"])
        hr.event(conn, app["id"], "stage_moved", {"stage": stage["name"]}, "recruiter")
        update: dict = {"jump": None, "draft_id": None, "reading": None}
        if stage["terminal"]:
            return Command(goto="choose", update=update | {"options": ["reject", "close"],
                                                           "choose_title": "Send stage email? (not moving forward)"})
        if stage["name"] == "Hired":
            return Command(goto="mark_hired", update=update)
        if stage["name"] == "Offer":
            hr.set_step(conn, app["id"], step="choose", status="needs_action", title="Send stage email?", phase="offer")
            return Command(goto="choose", update=update | {"options": ["offer", "hired"],
                                                           "choose_title": "Send stage email? (offer note)"})
        idx = next((i for i, r in enumerate(rs) if r["id"] == stage["id"]), None)
        if idx is None:                       # Shortlisted or Scheduling: the current interview round
            round_ = 0 if stage["name"] == "Shortlisted" else (row["round"] if row else 0)
            phase = "shortlisted" if stage["name"] == "Shortlisted" else "scheduling"
            hr.set_step(conn, app["id"], step="choose", status="needs_action", title="Send stage email?",
                        phase=phase, round_=round_)
            return Command(goto="choose", update=update | {"round": round_, "options": ["invite", "close"],
                                                           "choose_title": "Send stage email?"})
        before = hr.phase_key(row["round"], row["phase"]) if row else "shortlisted"
        skipped = [f"{idx}:{p}" for p in ("scheduling", "scheduled")
                   if row is None or (row["round"] or 0) < idx or before in ("shortlisted", f"{idx}:scheduling")]
        if skipped:
            hr.event(conn, app["id"], "skipped", {"keys": skipped, "note": f"{stage['name']} scheduling skipped "
                                                                            f"(moved on the board)"}, "recruiter")
        hr.set_step(conn, app["id"], step="choose", status="needs_action", title="Send stage email?",
                    phase="interview", round_=idx)
        return Command(goto="choose", update=update | {"round": idx, "options": ["invite", "external", "feedback"],
                                                       "choose_title": f"Send stage email? ({stage['name']})"})

    def mark_hired(self, state: State) -> Command:
        conn, app, _ = self._ctx(state)
        self._move(conn, app, "Hired", "marked as hired")
        return Command(goto="close", update={"outcome": "hired"})

    def close(self, state: State) -> dict:
        conn, app, job = self._ctx(state)
        outcome = state.get("outcome") or "closed"
        terminal = next(s for s in wf.stages(conn, job["id"]) if s["terminal"])
        if outcome == "closed" and app.get("stage_id") == terminal["id"]:
            outcome = "not_moving_forward"                  # closed from the board's Not moving forward column
        if outcome in ("withdrew", "declined", "not_moving_forward"):
            reason = {"withdrew": "candidate withdrew", "declined": "candidate declined the offer"}.get(outcome)
            if app.get("stage_id") != terminal["id"]:
                wf.move(conn, app, terminal["id"], reason=reason or "closed without an email")
        hr.close(conn, app["id"], outcome)
        return {}

    def _build(self) -> StateGraph:
        g = StateGraph(State)
        anywhere = (*NODES, END)
        for name in NODES:
            g.add_node(name, getattr(self, name), destinations=anywhere)
        g.add_conditional_edges(START, lambda s: "stage_moved" if s.get("jump") else "draft_invite",
                                ["stage_moved", "draft_invite"])
        for a, b in [("draft_nudge", "approve_email"), ("draft_offer", "approve_email"),
                     ("draft_answer", "approve_email"), ("draft_rejection", "approve_email"),
                     ("draft_confirmation", "approve_email"), ("propose_slots", "confirm_slot"),
                     ("request_feedback", "enter_feedback"), ("summarize_feedback", "decide"), ("close", END)]:
            g.add_edge(a, b)
        return g

    # --- API ------------------------------------------------------------------------------------------------------
    def _lock(self, app_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(app_id, threading.Lock())

    def _cfg(self, row: dict) -> dict:
        return {"configurable": {"thread_id": row["thread_id"]}}

    def start(self, app_id: str, *, jump: str | None = None) -> dict:
        """Start (or return) the application's workflow. A closed one starts again on a new thread."""
        with self._lock(app_id):
            conn = self._conn()
            row = hr.get(conn, app_id)
            if row and row["status"] != "closed" and self.graph.get_state(self._cfg(row)).interrupts:
                return row
            app = hr.app_row(conn, app_id)
            n = conn.execute("SELECT COUNT(*) FROM workflow_events WHERE application_id = ? AND type = 'started'",
                             (app_id,)).fetchone()[0]
            row = hr.create(conn, app, f"hire:{app_id}:{n + 1}")
            self.graph.invoke({"application_id": app_id, "job_id": app["job_id"], "round": 0, "jump": jump},
                              self._cfg(row))
            return hr.get(conn, app_id)

    def act(self, app_id: str, answer: dict, actor: str = "recruiter") -> dict:
        """Resume the waiting step with the recruiter's (or the scheduler's) answer, after checking it fits."""
        with self._lock(app_id):
            conn = self._conn()
            row = hr.get(conn, app_id)
            if row is None or row["status"] == "closed":
                raise ValueError("This application has no open workflow.")
            if row["status"] == "paused" and answer.get("action") != "jump":
                raise ValueError("The workflow is paused. Resume it first.")
            self._check(conn, row, answer)
            self.graph.invoke(Command(resume=answer), self._cfg(row))
            return hr.get(conn, app_id)

    def _check(self, conn, row: dict, a: dict) -> None:
        action, step, p = a.get("action"), row["step"], row["payload"]
        if action == "choose":
            if a.get("option") not in OPTIONS:
                raise ValueError(f"Unknown next step {a.get('option')!r}.")
            if step == "choose" and a["option"] not in {o["key"] for o in p.get("options", [])} \
                    and a["option"] not in ("close", "reject"):
                raise ValueError("Pick one of the offered next steps.")
            return
        if action == "jump":
            if not any(s["id"] == a.get("stage_id") for s in wf.stages(conn, row["job_id"])):
                raise ValueError("That stage belongs to another job.")
            return
        if action not in ALLOWED.get(step, set()):
            raise ValueError(f"{action!r} is not possible at this step ({row['title'] or step}).")
        if action == "send":
            e = wf.get_email(conn, a.get("email_id") or "")
            if a.get("email_id") != p.get("email_id") or e is None or e["status"] != "draft":
                raise ValueError("Only the draft waiting for approval can be sent (it may have been sent already).")
        if action == "reply":
            m = hr.get_message(conn, a.get("message_id") or "")
            if m is None or m["application_id"] != row["application_id"] or m["direction"] != "in":
                raise ValueError("Unknown reply.")
        if action == "confirm" and step == "confirm_slot":
            try:
                start = datetime.fromisoformat(a["start"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("Pick a slot or enter a start time (ISO 8601 with a time zone).") from None
            if start.tzinfo is None:
                raise ValueError("The start time needs a time zone.")
        if action == "summarize":
            stage = p.get("stage")
            if not hr.scorecards(conn, row["application_id"], stage):
                raise ValueError("Add at least one scorecard first.")

    def reply(self, app_id: str, text: str, source: str = "pasted", **headers) -> dict:
        """A candidate reply (pasted or from the inbox). Read now if the workflow waits for one; otherwise kept
        unread until it does (Tasks shows "Reply received")."""
        conn = self._conn()
        row = hr.get(conn, app_id)
        if row is None or row["status"] == "closed":
            raise ValueError("This application has no open workflow.")
        msg = hr.record_inbound(conn, app_id, text, source=source, **headers)
        if row["step"] == "await_reply" and row["status"] != "paused":
            return self.act(app_id, {"action": "reply", "message_id": msg["id"]}, actor="candidate")
        return hr.get(conn, app_id)

    def board_move(self, app_id: str, stage_id: str) -> dict:
        """The card was moved on the board (the move itself is already saved). Never sends anything."""
        conn = self._conn()
        row = hr.get(conn, app_id)
        if row is None or row["status"] == "closed" or not self.graph.get_state(self._cfg(row)).interrupts:
            return self.start(app_id, jump=stage_id)
        return self.act(app_id, {"action": "jump", "stage_id": stage_id})

    def tick(self, at: datetime | None = None) -> list[str]:
        """Scheduled checks (no email is sent): replies waiting to be read, nudges due, interviews that ended."""
        at = at or sch.now()
        conn = self._conn()
        touched = []
        for (app_id,) in conn.execute("SELECT application_id FROM workflows WHERE status IN "
                                      "('waiting_candidate', 'scheduled')").fetchall():
            row = hr.get(conn, app_id)
            try:
                new = hr.unread(conn, app_id)
                if row["step"] == "await_reply" and new:
                    self.act(app_id, {"action": "reply", "message_id": new[0]["id"]}, actor="candidate")
                elif row["next_due"] and datetime.fromisoformat(row["next_due"]) <= at:
                    if row["step"] == "await_reply":
                        self.act(app_id, {"action": "nudge"}, actor="agent")
                    elif row["step"] == "scheduled" and row["payload"].get("interview_id"):
                        self.act(app_id, {"action": "done"}, actor="agent")
                    else:
                        continue
                else:
                    continue
                touched.append(app_id)
            except ValueError:
                continue
        return touched

    def poll_inbox(self, inbox: replies.ImapInbox | None = None) -> dict:
        """REPLY_INBOX=imap: read new replies from the demo inbox (read-only) and hand them to their workflow.
        Every decision is logged (logger "rightstaff.inbox") and the last result is kept for GET /workflows/inbox."""
        import logging
        from core.db import now_iso
        log = logging.getLogger("rightstaff.inbox")
        conn = self._conn()
        inbox = inbox or replies.ImapInbox()
        out = {"at": now_iso(), "fetched": 0, "matched": [], "skipped": [], "error": None}
        try:
            raws = inbox.fetch(wanted=lambda h: replies.header_wanted(conn, h))
        except Exception as exc:                            # noqa: BLE001 - shown on Tasks and in the log
            out["error"] = f"{type(exc).__name__}: {exc}"
            log.warning("inbox poll failed: %s", out["error"])
            self.last_poll = out
            return out
        out["fetched"] = len(raws)
        for raw in raws:
            m = replies.parse_message(raw)
            if replies.our_copy(conn, m):
                why = "already stored" if replies.known(conn, m.message_id) else "our own sent copy"
                out["skipped"].append({"subject": m.subject, "why": why})
                continue
            app_id = replies.match(conn, m)
            if app_id is None:
                out["skipped"].append({"subject": m.subject, "why": "no workflow with this token"})
            elif not m.body:
                out["skipped"].append({"subject": m.subject, "why": "empty after removing the quoted text"})
            else:
                try:
                    self.reply(app_id, m.body, source="imap", subject=m.subject, message_id=m.message_id,
                               in_reply_to=m.in_reply_to, from_addr=m.from_addr)
                    out["matched"].append({"subject": m.subject, "application_id": app_id})
                except ValueError as exc:
                    out["skipped"].append({"subject": m.subject, "why": str(exc)})
        for x in out["matched"]:
            log.warning("inbox: matched reply %r → application %s", x["subject"], x["application_id"])
        for x in out["skipped"]:
            log.warning("inbox: skipped %r (%s)", x["subject"], x["why"])
        log.warning("inbox poll: %d fetched, %d matched, %d skipped", out["fetched"], len(out["matched"]),
                    len(out["skipped"]))
        self.last_poll = out
        return out
