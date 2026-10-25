"""What every router shares: the app, the server state, the task store, a database connection per request and the
helpers several areas use (404s, ValueError → 422, background scoring and early start)."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi import FastAPI, HTTPException, UploadFile

from api.state import State
from core import config, db
from extraction import store
from services import candidate as cx
from services import recruiter as rc
from services.tasks import TaskStore


class Api:
    def __init__(self, app: FastAPI, state: State, tasks: TaskStore):
        self.app, self.state, self.tasks = app, state, tasks

    @contextmanager
    def conn(self):
        c = db.connect(self.state.db_path)
        try:
            yield c
        finally:
            c.close()

    @staticmethod
    def not_found(what: str):
        raise HTTPException(404, f"{what} not found.")

    @staticmethod
    async def read_upload(f: UploadFile, what: str) -> bytes:
        """An uploaded file's bytes; 413 with a plain message above UPLOAD_MAX_MB (specs/19 item 2)."""
        data = await f.read()
        if len(data) > config.UPLOAD_MAX_MB * 1024 * 1024:
            raise HTTPException(413, f"{what} is too large. The limit is {config.UPLOAD_MAX_MB:g} MB.")
        return data

    @staticmethod
    def call(fn, *args, **kwargs):
        """A service call whose ValueError is the user's to fix: 422 with its message."""
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    def cand_or_404(self, c, candidate_id: str) -> dict:
        return store.get_candidate(c, candidate_id) or self.not_found("Candidate")

    def job_or_404(self, c, job_id: str) -> dict:
        return rc.get_job(c, job_id) or self.not_found("Job")

    def app_or_404(self, c, app_id: str) -> dict:
        row = c.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        return dict(row) if row else self.not_found("Application")

    def run_row(self, c, run_id: str) -> dict:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else self.not_found("Run")

    def start_scoring(self, candidate_id: str, baseline_id: str, weights: dict | None, kind: str = "score",
                      application_id: str | None = None) -> str:
        """Background scoring. `application_id`: an application (Apply, or a job edit) that points at the run."""
        state, conn = self.state, self.conn
        task_id = self.tasks.create(kind, candidate_id)

        def work(progress):
            progress.say("prepare_match", "Starting to score…", 3)
            # Confirming the review scores against the baseline it was started with; Apply and a job edit score
            # the saved profile against the job's own baseline.
            thread = state.review_threads.pop(candidate_id, None) if kind in ("score", "rejudge") else None
            pipe = state.pipeline
            if thread and pipe.graph.get_state(pipe._cfg(thread)).interrupts:
                res = pipe.confirm_review(thread, on_event=progress.on_event)
            else:   # no paused review (e.g. after a server restart): score the saved profile
                res = pipe.score(candidate_id, baseline_id, weights, on_event=progress.on_event)
            if not res.get("run_id"):
                raise ValueError(res.get("blocked") or "Scoring did not start.")
            if not res.get("paused"):
                from services import recruiter
                with conn() as c:
                    recruiter.attach_run(c, res["run_id"])     # the candidate now applies to the role's job
                    if application_id:
                        from services import jobs as jb
                        jb.link_run(c, application_id, res["run_id"])
            return {"run_id": res["run_id"], "final": res.get("final"), "errors": res.get("errors") or [],
                    "paused": res.get("paused"), "_message": res.get("paused") or "Score ready"}
        self.tasks.submit(task_id, work)
        return task_id

    def prejudge_targets(self, candidate_id: str) -> list[str]:
        """Early start (specs/18 §2 item 7): role-specific judging for the picked roles, once the documents are read
        and until Confirm. Never blocks the request."""
        from market.baseline import resolve_baseline
        with self.conn() as c:
            if cx.onboarding(c, candidate_id)["step"] not in ("roles", "review"):
                return []
            if c.execute("SELECT 1 FROM evidence_items WHERE candidate_id = ? AND type = 'resume' AND status = 'failed'",
                         (candidate_id,)).fetchone():
                return []                                       # the resume will be uploaded again: don't spend calls
            bids = [resolve_baseline(c, t["baseline_id"]) for t in cx.targets(c, candidate_id)]
        return self.state.pipeline.prejudge(candidate_id, bids) if bids else []
