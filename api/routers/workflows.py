"""Interview plan, pipeline, activity and the Not moving forward email (specs/13)."""
from __future__ import annotations

from api.context import Api
from api.schemas import JobSettingsIn, RejectIn, StageEdit, StageIn, StageMove, StageOrder
from core import config
from workflows import engine as wf


def register(api: Api) -> None:
    app, conn, state = api.app, api.conn, api.state
    app_or_404, job_or_404 = api.app_or_404, api.job_or_404
    wf_call = api.call

    # --- Interview plan, pipeline and activity (specs/13 §3) ------------------------------------------
    @app.get("/jobs/{job_id}/settings", tags=["workflows"])
    def job_settings(job_id: str):
        with conn() as c:
            job_or_404(c, job_id)
            return {"settings": wf.settings(c, job_id), "stages": wf.stages(c, job_id)}

    @app.put("/jobs/{job_id}/settings", tags=["workflows"])
    def put_job_settings(job_id: str, body: JobSettingsIn):
        with conn() as c:
            job_or_404(c, job_id)
            return {"settings": wf_call(wf.update_settings, c, job_id, body.model_dump()), "stages": wf.stages(c, job_id)}

    @app.post("/jobs/{job_id}/stages", tags=["workflows"])
    def add_stage(job_id: str, body: StageIn):
        with conn() as c:
            job_or_404(c, job_id)
            return wf_call(wf.add_stage, c, job_id, body.name, body.focus)

    @app.patch("/jobs/{job_id}/stages/{stage_id}", tags=["workflows"])
    def edit_stage(job_id: str, stage_id: str, body: StageEdit):
        with conn() as c:
            job_or_404(c, job_id)
            return wf_call(wf.update_stage, c, job_id, stage_id, body.name, body.focus)

    @app.put("/jobs/{job_id}/stages/order", tags=["workflows"])
    def order_stages(job_id: str, body: StageOrder):
        with conn() as c:
            job_or_404(c, job_id)
            return wf_call(wf.reorder_stages, c, job_id, body.ids)

    @app.delete("/jobs/{job_id}/stages/{stage_id}", tags=["workflows"])
    def remove_stage(job_id: str, stage_id: str):
        with conn() as c:
            job_or_404(c, job_id)
            return wf_call(wf.delete_stage, c, job_id, stage_id)

    @app.get("/jobs/{job_id}/pipeline", tags=["workflows"])
    def job_pipeline(job_id: str):
        """Board columns; each card carries its workflow status (Needs your action, Scheduled …)."""
        from workflows import hiring
        with conn() as c:
            board = wf.pipeline(c, job_or_404(c, job_id))
            for col in board["stages"]:
                for card in col["cards"]:
                    card["workflow"] = hiring.card_status(c, hiring.get(c, card["application_id"]))
            return board

    @app.patch("/applications/{app_id}/stage", tags=["workflows"])
    def move_stage(app_id: str, body: StageMove):
        """Move a card between stages; the move is logged in Activity and updates the hiring workflow
        (specs/16 §5). Nothing is sent: if an email would follow, the workflow asks "Send stage email?"."""
        with conn() as c:
            before = app_or_404(c, app_id)
            a = wf_call(wf.move, c, before, body.stage_id)
        if body.stage_id and body.stage_id != before.get("stage_id"):
            wf_call(state.hiring.board_move, app_id, body.stage_id)
        return {"application_id": app_id, "status": a["status"], "stage_id": a["stage_id"]}

    @app.get("/applications/{app_id}/activity", tags=["workflows"])
    def application_activity(app_id: str):
        with conn() as c:
            app_or_404(c, app_id)
            return wf.activity(c, app_id)

    # --- Emails (specs/13 §2–§4): drafts only; nothing is sent without the Send action ----------------------
    @app.get("/email/config", tags=["workflows"])
    def email_config():
        return {"provider": config.EMAIL_PROVIDER, "demo_redirect": config.DEMO_EMAIL_TO or None,
                "model": config.EMAIL_MODEL if state.writer is not None else None}

    @app.post("/applications/{app_id}/not-moving-forward", tags=["workflows"])
    def reject_draft(app_id: str, body: RejectIn | None = None):
        """Reasons (code) → draft (model) → checks (code) → paused for the recruiter's review. Sends nothing."""
        with conn() as c:
            app_or_404(c, app_id)
        return state.rejection.draft(app_id, body.reason_ids if body else None, body.note if body else None)

