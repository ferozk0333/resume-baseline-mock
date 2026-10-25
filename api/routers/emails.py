"""Emails: stage emails, the Outbox, edit, Send and cancel. Nothing is sent without the Send action."""
from __future__ import annotations

from fastapi import HTTPException

from api.context import Api
from api.schemas import EmailEdit, StageEmailIn
from workflows import engine as wf
from workflows import hiring as hr


def register(api: Api) -> None:
    app, conn, not_found, state = api.app, api.conn, api.not_found, api.state
    app_or_404, job_or_404 = api.app_or_404, api.job_or_404
    wf_call = api.call

    @app.post("/applications/{app_id}/stage-email", tags=["workflows"])
    def stage_email(app_id: str, body: StageEmailIn):
        with conn() as c:
            a = app_or_404(c, app_id)
            job = job_or_404(c, a["job_id"])
            stage = next((s for s in wf.stages(c, job["id"]) if s["id"] == body.stage_id and not s["terminal"]), None)
            if stage is None:
                raise HTTPException(422, "Pick an interview stage of this job.")
            return wf.draft_scheduling(c, a, job, stage, state.writer)

    @app.get("/emails", tags=["workflows"])
    def emails(status: str | None = None, job_id: str | None = None, application_id: str | None = None,
               kind: str | None = None):
        with conn() as c:
            return wf.list_emails(c, status=status, job_id=job_id, application_id=application_id, kind=kind)

    @app.get("/emails/{email_id}", tags=["workflows"])
    def email(email_id: str):
        with conn() as c:
            return wf.get_email(c, email_id) or not_found("Email")

    @app.put("/emails/{email_id}", tags=["workflows"])
    def edit_email(email_id: str, body: EmailEdit):
        """Save draft: the recruiter's edits (checked again for scores, ranking, other candidates)."""
        with conn() as c:
            return wf_call(wf.edit_email, c, email_id, body.subject, body.body)

    @app.post("/emails/{email_id}/send", tags=["workflows"])
    def send(email_id: str):
        """The recruiter's Send action: the only way an email is delivered."""
        with conn() as c:
            e = wf.get_email(c, email_id) or not_found("Email")
            row = hr.get(c, e["application_id"])
            if row and row["step"] == "approve_email" and row["payload"].get("email_id") == email_id:
                wf_call(state.hiring.act, e["application_id"], {"action": "send", "email_id": email_id})
                return wf.get_email(c, email_id)
            if e["kind"] == "not_moving_forward":
                return wf_call(state.rejection.decide, email_id, "send")
            return wf_call(wf.send_scheduling, c, email_id)

    @app.post("/emails/{email_id}/cancel", tags=["workflows"])
    def cancel(email_id: str):
        with conn() as c:
            e = wf.get_email(c, email_id) or not_found("Email")
            row = hr.get(c, e["application_id"])
            if row and row["step"] == "approve_email" and row["payload"].get("email_id") == email_id:
                wf_call(state.hiring.act, e["application_id"], {"action": "discard"})
                return wf.get_email(c, email_id)
            if e["kind"] == "not_moving_forward" and e["status"] == "draft":
                return wf_call(state.rejection.decide, email_id, "cancel")
            return wf.cancel_email(c, email_id)

