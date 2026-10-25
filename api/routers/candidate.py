"""Candidate experience (specs/17): onboarding, target roles, baseline scores, Home and job detail."""
from __future__ import annotations

import logging

from fastapi import HTTPException

from api.context import Api
from api.schemas import (HideJobIn, OnboardingStepIn, PlanDoneIn, RetryRolesIn, SaveJobPrefIn, TargetIn, TargetsIn,
                         TaskOut)
from core import db
from extraction import store
from extraction.records import scoring_blocker
from services import candidate as cx
from services import candidate_jobs as cj
from services import jobs as jb
from services.tasks import clean_error

log = logging.getLogger("rightstaff.api")


def register(api: Api) -> None:
    app, conn, not_found, state, tasks = api.app, api.conn, api.not_found, api.state, api.tasks
    cand_or_404, prejudge_targets = api.cand_or_404, api.prejudge_targets
    bad_request = api.call

    # --- Candidate experience (specs/17) ---------------------------------------------------------------------
    @app.get("/candidates/{candidate_id}/onboarding", tags=["candidate"])
    def get_onboarding(candidate_id: str):
        """Where the candidate is in the first run (every step resumable), with the tasks to re-attach to."""
        with conn() as c:
            cand = cand_or_404(c, candidate_id)
            ob = cx.onboarding(c, candidate_id)
            d = cx.application_details(c, candidate_id)
            return {"candidate": cand, "onboarding": ob, "targets": cx.targets(c, candidate_id),
                    "read_task": tasks.get(ob["read_task_id"]) if ob.get("read_task_id") else None,
                    "score_task": tasks.get(ob["score_task_id"]) if ob.get("score_task_id") else None,
                    "awaiting_review": candidate_id in state.review_threads,
                    "details": {"saved_at": d["saved_at"], "dismissed_at": d["dismissed_at"]},
                    "roles": [{"baseline_id": k, "title": v} for k, v in cx.role_baselines(c).items()]}

    @app.get("/candidates/{candidate_id}/reading-progress", tags=["candidate"])
    def reading_progress(candidate_id: str):
        """The document-reading wait: each added source's status and what was found in it. From saved data only."""
        from services import wait_screens as ws
        with conn() as c:
            cand_or_404(c, candidate_id)
            ob = cx.onboarding(c, candidate_id)
            return ws.reading_view(c, candidate_id, tasks.get(ob["read_task_id"]) if ob.get("read_task_id") else None)

    @app.get("/candidates/{candidate_id}/scoring-progress", tags=["candidate"])
    def scoring_progress(candidate_id: str):
        """The scoring wait: per role, a status and a fill per dimension (no internal counts)."""
        from services import wait_screens as ws
        with conn() as c:
            cand_or_404(c, candidate_id)
            ob = cx.onboarding(c, candidate_id)
            return ws.scoring_view(c, candidate_id, tasks.get(ob["score_task_id"]) if ob.get("score_task_id") else None)

    @app.get("/config/wait-tips", tags=["candidate"])
    def wait_tips():
        """"Good to know" tips for the two waits, from config/wait_tips.yaml."""
        from services import wait_screens as ws
        return ws.tips()

    @app.put("/candidates/{candidate_id}/onboarding", tags=["candidate"])
    def put_onboarding(candidate_id: str, body: OnboardingStepIn):
        with conn() as c:
            cand_or_404(c, candidate_id)
            if body.step in ("review", "scores", "details", "done") and not cx.targets(c, candidate_id):
                raise HTTPException(409, "Pick at least one target role first.")
            return bad_request(cx.set_step, c, candidate_id, body.step)

    def resume_read(candidate_id: str) -> bool:
        """The resume is read and saved (the other sources may still be reading): early start can begin."""
        with conn() as c:
            return c.execute("SELECT 1 FROM evidence_items WHERE candidate_id = ? AND type = 'resume' AND "
                             "status = 'validated'", (candidate_id,)).fetchone() is not None

    def source_ready(candidate_id: str, doc_type: str) -> None:
        """A source was saved into the profile while the others are still read: judge what is there now
        (specs/18 Phase 3). Background only; never fails the reading."""
        try:
            prejudge_targets(candidate_id)
        except Exception:                                  # noqa: BLE001
            log.exception("early start after %s failed", doc_type)
    state.on_source_ready = source_ready

    @app.put("/candidates/{candidate_id}/targets", tags=["candidate"])
    def put_targets(candidate_id: str, body: TargetsIn):
        """1–3 target roles, one primary (default: the first picked). Once the documents are read, newly picked
        roles start their judging at once."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            out = bad_request(cx.set_targets, c, candidate_id, body.baseline_ids, body.primary)
        if candidate_id in state.review_threads or resume_read(candidate_id):
            prejudge_targets(candidate_id)
        return out

    def score_roles(candidate_id: str, roles: list[dict], kind: str = "baseline_scores") -> str:
        """Score each role baseline in turn (job-independent judgments are reused across roles). Only the primary
        role's plan is written now; the others are "deferred" until their plan is first opened. A baseline score
        never applies the candidate to a job (Apply does)."""
        task_id = tasks.create(kind, candidate_id)
        tasks.event(task_id, "queued", "Waiting to start", 0, result_json={"roles": [t["baseline_id"] for t in roles]})

        def work(progress):
            progress.say("prepare_match", "Getting your scores ready…", 3)
            thread = state.review_threads.pop(candidate_id, None)
            if thread:                                  # roles are scored below, each with its own baseline
                try:
                    state.pipeline.confirm_review(thread, {"cancel": True})
                except Exception:                       # noqa: BLE001 - the review thread only held the pause
                    log.exception("closing the review thread failed")
            runs, failed, paused = {}, {}, None
            from market.baseline import resolve_baseline
            for i, t in enumerate(roles):               # one role failing never stops the others
                progress.say("score", f"Scoring your {t['title']} profile", 5 + 90 * i / len(roles))
                try:
                    with conn() as c:
                        bid = resolve_baseline(c, t["baseline_id"])
                    res = state.pipeline.score(candidate_id, bid, plan=t["is_primary"],
                                               on_event=cx.scoring_progress(progress, t["title"], i, len(roles)))
                    if not res.get("run_id"):
                        raise ValueError(res.get("blocked") or "Scoring did not start.")
                    runs[t["baseline_id"]] = res["run_id"]
                    paused = paused or res.get("paused")
                except Exception as exc:                # noqa: BLE001 - shown as "Couldn't finish" with Retry
                    log.exception("scoring %s failed", t["baseline_id"])
                    failed[t["baseline_id"]] = clean_error(exc)
            if not runs:
                raise ValueError(next(iter(failed.values()), "Scoring did not start."))
            return {"runs": runs, "failed": failed, "paused": paused, "roles": [t["baseline_id"] for t in roles],
                    "_message": paused or ("Some roles couldn't finish" if failed else "Your scores are ready")}
        tasks.submit(task_id, work)
        return task_id

    @app.post("/candidates/{candidate_id}/baseline-scores", response_model=TaskOut, status_code=202, tags=["candidate"])
    def baseline_scores(candidate_id: str, body: RetryRolesIn | None = None):
        """"Confirm profile": score every target role (primary first). 409 when the profile can't be scored.
        With `baseline_ids` ("Retry" on the wait screen): score only those roles again; the others keep their run."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            roles = cx.targets(c, candidate_id)
            if not roles:
                raise HTTPException(409, "Pick at least one target role first.")
            retry = body is not None and body.baseline_ids is not None
            if retry:
                roles = [t for t in roles if t["baseline_id"] in set(body.baseline_ids)]
                if not roles:
                    raise HTTPException(422, "None of these is one of your target roles.")
            blocker = scoring_blocker(store.load_profile(c, candidate_id))
            if blocker:
                raise HTTPException(409, blocker)
            ob = cx.onboarding(c, candidate_id)
            read = tasks.get(ob["read_task_id"]) if ob.get("read_task_id") else None
            if not retry and read is not None and read["status"] in ("queued", "running"):
                raise HTTPException(409, "We're still reading your documents. You can confirm when everything is read.")
        task_id = score_roles(candidate_id, roles)
        with conn() as c:
            if retry:
                cx.set_step(c, candidate_id, "scores", score_task_id=task_id)
            else:
                cx.set_step(c, candidate_id, "scores", score_task_id=task_id, confirmed_at=db.now_iso())
        return {"task_id": task_id}

    @app.get("/candidates/{candidate_id}/baseline-scores", tags=["candidate"])
    def get_baseline_scores(candidate_id: str):
        """One card per target role, primary first. Code only (no model call)."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            return cx.score_cards(c, candidate_id)

    @app.post("/candidates/{candidate_id}/targets", tags=["candidate"])
    def add_target(candidate_id: str, body: TargetIn):
        """"Add a role": scored now, plan deferred; job-independent judgments are reused."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            if cx.onboarding(c, candidate_id)["step"] != "done":
                raise HTTPException(409, "Finish setting up first.")
            blocker = scoring_blocker(store.load_profile(c, candidate_id))
            if blocker:
                raise HTTPException(409, blocker)
            roles = bad_request(cx.add_target, c, candidate_id, body.baseline_id)
            new = [t for t in roles if t["baseline_id"] == body.baseline_id]
            have = cx.role_run(c, candidate_id, body.baseline_id)
        task_id = None if have else score_roles(candidate_id, [{**new[0], "is_primary": False}], kind="add_role")
        return {"targets": roles, "task_id": task_id}

    @app.delete("/candidates/{candidate_id}/targets/{baseline_id}", tags=["candidate"])
    def remove_target(candidate_id: str, baseline_id: str):
        with conn() as c:
            cand_or_404(c, candidate_id)
            return bad_request(cx.remove_target, c, candidate_id, baseline_id)

    @app.post("/candidates/{candidate_id}/targets/{baseline_id}/primary", tags=["candidate"])
    def make_primary(candidate_id: str, baseline_id: str):
        with conn() as c:
            cand_or_404(c, candidate_id)
            return bad_request(cx.set_primary, c, candidate_id, baseline_id)

    @app.get("/candidates/{candidate_id}/applications", tags=["candidate"])
    def candidate_applications(candidate_id: str):
        """My applications: candidate-facing status and timeline. Never scores, ranks, notes or scorecards."""
        from services import my_applications as ma
        with conn() as c:
            cand_or_404(c, candidate_id)
            return ma.my_applications(c, candidate_id)

    @app.post("/candidates/{candidate_id}/plan-done", tags=["candidate"])
    def plan_done(candidate_id: str, body: PlanDoneIn):
        """"Mark done" (or undo) on a plan action. Changes no score."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            cx.mark_done(c, candidate_id, body.baseline_id, body.title, body.done)
            t = next((t for t in cx.targets(c, candidate_id) if t["baseline_id"] == body.baseline_id), None)
            return cx.score_card(c, candidate_id, t) if t else {}

    @app.get("/candidates/{candidate_id}/application-details", tags=["candidate"])
    def get_application_details(candidate_id: str):
        """The candidate's own "fill once" answers. Never part of any recruiter response or model input."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            return cx.application_details(c, candidate_id)

    @app.put("/candidates/{candidate_id}/application-details", tags=["candidate"])
    def put_application_details(candidate_id: str, body: dict):
        with conn() as c:
            cand_or_404(c, candidate_id)
            return bad_request(cx.save_application_details, c, candidate_id, body)

    # --- Candidate Home and job detail (specs/17 §4–§6): code only, no model calls ---------------------------------
    @app.get("/candidates/{candidate_id}/jobs", tags=["candidate"])
    def candidate_jobs(candidate_id: str):
        """Open recruiter jobs ranked for this candidate, with the JobMatch preview (dummy, never stored)."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            out = cj.home_jobs(c, candidate_id)
        out["profile_confirmed"] = out["profile_confirmed"] and candidate_id not in state.review_threads
        return out

    @app.get("/candidates/{candidate_id}/jobs/{job_id}", tags=["candidate"])
    def candidate_job(candidate_id: str, job_id: str):
        with conn() as c:
            cand_or_404(c, candidate_id)
            out = cj.job_detail(c, candidate_id, job_id) or not_found("Job")
        out["profile_confirmed"] = out["profile_confirmed"] and candidate_id not in state.review_threads
        return out

    @app.post("/candidates/{candidate_id}/jobs/{job_id}/save", tags=["candidate"])
    def save_job(candidate_id: str, job_id: str, body: SaveJobPrefIn):
        with conn() as c:
            cand_or_404(c, candidate_id)
            jb.get(c, job_id) or not_found("Job")
            return cj.save(c, candidate_id, job_id, body.saved)

    @app.post("/candidates/{candidate_id}/jobs/{job_id}/hide", tags=["candidate"])
    def hide_job(candidate_id: str, job_id: str, body: HideJobIn):
        """Not interested (optional reason). Hides the job for this candidate; changes no score."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            jb.get(c, job_id) or not_found("Job")
            return cj.hide(c, candidate_id, job_id, body.reason)

    @app.post("/candidates/{candidate_id}/jobs/{job_id}/unhide", tags=["candidate"])
    def unhide_job(candidate_id: str, job_id: str):
        with conn() as c:
            cand_or_404(c, candidate_id)
            return cj.unhide(c, candidate_id, job_id)

    @app.post("/candidates/{candidate_id}/application-details/dismiss", tags=["candidate"])
    def dismiss_application_details(candidate_id: str):
        with conn() as c:
            cand_or_404(c, candidate_id)
            return cx.dismiss_application_details(c, candidate_id)

