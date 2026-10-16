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
                writer=RoutedLLM(), job_llm=RoutedLLM(max_tokens=config.EXTRACT_MAX_TOKENS))


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
        self._hiring = None

    @property
    def writer(self):
        """The email/reply/feedback model. Recruiter workflows need only this, never the judge: building the full
        scoring deps (and its Jev key check) is left to scoring."""
        if self._deps is not None or os.getenv("RIGHTSTAFF_DEPS_FACTORY"):
            return self.deps.writer
        from core.llm import RoutedLLM
        self._writer = getattr(self, "_writer", None) or RoutedLLM()
        return self._writer

    @property
    def job_llm(self):
        """The JD / market reader (specs/14, 15). Like `writer`, it never needs the judge."""
        if self._deps is not None or os.getenv("RIGHTSTAFF_DEPS_FACTORY"):
            return self.deps.job_llm
        from core.llm import RoutedLLM
        self._job_llm = getattr(self, "_job_llm", None) or RoutedLLM(max_tokens=config.EXTRACT_MAX_TOKENS)
        return self._job_llm

    @property
    def rejection(self):
        """The "Not moving forward" graph (paused drafts are checkpointed to SQLite)."""
        if self._rejection is None:
            from backend.rejection_flow import RejectionFlow
            self._rejection = RejectionFlow(self.db_path, self.writer)
        return self._rejection

    @property
    def hiring(self):
        """The hiring workflow graph, one thread per application (checkpointed to SQLite)."""
        if self._hiring is None:
            from backend.hiring_flow import HiringFlow
            self._hiring = HiringFlow(self.db_path, self.writer, rejection=self.rejection)
        return self._hiring

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


class JobDraftIn(BaseModel):
    draft: dict


class CopyIn(BaseModel):
    kind: Literal["job", "template"]
    id: str


class SaveJobIn(BaseModel):
    confirm: bool = False                # "N applicants will be re-scored" was confirmed


class JobStatusIn(BaseModel):
    status: Literal["open", "closed"]


class ApplyIn(BaseModel):
    job_id: str


class RecomputeIn(BaseModel):
    test: bool = False                   # no model: only postings whose extraction is cached or hand-labelled


class DecideIn(BaseModel):
    approve: bool
    canonical: str | None = None


class SourceIn(BaseModel):
    source: Literal["manual", "market"]
    confirm: bool = False


class SwitchBaselineIn(BaseModel):
    source: Literal["standard", "market"]
    confirm: bool = False                # the re-score notice was confirmed


class TargetsIn(BaseModel):
    baseline_ids: list[str]
    primary: str | None = None


class RetryRolesIn(BaseModel):
    baseline_ids: list[str] | None = None


class PlanDoneIn(BaseModel):
    baseline_id: str
    title: str
    done: bool = True


class TargetIn(BaseModel):
    baseline_id: str


class OnboardingStepIn(BaseModel):
    step: Literal["proof", "roles", "review", "scores", "details", "done"]


class SaveJobPrefIn(BaseModel):
    saved: bool


class HideJobIn(BaseModel):
    reason: Literal["location", "salary", "role", "seniority", "other"] | None = None


class MarketSettingsIn(BaseModel):
    allow_test_versions: bool


class WorkflowStart(BaseModel):
    confirm_move: bool = False           # "Move to Shortlisted and start?" was confirmed


class BulkStart(WorkflowStart):
    application_ids: list[str]
    check_only: bool = False             # only list who would be moved to Shortlisted (for the confirmation)


class WorkflowAction(BaseModel):
    """The recruiter's answer at the current step, e.g. {"action": "send", "email_id": …},
    {"action": "confirm", "start": "2026-10-20T11:00:00-04:00"}, {"action": "choose", "option": "slots"}."""
    action: str
    email_id: str | None = None
    message_id: str | None = None
    option: str | None = None
    start: str | None = None
    end: str | None = None
    stage_id: str | None = None


class ReplyIn(BaseModel):
    text: str | None = None
    canned: str | None = None


class Rating(BaseModel):
    area: str
    rating: int
    notes: str | None = None


class ScorecardIn(BaseModel):
    interviewer: str
    ratings: list[Rating]
    notes: str | None = None
    recommendation: Literal["strong_yes", "yes", "no", "strong_no"]


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

def start_ticker(state: State) -> None:
    """Scheduled workflow checks every WORKFLOW_TICK_SECONDS (no email is sent): replies to read, nudges due,
    interviews that ended; and the inbox poll when REPLY_INBOX=imap."""
    import threading
    import time

    def loop():
        last_poll = 0.0
        while True:
            time.sleep(config.WORKFLOW_TICK_SECONDS)
            if config.REPLY_INBOX == "imap" and time.time() - last_poll >= config.IMAP_POLL_MINUTES * 60:
                last_poll = time.time()
                try:
                    state.hiring.poll_inbox()           # logs each decision ("inbox: …")
                except Exception:                       # noqa: BLE001 - logged; the next poll tries again
                    log.exception("inbox poll failed")
            try:
                state.hiring.tick()
            except Exception:                           # noqa: BLE001 - logged; the next tick tries again
                log.exception("workflow tick failed")
    threading.Thread(target=loop, name="workflow-tick", daemon=True).start()


def create_app(deps: Deps | None = None) -> FastAPI:
    from contextlib import asynccontextmanager
    state = State(deps)

    @asynccontextmanager
    async def lifespan(_app):
        if config.WORKFLOW_TICK_SECONDS > 0:
            start_ticker(state)
        yield
    app = FastAPI(title="RightStaff API", version="0.2", lifespan=lifespan,
                  description="Scores a candidate against a baseline role profile (EEAT). Local mock.")
    @app.middleware("http")
    async def errors_with_cors(request: Request, call_next):
        """An unexpected error becomes a JSON 500 inside the CORS layer, so the browser shows the message instead
        of "Failed to fetch". Stack traces stay in the log."""
        try:
            return await call_next(request)
        except Exception as exc:                            # noqa: BLE001
            log.exception("request failed: %s %s", request.method, request.url.path)
            return JSONResponse(status_code=500, content={"detail": clean_error(exc)})

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
        """Role baselines (a recruiter job's own baseline is managed through the job, never listed here)."""
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
    async def onboard(candidate_id: str, baseline_id: str | None = Form(None), resume: UploadFile = File(...),
                      linkedin_pdf: UploadFile | None = File(None), github: str | None = Form(None),
                      portfolio_url: str | None = Form(None),
                      linkedin_url: str | None = Form(None, description="LinkedIn profile URL (display only)"),
                      demo_videos: str = Form("[]", description='JSON list of YouTube/Loom URLs (max 3)'),
                      certifications: str = Form("[]", description="JSON list of {name, issuer, date, "
                                                                    "credential_id, verify_url}"),
                      references: str = Form("[]", description="JSON list of {name, relationship, contact, "
                                                                "statement}"),
                      onboarding: bool = Form(False, description="First-run onboarding (specs/17): plain-language "
                                                                 "progress; target roles are picked while it runs")):
        """Read the documents and stop at review. Without a baseline (onboarding: roles are picked while this runs),
        the job-independent questions that start during review use the primary target role, or the first role
        baseline: they don't depend on the role, so any role baseline gives the same answers."""
        with conn() as c:
            cand = store.get_candidate(c, candidate_id) or not_found("Candidate")
            if baseline_id is None:
                from backend import candidate as cx
                baseline_id = (cx.primary(c, candidate_id) or {}).get("baseline_id") or next(iter(cx.role_baselines(c)))
            if db.get_baseline(c, baseline_id) is None:
                not_found("Baseline")
            from market.baseline import resolve_baseline
            baseline_id = resolve_baseline(c, baseline_id)          # the role's Market baseline when toggled on
        try:
            videos = json.loads(demo_videos or "[]")
            media.validate_links(videos)
            certs = [CertificationForm(**x) for x in json.loads(certifications or "[]")]
            refs = [ReferenceForm(**x) for x in json.loads(references or "[]")]
            from backend import linkedin as li
            profile_url = li.normalize(linkedin_url) if (linkedin_url or "").strip() else None
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
        from backend import wait_screens as ws               # the reading wait screen lists these (specs/17)
        added = ws.added_sources({"linkedin_pdf": inputs.linkedin_pdf, "github": inputs.github,
                                  "portfolio": inputs.portfolio_url})
        tasks.event(task_id, "queued", "Waiting to start", 0, result_json={"sources": added})
        if onboarding:
            from backend import candidate as cx
            with conn() as c:
                cx.set_step(c, candidate_id, "roles", read_task_id=task_id, confirmed_at=None)

        def work(progress):
            progress.say("ingest", "Reading your documents…", 2)
            if onboarding:
                from backend import candidate as cx
                on_event = cx.reading_progress(progress)
            else:
                on_event = progress.on_event
            res = state.pipeline.start_onboarding(inputs, baseline_id, on_event=on_event)
            with conn() as c:
                media.save_links(c, candidate_id, videos)
                if profile_url:
                    with c:
                        c.execute("UPDATE candidates SET linkedin_url = ? WHERE id = ?", (profile_url, candidate_id))
            if res.get("awaiting_review"):
                state.review_threads[candidate_id] = res["thread_id"]
                return {"_status": "done", "_message": "Ready for review", "candidate_id": candidate_id, "sources": added,
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

    # --- Candidate experience (specs/17) ---------------------------------------------------------------------
    from backend import candidate as cx

    def cand_or_404(c, candidate_id: str) -> dict:
        return store.get_candidate(c, candidate_id) or not_found("Candidate")

    def bad_request(fn, *args):
        try:
            return fn(*args)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

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
        from backend import wait_screens as ws
        with conn() as c:
            cand_or_404(c, candidate_id)
            ob = cx.onboarding(c, candidate_id)
            return ws.reading_view(c, candidate_id, tasks.get(ob["read_task_id"]) if ob.get("read_task_id") else None)

    @app.get("/candidates/{candidate_id}/scoring-progress", tags=["candidate"])
    def scoring_progress(candidate_id: str):
        """The scoring wait: per role, a status and a fill per dimension (no internal counts)."""
        from backend import wait_screens as ws
        with conn() as c:
            cand_or_404(c, candidate_id)
            ob = cx.onboarding(c, candidate_id)
            return ws.scoring_view(c, candidate_id, tasks.get(ob["score_task_id"]) if ob.get("score_task_id") else None)

    @app.get("/config/wait-tips", tags=["candidate"])
    def wait_tips():
        """"Good to know" tips for the two waits, from config/wait_tips.yaml."""
        from backend import wait_screens as ws
        return ws.tips()

    @app.put("/candidates/{candidate_id}/onboarding", tags=["candidate"])
    def put_onboarding(candidate_id: str, body: OnboardingStepIn):
        with conn() as c:
            cand_or_404(c, candidate_id)
            if body.step in ("review", "scores", "details", "done") and not cx.targets(c, candidate_id):
                raise HTTPException(409, "Pick at least one target role first.")
            return bad_request(cx.set_step, c, candidate_id, body.step)

    @app.put("/candidates/{candidate_id}/targets", tags=["candidate"])
    def put_targets(candidate_id: str, body: TargetsIn):
        """1–3 target roles, one primary (default: the first picked)."""
        with conn() as c:
            cand_or_404(c, candidate_id)
            return bad_request(cx.set_targets, c, candidate_id, body.baseline_ids, body.primary)

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
        from backend import my_applications as ma
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
    from backend import candidate_jobs as cj

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

    # --- Scoring ---------------------------------------------------------------------------------------
    def start_scoring(candidate_id: str, baseline_id: str, weights: dict | None, kind: str = "score",
                      application_id: str | None = None) -> str:
        """Background scoring. `application_id`: an application (Apply, or a job edit) that points at the run."""
        task_id = tasks.create(kind, candidate_id)

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
                from backend import recruiter
                with conn() as c:
                    recruiter.attach_run(c, res["run_id"])     # the candidate now applies to the role's job
                    if application_id:
                        from backend import jobs as jb
                        jb.link_run(c, application_id, res["run_id"])
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
            from market.baseline import resolve_baseline
            baseline_id = resolve_baseline(c, body.baseline_id)     # Manual or Market (specs/15 §9)
        if blocker:
            raise HTTPException(409, blocker)
        return {"task_id": start_scoring(candidate_id, baseline_id, body.weights)}

    def run_row(c, run_id: str) -> dict:
        row = c.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else not_found("Run")

    def run_view(c, run: dict, weights: dict) -> dict:
        scores = run_scores(c, run["id"])
        agg = rescore(c, run["id"], weights)
        subs = []
        for dim, names in config.SUBCATEGORIES.items():
            for s in names:
                # Not saved yet: still being judged while the run is running; an error once it has stopped.
                absent = "pending" if run["status"] == "running" else "error"
                v = scores.get(s, {"status": absent, "score": None, "details": {}, "guidance": None})
                subs.append({"key": s, "label": config.SUBCATEGORY_LABELS[s], "dimension": dim,
                             "status": v["status"], "score": v["score"], "guidance": v["guidance"],
                             "details": v["details"], "formula": FORMULAS.get(s),
                             "weight": weights["subcategories"][dim][s],
                             "effective_weight": agg.effective_weights.get(s, 0.0)})
        b = c.execute("SELECT market_version_id, title FROM baselines WHERE id = ?", (run["baseline_id"],)).fetchone()
        source = "manual"
        if b and b["market_version_id"]:
            v = c.execute("SELECT version FROM market_versions WHERE id = ?", (b["market_version_id"],)).fetchone()
            source = f"market v{v[0]}" if v else "market"
        return {"run": {k: run[k] for k in ("id", "candidate_id", "baseline_id", "status", "started_at",
                                            "finished_at", "logic_version", "plan_status")} | {"baseline_source": source},
                "progress": {"done": sum(1 for x in subs if x["status"] != "pending"), "total": len(subs)},
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

    @app.post("/runs/{run_id}/plan", tags=["plan"])
    def open_plan(run_id: str):
        """First open of a role's plan (specs/17 §3.1): write it now if it was deferred. {"status": "ready"} when it
        already exists, else {"task_id"} ("Preparing your plan…"). One plan call, recorded on the run."""
        with conn() as c:
            run = run_row(c, run_id)
            if current_plan(c, run_id) is not None:
                return {"status": "ready", "task_id": None}
            if run.get("plan_status") == "generating":
                return {"status": "generating", "task_id": None}
            db.set_plan_status(c, run_id, "generating")
        task_id = tasks.create("plan", run["candidate_id"], run_id)

        def work(progress):
            progress.say("gap_analysis", "Preparing your plan…", 20)
            from graph.pipeline import write_plan
            from graph.pipeline import run_scores as scores_of
            with conn() as c:
                subs = scores_of(c, run_id)
            plan = write_plan(state.deps, run_id, run["candidate_id"], run["baseline_id"], subs,
                              json.loads(run["weights_json"]))
            if plan.error:
                raise ValueError(f"The plan could not be prepared: {plan.error}")
            return {"run_id": run_id, "_message": "Plan ready"}
        tasks.submit(task_id, work)
        return {"status": "generating", "task_id": task_id}

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

    # --- Create, edit and publish jobs (specs/14) ------------------------------------------------------------
    from backend import jobs as jb

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
                data = await file.read()
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

    # --- Market baselines (specs/15) -------------------------------------------------------------------------
    from market import baseline as mb
    from market import normalize as mnorm
    from market import pipeline as mp
    from market import sources as msrc
    from market import store as mstore

    def role_or_404(key: str) -> msrc.Role:
        return msrc.load().get(key) or not_found("Role")

    def market_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/market/roles", tags=["market"])
    def market_roles():
        with conn() as c:
            out = []
            for key, r in msrc.load().items():
                rows = mstore.postings(c, key)
                vs = mb.versions(c, key)
                out.append({"key": key, "title": r.title, "manual_baseline": r.manual_baseline,
                            "source": mb.source_of(c, key), "active": next((v for v in vs if v["status"] == "published"), None),
                            "latest": vs[0] if vs else None, "postings": len(rows),
                            "levels": mstore.level_counts(c, key),
                            "companies": len({p["company"] for p in rows}),
                            "configured": {"companies": len(r.companies),
                                           "placeholders": sum(c2.placeholder for c2 in r.companies),
                                           "fetchable": sum(1 for c2 in r.companies if not c2.placeholder and
                                                            c2.source != "manual" and c2.source_id)}})
            return {"roles": out, "allow_test_versions": mb.allow_test_versions(c),
                    "thresholds": {"WINDOW_DAYS": config.WINDOW_DAYS, "REQ_SHARE": config.REQ_SHARE,
                                   "PREF_SHARE": config.PREF_SHARE, "MARGIN": config.MARGIN,
                                   "MIN_COMPANIES": config.MIN_COMPANIES, "MIN_POSTINGS": config.MIN_POSTINGS}}

    @app.get("/market/{role}/versions", tags=["market"])
    def market_versions(role: str):
        role_or_404(role)
        with conn() as c:
            return [{k: v for k, v in x.items() if k != "content"} for x in mb.versions(c, role)]

    @app.get("/market/{role}/postings", tags=["market"])
    def market_postings(role: str):
        role_or_404(role)
        with conn() as c:
            return [{k: p[k] for k in ("id", "company", "tier", "source", "title", "level", "level_method", "source_url",
                                       "first_seen", "last_seen", "posted_at")} | {"extracted": mstore.cached(c, p["text_hash"]) is not None}
                    for p in mstore.postings(c, role)]

    @app.post("/market/{role}/postings", tags=["market"])
    async def market_add(role: str, company: str = Form(...), tier: str = Form(...), title: str = Form(...),
                         url: str | None = Form(None), posted_at: str | None = Form(None), text: str | None = Form(None),
                         file: UploadFile | None = File(None)):
        """Add postings by hand (companies without a public API, and the first test postings)."""
        r = role_or_404(role)
        from ingest.documents import DocumentError, extract_text
        try:
            if file is not None and file.filename:
                with conn() as c:
                    text = extract_text(file.filename, await file.read(), c)
        except DocumentError as exc:
            raise HTTPException(422, str(exc)) from exc
        with conn() as c:
            return market_call(mp.add_manual, c, r, company=company, tier=tier, title=title, text=text or "", url=url,
                               posted_at=posted_at)

    @app.post("/market/{role}/fetch", tags=["market"])
    def market_fetch(role: str):
        """Read the role's public job boards now (normally weekly). No model calls."""
        r = role_or_404(role)
        with conn() as c:
            return mp.fetch_role(c, r)

    @app.post("/market/{role}/recompute", tags=["market"])
    async def market_recompute(role: str, body: RecomputeIn | None = None):
        """A new draft version over the window: extraction of new postings (cached), aggregation, one summary."""
        r = role_or_404(role)

        def work():
            with conn() as c:
                return mb.recompute(c, r, llm=state.job_llm, test=bool(body and body.test))
        return await asyncio.to_thread(work)

    @app.post("/market/{role}/fixtures", tags=["market"])
    def market_fixtures(role: str):
        """Test mode: load the hand-labelled fixture postings for the role (no model)."""
        r = role_or_404(role)
        with conn() as c:
            return {"added": mp.load_fixtures(c, r)}

    @app.get("/market/versions/{vid}", tags=["market"])
    def market_version(vid: str):
        with conn() as c:
            v = mb.get_version(c, vid) or not_found("Version")
            r = msrc.load()[v["role"]]
            return {**v, "diff": mb.diff(c, vid), "side_by_side": mb.side_by_side(c, r, vid), "role_title": r.title,
                    "can_publish": (not v["insufficient"] or mb.allow_test_versions(c)) and v["status"] == "draft"}

    @app.post("/market/versions/{vid}/publish", tags=["market"])
    def market_publish(vid: str):
        """Nothing goes live without a person publishing it."""
        with conn() as c:
            mb.get_version(c, vid) or not_found("Version")
            return market_call(mb.publish, c, vid)

    @app.post("/market/versions/{vid}/discard", tags=["market"])
    def market_discard(vid: str):
        with conn() as c:
            return mb.discard(c, vid)

    @app.get("/market/suggestions", tags=["market"])
    def market_suggestions(status: str | None = "pending"):
        with conn() as c:
            return {"suggestions": mnorm.suggestions(c, status),
                    "canonical": sorted(mnorm.canonicals(mnorm.load_map()))}

    @app.post("/market/suggestions/{sid}", tags=["market"])
    def market_decide(sid: str, body: DecideIn):
        """A person approves (merge into the canonical name; written to the reviewed map) or rejects."""
        with conn() as c:
            return market_call(mnorm.decide, c, sid, body.approve, body.canonical)

    @app.post("/market/{role}/source", tags=["market"])
    def market_source(role: str, body: SourceIn):
        """The toggle. 409 with "Switching will re-score N candidates (about M model calls)" unless confirmed."""
        r = role_or_404(role)
        with conn() as c:
            est = mb.switch_estimate(c, r, body.source)
            if not est["possible"]:
                raise HTTPException(422, est["reason"])
            if est["candidates"] and not body.confirm:
                raise HTTPException(409, est)
            mb.set_setting(c, f"source:{role}", body.source)
            return {"source": body.source, **est}

    @app.put("/market/settings", tags=["market"])
    def market_settings(body: MarketSettingsIn):
        with conn() as c:
            mb.set_setting(c, "allow_test_versions", "1" if body.allow_test_versions else "0")
            return {"allow_test_versions": body.allow_test_versions}

    @app.get("/runs/{run_id}/baselines", tags=["market"])
    def run_baselines(run_id: str):
        """The Score page's "Scored against: Standard | Market (vN, date)" switch."""
        with conn() as c:
            return mb.run_options(c, run_row(c, run_id))

    @app.post("/runs/{run_id}/switch-baseline", tags=["market"])
    def switch_baseline(run_id: str, body: SwitchBaselineIn):
        """Re-score this candidate against the other baseline. 409 with the re-score notice unless confirmed."""
        with conn() as c:
            run = run_row(c, run_id)
            opts = mb.run_options(c, run)
            target = (opts.get(body.source) or {}).get("baseline_id")
            if target is None:
                raise HTTPException(422, "No published Market baseline for this role yet.")
            if target == run["baseline_id"]:
                raise HTTPException(422, "This score already uses that baseline.")
            est = mb.candidate_switch_estimate(c, run["candidate_id"], run["baseline_id"], target)
            if not body.confirm:
                label = "the Standard baseline" if body.source == "standard" else opts["market"]["label"]
                raise HTTPException(409, {"target": label, **est})
        task_id = start_scoring(run["candidate_id"], target, json.loads(run["weights_json"]), kind="rescore")
        return {"task_id": task_id}

    @app.get("/market-view", tags=["market"])
    def market_view_roles():
        """Candidate side: roles with a published Market version (counts only)."""
        with conn() as c:
            return [{"role": k, "title": r.title, "available": mb.active(c, k) is not None} for k, r in msrc.load().items()]

    @app.get("/market-view/{role}", tags=["market"])
    def market_view(role: str):
        r = role_or_404(role)
        with conn() as c:
            return mb.market_view(c, r) or not_found("Published Market version")

    @app.get("/market/dry-run", tags=["market"])
    def market_dry_run():
        with conn() as c:
            return mp.dry_run(c, msrc.load())

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

    @app.post("/applications/{app_id}/opened", status_code=204, tags=["recruiter"])
    def panel_opened(app_id: str):
        """The recruiter opened this applicant's panel: an activity signal for the job (specs/17 §4.1)."""
        from backend import candidate_jobs as cj
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
        """Board columns; each card carries its workflow status (Needs your action, Scheduled …)."""
        from backend import hiring
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

    # --- Hiring workflow (specs/16): one per application; every email and decision waits for the recruiter ----
    from backend import hiring as hr
    from backend import replies as rp

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
        """Whether replies are read from the demo inbox, and the last poll (also in the API log: "inbox:")."""
        last = getattr(state._hiring, "last_poll", None) if state._hiring else None
        return {"enabled": config.REPLY_INBOX == "imap", "every_minutes": config.IMAP_POLL_MINUTES,
                "user": config.IMAP_USER or None, "last_poll": last}

    @app.get("/recruiter/tasks", tags=["hiring"])
    def recruiter_tasks():
        """Everything waiting for the recruiter across jobs, oldest first, including applications waiting longer than
        CLOSURE_DAYS for an answer (specs/17 §7: a reminder only; nothing is sent)."""
        from backend import my_applications as ma
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

