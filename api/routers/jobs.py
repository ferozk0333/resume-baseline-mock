"""Recruiter job list; create, edit and publish jobs (specs/14); applying."""
from __future__ import annotations

import asyncio
import os
from typing import Literal

from fastapi import File, Form, HTTPException, UploadFile

from api.context import Api
from api.schemas import ApplyIn, CopyIn, JobDraftIn, JobStatusIn, SaveJobIn, TaskOut
from core import config, db
from extraction import store
from extraction.records import scoring_blocker
from services import candidate_jobs as cj
from services import jobs as jb
from services import recruiter as rc


def register(api: Api) -> None:
    app, conn, not_found, read_upload, state = api.app, api.conn, api.not_found, api.read_upload, api.state
    start_scoring = api.start_scoring

    # --- Recruiter (phase 11) ----------------------------------------------------------------------------
    @app.get("/jobs", tags=["recruiter"])
    def jobs():
        with conn() as c:
            return rc.list_jobs(c)

    # --- Create, edit and publish jobs (specs/14) ------------------------------------------------------------
    def draft_or_404(c, job_id: str) -> dict:
        try:
            return jb.load_draft(c, job_id)
        except KeyError:
            not_found("Job")

    @app.get("/recruiter/jobs", tags=["jobs"])
    def jobs_listing(status: Literal["draft", "open", "closed"] | None = None):
        """The Jobs page: title, company, location, applicants, created, status (tabs Open · Drafts · Closed)."""
        with conn() as c:
            return jb.listing(c, status)

    @app.get("/jobs/templates", tags=["jobs"])
    def job_templates():
        """"Copy an existing job": every published job and the role templates."""
        with conn() as c:
            return jb.templates(c)

    @app.post("/jobs/drafts", tags=["jobs"])
    def new_draft():
        """Start from scratch: an empty draft at step 2."""
        with conn() as c:
            return jb.load_draft(c, jb.from_scratch(c))

    @app.post("/jobs/drafts/copy", tags=["jobs"])
    def copy_draft(body: CopyIn):
        with conn() as c:
            try:
                return jb.load_draft(c, jb.copy_of(c, body.kind, body.id))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.post("/jobs/drafts/read", tags=["jobs"])
    async def read_jd(file: UploadFile | None = File(None), text: str | None = Form(None)):
        """Upload (PDF, DOCX, text) or paste a job description → one JOB_EXTRACT_MODEL call → a prefilled draft.
        If the model is unavailable the draft starts empty, with the JD text to copy from. Never blocks."""
        from extraction.job_reader import read_job
        from ingest.documents import DocumentError, extract_text
        jd_file = None
        try:
            if file is not None and file.filename:
                data = await read_upload(file, "The file")
                if file.filename.lower().endswith((".txt", ".md")):
                    jd_text = data.decode("utf-8", errors="replace")
                else:
                    with conn() as c:
                        jd_text = extract_text(file.filename, data, c)
                folder = config.UPLOAD_DIR / "jobs"
                folder.mkdir(parents=True, exist_ok=True)
                from ingest.documents import sha256
                jd_file = str(folder / f"{sha256(data)[:16]}{os.path.splitext(file.filename)[1].lower()}")
                with open(jd_file, "wb") as fh:
                    fh.write(data)
            else:
                jd_text = (text or "").strip()
        except DocumentError as exc:
            raise HTTPException(422, str(exc)) from exc
        if len(jd_text.strip()) < 200:
            raise HTTPException(422, "Paste or upload the full job description (at least a few sentences).")
        read = await asyncio.to_thread(read_job, state.job_llm, jd_text)
        with conn() as c:
            return jb.load_draft(c, jb.from_extraction(c, read, jd_text, jd_file))

    @app.get("/jobs/{job_id}/draft", tags=["jobs"])
    def get_draft(job_id: str):
        """The form: a draft, or an edit of a published job (resumes where the recruiter left off)."""
        with conn() as c:
            return draft_or_404(c, job_id)

    @app.put("/jobs/{job_id}/draft", tags=["jobs"])
    def put_draft(job_id: str, body: JobDraftIn):
        """Autosave (every change). A published job is not changed until Save."""
        with conn() as c:
            draft_or_404(c, job_id)
            return jb.save_draft(c, job_id, body.draft)

    @app.delete("/jobs/{job_id}/draft", status_code=204, tags=["jobs"])
    def delete_draft(job_id: str):
        """Delete a draft job, or discard an unsaved edit of a published one."""
        with conn() as c:
            job = jb.get(c, job_id) or not_found("Job")
            if job["status"] == "draft":
                jb.delete_draft(c, job_id)
            else:
                jb.discard_edit(c, job_id)

    @app.post("/jobs/{job_id}/publish", tags=["jobs"])
    def publish_job(job_id: str):
        with conn() as c:
            draft_or_404(c, job_id)
            try:
                return jb.summary(c, jb.publish(c, job_id))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.get("/jobs/{job_id}/edit-impact", tags=["jobs"])
    def job_edit_impact(job_id: str):
        with conn() as c:
            draft_or_404(c, job_id)
            return jb.edit_impact(c, job_id)

    @app.post("/jobs/{job_id}/save", tags=["jobs"])
    def save_job(job_id: str, body: SaveJobIn):
        """Save an edit of a published job. If applicants must be re-scored, 409 with the counts unless confirmed;
        then the re-scoring starts in the background (saved job-independent judgments are reused)."""
        with conn() as c:
            draft_or_404(c, job_id)
            impact = jb.edit_impact(c, job_id)
            if impact["rescore"] and not body.confirm:
                raise HTTPException(409, impact)
            try:
                out = jb.save_edit(c, job_id)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            baseline_id = out["job"]["baseline_id"]
        started = [start_scoring(a["candidate_id"], baseline_id, None, kind="rescore", application_id=a["id"])
                   for a in out["rescore"]]
        return {"job": jb.summary(db.connect(state.db_path), out["job"]), "rescoring": len(started), "tasks": started}

    @app.post("/jobs/{job_id}/status", tags=["jobs"])
    def job_status(job_id: str, body: JobStatusIn):
        """Close (gone from the candidate side; applications stay) or reopen."""
        with conn() as c:
            jb.get(c, job_id) or not_found("Job")
            try:
                return jb.summary(c, jb.set_status(c, job_id, body.status))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.get("/jobs/{job_id}/posting", tags=["jobs"])
    def job_posting(job_id: str):
        """The candidate-facing posting and the original job description (job page's Job description tab)."""
        with conn() as c:
            job = jb.get(c, job_id) or not_found("Job")
            return {**jb.posting(c, job), "status": job["status"], "jd_text": job.get("jd_text"),
                    "has_jd_file": bool(job.get("jd_file"))}

    @app.get("/jobs/{job_id}/jd-file", tags=["jobs"])
    def job_jd_file(job_id: str):
        from fastapi.responses import FileResponse
        with conn() as c:
            job = jb.get(c, job_id) or not_found("Job")
        if not job.get("jd_file") or not os.path.exists(job["jd_file"]):
            not_found("Job description file")
        return FileResponse(job["jd_file"])

    @app.get("/open-jobs", tags=["jobs"])
    def open_jobs():
        """Candidate Home: "Apply to a job". Closed jobs are not listed."""
        with conn() as c:
            return jb.open_jobs(c)

    @app.post("/candidates/{candidate_id}/apply", response_model=TaskOut, status_code=202, tags=["jobs"])
    def apply(candidate_id: str, body: ApplyIn):
        """Apply with the confirmed profile: scored against the job (job-independent judgments reused); the
        application is visible to the recruiter at once."""
        with conn() as c:
            store.get_candidate(c, candidate_id) or not_found("Candidate")
            blocker = scoring_blocker(store.load_profile(c, candidate_id))
            if blocker:
                raise HTTPException(409, blocker)
            if candidate_id in state.review_threads or not cj.profile_confirmed(c, candidate_id):
                raise HTTPException(409, "Confirm your profile first: applying uses your confirmed profile. "
                                         "Finish the Review step, then apply.")
            try:
                app_id = jb.apply(c, body.job_id, candidate_id)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            baseline_id = jb.get(c, body.job_id)["baseline_id"]
        return {"task_id": start_scoring(candidate_id, baseline_id, None, kind="apply", application_id=app_id)}

