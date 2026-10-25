"""Hiring workflow (specs/16): one per application; every email and decision waits for the recruiter."""
from __future__ import annotations

from fastapi import HTTPException

from api.context import Api
from api.schemas import BulkStart, ReplyIn, ScorecardIn, WorkflowAction, WorkflowStart
from core import config
from services.tasks import clean_error
from workflows import engine as wf
from workflows import hiring as hr
from workflows import replies as rp


def register(api: Api) -> None:
    app, conn, state = api.app, api.conn, api.state
    app_or_404, job_or_404 = api.app_or_404, api.job_or_404
    wf_call = api.call

    # --- Hiring workflow (specs/16): one per application; every email and decision waits for the recruiter ----
    def start_one(c, app_id: str, confirm_move: bool) -> dict:
        a = app_or_404(c, app_id)
        open_ = hr.get(c, app_id)
        if open_ and open_["status"] != "closed":
            return open_
        if a["status"] != "shortlisted":
            if not confirm_move:
                raise HTTPException(409, "Move to Shortlisted and start?")
            wf.set_status(c, a, "shortlisted")
        return wf_call(state.hiring.start, app_id)

    @app.post("/applications/{app_id}/workflow", tags=["hiring"])
    def start_workflow(app_id: str, body: WorkflowStart | None = None):
        """Start the interview loop for one candidate (any status: confirm the move to Shortlisted first)."""
        with conn() as c:
            start_one(c, app_id, bool(body and body.confirm_move))
            return hr.view(c, app_id)

    @app.post("/jobs/{job_id}/workflows", tags=["hiring"])
    def start_workflows(job_id: str, body: BulkStart):
        """Bulk "Start interview loop". 409 lists the candidates who are not shortlisted unless confirmed."""
        with conn() as c:
            job_or_404(c, job_id)
            apps = [app_or_404(c, a) for a in body.application_ids]
            if not apps:
                raise HTTPException(422, "Select at least one candidate.")
            if any(a["job_id"] != job_id for a in apps):
                raise HTTPException(422, "Those candidates are not all in this job.")
            def has_open(a):
                return (hr.get(c, a["id"]) or {}).get("status") not in (None, "closed")
            to_move = [a for a in apps if a["status"] != "shortlisted" and not has_open(a)]
            if body.check_only:
                return {"needs_move": [hr.candidate_name(c, a) or "Applicant" for a in to_move]}
            if to_move and not body.confirm_move:
                names = [hr.candidate_name(c, a) or "Applicant" for a in to_move]
                raise HTTPException(409, f"Move to Shortlisted and start? ({', '.join(names)})")
            started = [start_one(c, a["id"], True)["application_id"] for a in apps]
            return {"started": started}

    @app.get("/applications/{app_id}/workflow", tags=["hiring"])
    def workflow_view(app_id: str):
        with conn() as c:
            app_or_404(c, app_id)
            return hr.view(c, app_id)

    @app.post("/applications/{app_id}/workflow/action", tags=["hiring"])
    def workflow_action(app_id: str, body: WorkflowAction):
        """The recruiter's answer at the current step. "hold" pauses the decision."""
        with conn() as c:
            app_or_404(c, app_id)
            if body.action in ("pause", "hold"):
                wf_call(hr.pause, c, app_id, "recruiter", "on_hold" if body.action == "hold" else "paused")
            elif body.action == "resume":
                wf_call(hr.unpause, c, app_id)
            else:
                wf_call(state.hiring.act, app_id, body.model_dump(exclude_none=True))
            return hr.view(c, app_id)

    @app.post("/applications/{app_id}/workflow/reply", tags=["hiring"])
    def workflow_reply(app_id: str, body: ReplyIn):
        """Simulate / paste the candidate's reply (or pick a canned one). The agent reads it; nothing is sent."""
        text = rp.CANNED.get(body.canned or "") or body.text
        if not text or not text.strip():
            raise HTTPException(422, "Paste the reply or pick a canned one.")
        with conn() as c:
            app_or_404(c, app_id)
            wf_call(state.hiring.reply, app_id, text, "canned" if body.canned else "pasted")
            return hr.view(c, app_id)

    @app.post("/applications/{app_id}/workflow/scorecards", tags=["hiring"])
    def add_scorecard(app_id: str, body: ScorecardIn):
        with conn() as c:
            app_or_404(c, app_id)
            row = hr.get(c, app_id)
            if row is None or row["step"] != "enter_feedback":
                raise HTTPException(422, "Feedback is entered when the workflow asks for it.")
            wf_call(hr.add_scorecard, c, app_id, interview_id=row["payload"].get("interview_id"),
                    stage=row["payload"].get("stage") or "Interview", interviewer=body.interviewer,
                    ratings=[r.model_dump() for r in body.ratings], notes=body.notes,
                    recommendation=body.recommendation)
            return hr.view(c, app_id)

    @app.get("/workflows/inbox", tags=["hiring"])
    def inbox_status():
        """Whether replies are read from the demo inbox, and when it was last checked. Never the address itself."""
        last = getattr(state._hiring, "last_poll", None) if state._hiring else None
        return {"enabled": config.REPLY_INBOX == "imap",
                "last_check": {"at": last["at"], "failed": bool(last.get("error"))} if last else None}

    @app.get("/recruiter/tasks", tags=["hiring"])
    def recruiter_tasks():
        """Everything waiting for the recruiter across jobs, oldest first, including applications waiting longer than
        CLOSURE_DAYS for an answer (specs/17 §7: a reminder only; nothing is sent)."""
        from services import my_applications as ma
        with conn() as c:
            return sorted(hr.tasks(c) + ma.closure_tasks(c), key=lambda t: t["since"] or "")

    @app.post("/workflows/tick", tags=["hiring"])
    def workflows_tick():
        """Run the scheduled checks now (also runs every WORKFLOW_TICK_SECONDS): replies, nudges, interviews."""
        out = {"touched": state.hiring.tick()}
        if config.REPLY_INBOX == "imap":
            try:
                out["inbox"] = state.hiring.poll_inbox()
            except Exception as exc:                    # noqa: BLE001 - shown, never fatal
                out["inbox_error"] = clean_error(exc)
        return out

