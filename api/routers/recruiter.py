"""Recruiter: a job, its weight assistant (specs/21), applicants and the applicant panel, compare."""
from __future__ import annotations

from fastapi import HTTPException

from api.context import Api
from api.schemas import JobUpdate, StatusIn, WeightSuggestIn
from core import db
from services import recruiter as rc
from workflows import engine as wf


def register(api: Api) -> None:
    app, conn, not_found, state = api.app, api.conn, api.not_found, api.state
    app_or_404, job_or_404 = api.app_or_404, api.job_or_404

    @app.get("/jobs/{job_id}", tags=["recruiter"])
    def job(job_id: str):
        with conn() as c:
            j = job_or_404(c, job_id)
            return {**j, "baseline": db.get_baseline(c, j["baseline_id"]), "presets": list(rc.PRESETS),
                    "presets_note": "All presets equal the team table until the team sets values."}

    @app.put("/jobs/{job_id}", tags=["recruiter"])
    def update_job(job_id: str, body: JobUpdate):
        with conn() as c:
            job_or_404(c, job_id)
            try:
                return rc.update_job(c, job_id, weights=body.weights, must_haves=body.must_haves, preset=body.preset)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    # --- Weight assistant (specs/21): suggest → preview → Apply / Discard → Undo. Nothing changes until Apply. ---

    def suggestion_or_404(c, job_id: str, sid: str) -> dict:
        from services import weight_assistant as wa
        return wa.get(c, job_id, sid) or not_found("Suggestion")

    @app.post("/jobs/{job_id}/weight-suggestions", tags=["recruiter"])
    def suggest_weights(job_id: str, body: WeightSuggestIn):
        """One model call (or the cache): weights only, from the recruiter's text and the job's title, level and
        weights. No applicant data is sent. The preview re-aggregates saved scores; nothing is applied."""
        from services import weight_assistant as wa
        with conn() as c:
            j = job_or_404(c, job_id)
            try:
                out = wa.suggest(c, state.deps.weights_llm, j, body.text, fresh=body.fresh)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if out["status"] != "ok":
                return out
            return {"status": "ok", "suggestion": wa.view(c, j, wa.get(c, job_id, out["id"]))}

    @app.get("/jobs/{job_id}/weight-suggestions", tags=["recruiter"])
    def weight_suggestion_history(job_id: str):
        from services import weight_assistant as wa
        with conn() as c:
            job_or_404(c, job_id)
            return wa.history(c, job_id)

    @app.get("/jobs/{job_id}/weight-suggestions/{sid}", tags=["recruiter"])
    def weight_suggestion(job_id: str, sid: str):
        """Re-open a suggestion: changes and preview against the job's weights now."""
        from services import weight_assistant as wa
        with conn() as c:
            j = job_or_404(c, job_id)
            return wa.view(c, j, suggestion_or_404(c, job_id, sid))

    @app.post("/jobs/{job_id}/weight-suggestions/{sid}/apply", tags=["recruiter"])
    def apply_weight_suggestion(job_id: str, sid: str):
        from services import weight_assistant as wa
        with conn() as c:
            j = job_or_404(c, job_id)
            return {"job": wa.apply(c, j, suggestion_or_404(c, job_id, sid))}

    @app.post("/jobs/{job_id}/weight-suggestions/{sid}/undo", tags=["recruiter"])
    def undo_weight_suggestion(job_id: str, sid: str):
        from services import weight_assistant as wa
        with conn() as c:
            j = job_or_404(c, job_id)
            try:
                return {"job": wa.undo(c, j, suggestion_or_404(c, job_id, sid))}
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc

    @app.get("/jobs/{job_id}/applicants", tags=["recruiter"])
    def applicants(job_id: str, sort: str = "match", status: str | None = None, all_must_haves: bool = False,
                   min_score: float | None = None):
        """Ranked by the job's weights. Re-ranking after a weight change makes no model calls."""
        with conn() as c:
            j = job_or_404(c, job_id)
            try:
                rows = rc.applicants(c, j, sort=sort, status=status, all_must_haves=all_must_haves, min_score=min_score)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            return {"weights": j["weights"], "preset": j["preset"], "applicants": rows}

    @app.post("/applications/{app_id}/opened", status_code=204, tags=["recruiter"])
    def panel_opened(app_id: str):
        """The recruiter opened this applicant's panel: an activity signal for the job (specs/17 §4.1)."""
        from services import candidate_jobs as cj
        with conn() as c:
            app_or_404(c, app_id)
            cj.record_panel_opened(c, app_id)

    @app.get("/applications/{app_id}", tags=["recruiter"])
    def application(app_id: str):
        with conn() as c:
            a = app_or_404(c, app_id)
            return rc.panel(c, a, job_or_404(c, a["job_id"]))

    @app.patch("/applications/{app_id}", tags=["recruiter"])
    def application_status(app_id: str, body: StatusIn):
        """Shortlist or back to new (stages follow). Not moving forward normally goes through the email drawer."""
        with conn() as c:
            a = wf.set_status(c, app_or_404(c, app_id), body.status)
            return {"application_id": app_id, "status": a["status"], "stage_id": a["stage_id"]}

    @app.get("/jobs/{job_id}/compare", tags=["recruiter"])
    def compare(job_id: str, ids: str):
        with conn() as c:
            try:
                return rc.compare(c, job_or_404(c, job_id), [i for i in ids.split(",") if i])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

