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
                planner=RoutedLLM(max_tokens=config.PLAN_MAX_TOKENS, effort=config.PLAN_EFFORT))


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

    @app.get("/config/weights", tags=["scoring"])
    def default_weights():
        return {"weights": config.default_weights(), "labels": config.SUBCATEGORY_LABELS}

    return app

