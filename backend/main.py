"""FastAPI backend (specs/10 §2) around the existing pipeline, scoring and database.

Run: `.venv/bin/uvicorn backend.main:create_app --factory --reload --port 8000` (or `make dev`). OpenAPI docs at /docs.
Set RIGHTSTAFF_DEPS_FACTORY="module:function" to build Deps differently (tests use fakes).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
from contextlib import contextmanager
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend import media
from backend.tasks import TaskStore, clean_error
from core import config, db
from core.models import Baseline
from extraction import store
from extraction.onboard import CertificationForm, OnboardingInputs, ReferenceForm, Upload
from extraction.records import scoring_blocker
from graph.context import Deps
from graph.pipeline import (Pipeline, current_plan, regenerate_plan, rescore, run_gaps, run_scores,
                            save_run_weights)
from judges.service import clear_judgments
from scoring.explain import FORMULAS
from scoring.plan import TYPE_LABELS

log = logging.getLogger("rightstaff.api")


# --- Dependencies -----------------------------------------------------------------------------------

def default_deps() -> Deps:
    from core.llm import RoutedLLM
    from judges.service import get_judge
    models = [config.EXTRACT_MODEL, config.JUDGE_FALLBACK_MODEL, config.COMPLEXITY_MODEL, config.REASONING_MODEL]
    if not config.ANTHROPIC_API_KEY and not all(m.startswith(("groq/", "gemini/")) for m in models):
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env (or set LLM_PROVIDER=gemini).")
    return Deps(str(config.DB_PATH), get_judge(), llm=RoutedLLM(effort=config.EXTRACT_EFFORT), extraction_cache=True,
                planner=RoutedLLM(max_tokens=config.PLAN_MAX_TOKENS, effort=config.PLAN_EFFORT),
                writer=RoutedLLM())


def build_deps() -> Deps:
    factory = os.getenv("RIGHTSTAFF_DEPS_FACTORY")
    if factory:
        module, func = factory.split(":")
        return getattr(importlib.import_module(module), func)()
    return default_deps()


class State:
    """One pipeline per server: it holds paused onboarding threads (review) in memory."""

    def __init__(self, deps: Deps | None = None):
        self._deps = deps
        self._pipeline: Pipeline | None = None
        self.review_threads: dict[str, str] = {}          # candidate_id → paused onboarding thread
        self._rejection = None
        self._loop = None

    @property
    def rejection(self):
        """The "Not moving forward" graph (paused drafts live in its in-memory checkpointer)."""
        if self._rejection is None:
            from backend.rejection_flow import RejectionFlow
            self._rejection = RejectionFlow(self.db_path, self.deps.writer)
        return self._rejection

    @property
    def loop(self):
        """The interview-loop graph (paused loops are checkpointed to SQLite)."""
        if self._loop is None:
            from backend.loop_flow import LoopFlow
            self._loop = LoopFlow(self.db_path, self.deps.writer)
        return self._loop

    @property
    def deps(self) -> Deps:
        if self._deps is None:
            self._deps = build_deps()
        return self._deps

    @property
    def pipeline(self) -> Pipeline:
        if self._pipeline is None:
            self._pipeline = Pipeline(self.deps)
        return self._pipeline

    @property
    def db_path(self) -> str:
        return self._deps.db_path if self._deps is not None else str(config.DB_PATH)


# --- Request and response models ------------------------------------------------------------------------

class CandidateIn(BaseModel):
    name: str | None = None
    target_role: str | None = None


class CandidateOut(BaseModel):
    id: str
    name: str | None
    target_role: str | None
    github_username: str | None = None
    portfolio_url: str | None = None
    created_at: str | None = None


class TaskOut(BaseModel):
    task_id: str


class TaskStatus(BaseModel):
    id: str
    kind: str
    status: str
    step: str | None
    message: str | None
    percent: float | None
    error: str | None
    candidate_id: str | None
    run_id: str | None
    result: dict | None


class ScoreIn(BaseModel):
    baseline_id: str
    weights: dict | None = None


class WeightsIn(BaseModel):
    weights: dict


class JobUpdate(BaseModel):
    weights: dict | None = None
    must_haves: list[str] | None = None
    preset: str | None = None


class StatusIn(BaseModel):
    status: Literal["new", "shortlisted", "not_moving_forward"]


class StageMove(BaseModel):
    stage_id: str | None


class StageIn(BaseModel):
    name: str
    focus: list[str] | None = None


class StageEdit(BaseModel):
    name: str | None = None
    focus: list[str] | None = None


class StageOrder(BaseModel):
    ids: list[str]


class JobSettingsIn(BaseModel):
    recruiter_name: str | None = None
    duration_minutes: int | None = None
    availability: str | None = None
    scheduling_link: str | None = None


class RejectIn(BaseModel):
    reason_ids: list[str] | None = None
    note: str | None = None


class LoopIn(BaseModel):
    application_ids: list[str]


class StageEmailIn(BaseModel):
    stage_id: str


class EmailEdit(BaseModel):
    subject: str | None = None
    body: str | None = None


class ProfileEdit(BaseModel):
    """One review edit. `item` is role | project | bullet? (bullets go with their role/project) | claim |
    certification; `op` add | edit | delete | add_back (add_back uses `id` of a dropped item)."""
    op: Literal["add", "edit", "delete", "add_back"]
    item: Literal["role", "project", "claim", "certification", "dropped"]
    id: str | None = None
    data: dict = Field(default_factory=dict)


# --- App ------------------------------------------------------------------------------------------------

def create_app(deps: Deps | None = None) -> FastAPI:
    state = State(deps)
    app = FastAPI(title="RightStaff API", version="0.2",
                  description="Scores a candidate against a baseline role profile (EEAT). Local mock.")
    app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])     # local mock: any local web port
    app.state.rs = state
    tasks = TaskStore(state.db_path)
    app.state.tasks = tasks
    conn0 = db.init_app_db(state.db_path)
    conn0.close()

    @contextmanager
    def conn():
        c = db.connect(state.db_path)
        try:
            yield c
        finally:
            c.close()

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):           # stack traces stay in the log
        log.exception("request failed: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": clean_error(exc)})

    def not_found(what: str):
        raise HTTPException(404, f"{what} not found.")

    # --- Baselines ---------------------------------------------------------------------------------
    @app.get("/baselines", response_model=list[Baseline], tags=["baselines"])
    def list_baselines():
        with conn() as c:
            return db.list_baselines(c)

    @app.get("/baselines/{baseline_id}", response_model=Baseline, tags=["baselines"])
    def get_baseline(baseline_id: str):
        with conn() as c:
            return db.get_baseline(c, baseline_id) or not_found("Baseline")

    @app.post("/baselines", response_model=Baseline, status_code=201, tags=["baselines"])
    def create_baseline(baseline: Baseline):
        with conn() as c:
            new_id = db.create_baseline(c, baseline.model_copy(update={"id": None}))
            return db.get_baseline(c, new_id)

    @app.put("/baselines/{baseline_id}", response_model=Baseline, tags=["baselines"])
    def update_baseline(baseline_id: str, baseline: Baseline):
        with conn() as c:
            if db.get_baseline(c, baseline_id) is None:
                not_found("Baseline")
            db.update_baseline(c, baseline.model_copy(update={"id": baseline_id}))
            return db.get_baseline(c, baseline_id)

    @app.delete("/baselines/{baseline_id}", status_code=204, tags=["baselines"])
    def delete_baseline(baseline_id: str):
        with conn() as c:
            if not db.delete_baseline(c, baseline_id):
                not_found("Baseline")

    @app.get("/baselines-check", tags=["baselines"])
    def check_level(level: str, required_years: float):
        """Level/years warning (specs/06): shown as the user types."""
        from core.models import level_years_warning
        return {"warning": level_years_warning(level, required_years)}

    @app.post("/baselines/reset-seed", response_model=list[str], tags=["baselines"])
    def reset_seed():
        with conn() as c:
            return db.seed_baselines(c, reset=True)

    # --- Candidates ----------------------------------------------------------------------------------
    @app.get("/candidates", response_model=list[CandidateOut], tags=["candidates"])
    def list_candidates():
        with conn() as c:
            return store.list_candidates(c)

    @app.post("/candidates", response_model=CandidateOut, status_code=201, tags=["candidates"])
    def create_candidate(body: CandidateIn):
        with conn() as c:
            cid = store.create_candidate(c, body.name, body.target_role)
            return store.get_candidate(c, cid)

    @app.post("/candidates/{candidate_id}/onboard", response_model=TaskOut, status_code=202, tags=["candidates"])
    async def onboard(candidate_id: str, baseline_id: str = Form(...), resume: UploadFile = File(...),
                      linkedin_pdf: UploadFile | None = File(None), github: str | None = Form(None),
                      portfolio_url: str | None = Form(None),
                      demo_videos: str = Form("[]", description='JSON list of YouTube/Loom URLs (max 3)'),
                      certifications: str = Form("[]", description="JSON list of {name, issuer, date, "
                                                                    "credential_id, verify_url}"),
                      references: str = Form("[]", description="JSON list of {name, relationship, contact, "
                                                                "statement}")):
        with conn() as c:
            cand = store.get_candidate(c, candidate_id) or not_found("Candidate")
            if db.get_baseline(c, baseline_id) is None:
                not_found("Baseline")
        try:
            videos = json.loads(demo_videos or "[]")
            media.validate_links(videos)
            certs = [CertificationForm(**x) for x in json.loads(certifications or "[]")]
            refs = [ReferenceForm(**x) for x in json.loads(references or "[]")]
        except media.MediaError as exc:
            raise HTTPException(422, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, f"Could not read the form: {exc}") from exc
        inputs = OnboardingInputs(
            resume=Upload(resume.filename or "resume", await resume.read()),
            linkedin_pdf=Upload(linkedin_pdf.filename or "linkedin.pdf", await linkedin_pdf.read())
            if linkedin_pdf is not None and linkedin_pdf.filename else None,
            github=github or None, portfolio_url=portfolio_url or None, certifications=certs, references=refs,
            name=cand["name"], target_role=cand["target_role"], candidate_id=candidate_id)
        task_id = tasks.create("onboard", candidate_id)

        def work(progress):
            progress.say("ingest", "Reading your documents…", 2)
            res = state.pipeline.start_onboarding(inputs, baseline_id, on_event=progress.on_event)
            with conn() as c:
                media.save_links(c, candidate_id, videos)
            if res.get("awaiting_review"):
                state.review_threads[candidate_id] = res["thread_id"]
                return {"_status": "done", "_message": "Ready for review", "candidate_id": candidate_id,
                        "warnings": res.get("warnings") or [], "blocked": res["awaiting_review"].get("blocked")}
            raise ValueError(res.get("blocked") or "Onboarding stopped before the review step.")
        tasks.submit(task_id, work)
        return {"task_id": task_id}

    @app.get("/candidates/{candidate_id}/profile", tags=["candidates"])
    def get_profile(candidate_id: str):
        with conn() as c:
            cand = store.get_candidate(c, candidate_id) or not_found("Candidate")
            profile = store.load_profile(c, candidate_id)
            evidence = store.list_evidence(c, candidate_id)
            from dataclasses import asdict
            from extraction.records import REVIEW_MESSAGES
            data = json.loads(json.dumps(asdict(profile), default=str))
            for item in [*data["roles"], *data["projects"]]:          # readable review flags
                item["review_reasons"] = [REVIEW_MESSAGES.get(r, r) for r in item["review_reasons"]]
            return {
                "candidate": cand,
                "profile": data,
                "dropped": store.list_dropped(c, candidate_id),
                "warnings": [e["error"] for e in evidence if e["status"] == "failed" and e["error"]],
                "sources": [{"id": e["id"], "type": e["type"], "status": e["status"], "text": e["raw_text"]}
                            for e in evidence if e["type"] in ("resume", "linkedin_pdf", "portfolio")],
                "media_links": media.list_links(c, candidate_id),
                "blocker": scoring_blocker(profile),
            }

    @app.patch("/candidates/{candidate_id}/profile", tags=["candidates"])
    def edit_profile(candidate_id: str, edit: ProfileEdit):
        from datetime import date as _date

        def day(v):
            return _date.fromisoformat(v + "-01" if v and len(v) == 7 else v) if v else None
        d = edit.data
        with conn() as c:
            store.get_candidate(c, candidate_id) or not_found("Candidate")
            try:
                if edit.op == "delete":
                    table = {"role": "roles", "project": "projects", "claim": "claims",
                             "certification": "certifications"}.get(edit.item)
                    if table is None or not edit.id:
                        raise ValueError("delete needs an item type and id")
                    store.delete_item(c, table, edit.id)
                elif edit.op == "add_back" or edit.item == "dropped":
                    store.restore_dropped(c, edit.id)
                elif edit.item == "role":
                    store.save_role(c, candidate_id, role_id=edit.id if edit.op == "edit" else None,
                                    title=d.get("title", ""), employer=d.get("employer"), industry=d.get("industry"),
                                    start_date=day(d.get("start_date")), end_date=day(d.get("end_date")),
                                    is_current=bool(d.get("is_current")), team_size=d.get("team_size"),
                                    bullets=d.get("bullets", []))
                elif edit.item == "project":
                    store.save_project(c, candidate_id, project_id=edit.id if edit.op == "edit" else None,
                                       kind=d.get("kind", "project"), name=d.get("name", ""),
                                       description=d.get("description"), url=d.get("url"),
                                       start_date=day(d.get("start_date")), end_date=day(d.get("end_date")),
                                       bullets=d.get("bullets", []))
                elif edit.item == "claim":
                    store.add_claim(c, candidate_id, d.get("kind", "skill"), d.get("name", ""))
                elif edit.item == "certification":
                    store.save_certification(c, candidate_id, cert_id=edit.id if edit.op == "edit" else None,
                                             name=d.get("name", ""), issuer=d.get("issuer"), issued_date=None,
                                             credential_id=d.get("credential_id"), verify_url=d.get("verify_url"))
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return get_profile(candidate_id)

    @app.get("/candidates/{candidate_id}/runs", tags=["candidates"])
    def candidate_runs(candidate_id: str):
        with conn() as c:
            return db.list_runs(c, candidate_id)

    # --- Scoring ---------------------------------------------------------------------------------------
    def start_scoring(candidate_id: str, baseline_id: str, weights: dict | None, kind: str = "score") -> str:
        task_id = tasks.create(kind, candidate_id)

        def work(progress):
            progress.say("prepare_match", "Starting to score…", 3)
            thread = state.review_threads.pop(candidate_id, None)
            pipe = state.pipeline
            if thread and pipe.graph.get_state(pipe._cfg(thread)).interrupts:
                res = pipe.confirm_review(thread, on_event=progress.on_event)
            else:   # no paused review (e.g. after a server restart): score the saved profile
                res = pipe.score(candidate_id, baseline_id, weights, on_event=progress.on_event)
            if not res.get("run_id"):
                raise ValueError(res.get("blocked") or "Scoring did not start.")
            return {"run_id": res["run_id"], "final": res.get("final"), "errors": res.get("errors") or [],
                    "paused": res.get("paused"), "_message": res.get("paused") or "Score ready"}
        tasks.submit(task_id, work)
        return task_id

    @app.post("/candidates/{candidate_id}/score", response_model=TaskOut, status_code=202, tags=["scoring"])
    def score(candidate_id: str, body: ScoreIn):
        with conn() as c:
            store.get_candidate(c, candidate_id) or not_found("Candidate")
            if db.get_baseline(c, body.baseline_id) is None:
                not_found("Baseline")
            blocker = scoring_blocker(store.load_profile(c, candidate_id))
        if blocker:
            raise HTTPException(409, blocker)
        return {"task_id": start_scoring(candidate_id, body.baseline_id, body.weights)}

    def run_row(c, run_id: str) -> dict:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else not_found("Run")

    def run_view(c, run: dict, weights: dict) -> dict:
        scores = run_scores(c, run["id"])
        agg = rescore(c, run["id"], weights)
        subs = []
        for dim, names in config.SUBCATEGORIES.items():
            for s in names:
                v = scores.get(s, {"status": "error", "score": None, "details": {}, "guidance": None})
                subs.append({"key": s, "label": config.SUBCATEGORY_LABELS[s], "dimension": dim,
                             "status": v["status"], "score": v["score"], "guidance": v["guidance"],
                             "details": v["details"], "formula": FORMULAS.get(s),
                             "weight": weights["subcategories"][dim][s],
                             "effective_weight": agg.effective_weights.get(s, 0.0)})
        return {"run": {k: run[k] for k in ("id", "candidate_id", "baseline_id", "status", "started_at",
                                            "finished_at", "logic_version", "plan_status")},
                "weights": weights, "final": agg.final, "dimensions": agg.dimensions, "subcategories": subs,
                "gaps": [g.as_dict() for g in run_gaps(c, run["id"], weights)]}

    @app.get("/runs/{run_id}", tags=["scoring"])
    def get_run(run_id: str):
        with conn() as c:
            run = run_row(c, run_id)
            return run_view(c, run, json.loads(run["weights_json"]))

    @app.get("/runs/{run_id}/benchmarks", tags=["scoring"])
    def run_benchmarks(run_id: str):
        """Percentiles and "what higher scorers have" (specs/12). Recomputed from saved runs on every request."""
        from scoring import benchmarks
        with conn() as c:
            run_row(c, run_id)
            return benchmarks.for_run(c, run_id)

    @app.post("/runs/{run_id}/rescore", tags=["scoring"])
    def rescore_run(run_id: str, body: WeightsIn):
        """Re-aggregate saved scores and re-rank gaps with other weights. No model calls."""
        with conn() as c:
            run = run_row(c, run_id)
            try:
                return run_view(c, run, body.weights)
            except (KeyError, TypeError) as exc:
                raise HTTPException(422, f"weights are incomplete: missing {exc}") from exc

    @app.put("/runs/{run_id}/weights", tags=["scoring"])
    def save_weights(run_id: str, body: WeightsIn):
        with conn() as c:
            run_row(c, run_id)
            save_run_weights(c, run_id, body.weights)
        return {"saved": True}

    def plan_view(plan) -> dict:
        return {"status": "failed" if plan.error else "ready", "error": plan.error, "markdown": plan.markdown,
                "model": plan.model_version, "quick_wins": plan.quick_wins, "not_actionable": plan.not_actionable,
                "gaps": [g.as_dict() for g in plan.gaps], "dropped": plan.dropped,
                "actions": [{**p.action.model_dump(), "type_label": TYPE_LABELS.get(p.action.type, p.action.type),
                             "points": round(p.points, 1), "points_text": p.points_text}
                            for p in plan.actions]}

    @app.get("/runs/{run_id}/plan", tags=["plan"])
    def get_plan(run_id: str, weights: str | None = None):
        with conn() as c:
            run = run_row(c, run_id)
            w = json.loads(weights) if weights else None
            plan = current_plan(c, run_id, w)
            if plan is None:
                return {"status": run.get("plan_status") or "none", "actions": [], "gaps": []}
            return plan_view(plan)

    @app.post("/runs/{run_id}/plan/regenerate", response_model=TaskOut, status_code=202, tags=["plan"])
    def regenerate(run_id: str, body: WeightsIn | None = None):
        with conn() as c:
            run = run_row(c, run_id)
        task_id = tasks.create("plan", run["candidate_id"], run_id)

        def work(progress):
            progress.say("gap_analysis", "Writing a new improvement plan…", 20)
            with conn() as c:
                db.set_plan_status(c, run_id, "generating")
                plan = regenerate_plan(c, run_id, state.deps.planner, body.weights if body else None)
                db.set_plan_status(c, run_id, "failed" if plan.error else "ready")
            if plan.error:
                raise ValueError(f"The plan could not be generated: {plan.error}")
            return {"run_id": run_id, "_message": "Plan ready"}
        tasks.submit(task_id, work)
        return {"task_id": task_id}

    @app.post("/runs/{run_id}/rejudge", response_model=TaskOut, status_code=202, tags=["scoring"])
    def rejudge(run_id: str):
        with conn() as c:
            run = run_row(c, run_id)
            clear_judgments(c, run["candidate_id"], run["baseline_id"])
        return {"task_id": start_scoring(run["candidate_id"], run["baseline_id"], json.loads(run["weights_json"]),
                                         kind="rejudge")}

    # --- Tasks ---------------------------------------------------------------------------------------------
    @app.get("/tasks/{task_id}", response_model=TaskStatus, tags=["tasks"])
    def task_status(task_id: str):
        return tasks.get(task_id) or not_found("Task")

    @app.get("/tasks/{task_id}/events", tags=["tasks"])
    async def task_events(task_id: str, request: Request):
        """Server-Sent Events: one `progress` event per step, then `done` or `error`."""
        if tasks.get(task_id) is None:
            not_found("Task")

        async def stream():
            seq = 0
            while True:
                for e in tasks.events_after(task_id, seq):
                    seq = e["seq"]
                    kind = e["status"] if e["status"] in ("done", "error") else "progress"
                    yield f"event: {kind}\ndata: {json.dumps(e)}\n\n"
                    if kind != "progress":
                        return
                if await request.is_disconnected():
                    return
                await asyncio.sleep(0.3)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # --- Recruiter (phase 11) ----------------------------------------------------------------------------
    from backend import recruiter as rc
    from backend import workflows as wf

    def job_or_404(c, job_id: str) -> dict:
        return rc.get_job(c, job_id) or not_found("Job")

    def app_or_404(c, app_id: str) -> dict:
        row = c.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
        return dict(row) if row else not_found("Application")

    @app.get("/jobs", tags=["recruiter"])
    def jobs():
        with conn() as c:
            return rc.list_jobs(c)

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

    # --- Interview plan, pipeline and activity (specs/13 §3) ------------------------------------------
    def wf_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

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
        with conn() as c:
            return wf.pipeline(c, job_or_404(c, job_id))

    @app.patch("/applications/{app_id}/stage", tags=["workflows"])
    def move_stage(app_id: str, body: StageMove):
        """Move a card between stages; the move is logged in Activity."""
        with conn() as c:
            a = wf_call(wf.move, c, app_or_404(c, app_id), body.stage_id)
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
                "model": config.EMAIL_MODEL if state.deps.writer is not None else None}

    @app.post("/applications/{app_id}/not-moving-forward", tags=["workflows"])
    def reject_draft(app_id: str, body: RejectIn | None = None):
        """Reasons (code) → draft (model) → checks (code) → paused for the recruiter's review. Sends nothing."""
        with conn() as c:
            app_or_404(c, app_id)
        return state.rejection.draft(app_id, body.reason_ids if body else None, body.note if body else None)

    @app.post("/jobs/{job_id}/interview-loop", tags=["workflows"])
    def start_loop(job_id: str, body: LoopIn):
        with conn() as c:
            job = job_or_404(c, job_id)
        return wf_call(state.loop.start, job, body.application_ids)

    @app.get("/jobs/{job_id}/interview-loop/{batch_id}", tags=["workflows"])
    def loop_batch(job_id: str, batch_id: str):
        with conn() as c:
            job_or_404(c, job_id)
            return {"batch_id": batch_id, "drafts": wf.list_emails(c, job_id=job_id, batch_id=batch_id)}

    @app.post("/applications/{app_id}/stage-email", tags=["workflows"])
    def stage_email(app_id: str, body: StageEmailIn):
        with conn() as c:
            a = app_or_404(c, app_id)
            job = job_or_404(c, a["job_id"])
            stage = next((s for s in wf.stages(c, job["id"]) if s["id"] == body.stage_id and not s["terminal"]), None)
            if stage is None:
                raise HTTPException(422, "Pick an interview stage of this job.")
            return wf.draft_scheduling(c, a, job, stage, state.deps.writer)

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
            if e["kind"] == "not_moving_forward":
                return wf_call(state.rejection.decide, email_id, "send")
            if e["batch_id"] and e["status"] == "draft":
                return wf_call(state.loop.decide, e["batch_id"], "send", [email_id])[0]
            return wf_call(wf.send_scheduling, c, email_id)

    @app.post("/emails/{email_id}/cancel", tags=["workflows"])
    def cancel(email_id: str):
        with conn() as c:
            e = wf.get_email(c, email_id) or not_found("Email")
            if e["kind"] == "not_moving_forward" and e["status"] == "draft":
                return wf_call(state.rejection.decide, email_id, "cancel")
            if e["batch_id"] and e["status"] == "draft":
                return wf_call(state.loop.decide, e["batch_id"], "remove", [email_id])[0]
            return wf.cancel_email(c, email_id)

    @app.get("/jobs/{job_id}/compare", tags=["recruiter"])
    def compare(job_id: str, ids: str):
        with conn() as c:
            try:
                return rc.compare(c, job_or_404(c, job_id), [i for i in ids.split(",") if i])
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc

    @app.get("/candidates/{candidate_id}/files/resume", tags=["candidates"])
    def resume_file(candidate_id: str):
        """The uploaded (or rendered demo) resume, for the inline viewer."""
        from fastapi.responses import FileResponse
        with conn() as c:
            ev = next((e for e in store.list_evidence(c, candidate_id) if e["type"] == "resume" and e["file_path"]), None)
        if ev is None or not os.path.exists(ev["file_path"]):
            not_found("Resume file")
        kind = "application/pdf" if ev["file_path"].endswith(".pdf") else \
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        return FileResponse(ev["file_path"], media_type=kind,
                            headers={"Content-Disposition": f'inline; filename="resume.{ev["file_path"].rsplit(".", 1)[-1]}"'})

    @app.get("/config/mode", tags=["scoring"])
    def mode():
        """"demo": the API runs on fakes (scripted extraction and labels; uploads are not read). "live": real models."""
        fake = bool(os.getenv("RIGHTSTAFF_DEPS_FACTORY"))
        return {"mode": "demo" if fake else "live", "judge": config.JUDGE,
                "models": None if fake else {"extract": config.EXTRACT_MODEL, "judge_fallback": config.JUDGE_FALLBACK_MODEL,
                                             "complexity": config.COMPLEXITY_MODEL, "plan": config.REASONING_MODEL}}

    @app.get("/config/weights", tags=["scoring"])
    def default_weights():
        return {"weights": config.default_weights(), "labels": config.SUBCATEGORY_LABELS}

    return app

