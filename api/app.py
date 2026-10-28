"""FastAPI backend (specs/10 §2) around the pipeline, scoring and database.

Run: `.venv/bin/uvicorn api.app:create_app --factory --reload --port 8000` (or `make dev`). OpenAPI docs at /docs.
Routes live in api/routers/ by area; create_app registers them in a fixed order (where two paths can match the same URL,
the more specific one comes first: /jobs/templates before /jobs/{job_id}, and the market routes keep their order).
"""
from __future__ import annotations

import logging
import re

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.context import Api
from api.routers import (admin, baselines, candidate, candidates, emails, hiring, jobs, market, ops, recruiter, scoring,
                         tasks as task_routes, workflows)
from api.state import State
from core import config, db
from graph.context import Deps
from services.tasks import TaskStore, clean_error

log = logging.getLogger("rightstaff.api")

ROUTERS = (ops, baselines, candidates, admin, candidate, scoring, task_routes, jobs, market, recruiter, workflows,
           hiring, emails)


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


UPLOAD_PATHS = re.compile(r"^/candidates/[^/]+/onboard$|^/jobs/drafts/read$|^/market/[^/]+/postings(/from-url)?$")


class UploadRateLimit:
    """Per-address upload limit (deploy item 6): at most UPLOAD_RATE_PER_HOUR upload requests per client address in
    any hour, kept in memory (the API runs as one process). Behind the proxy the address is the first X-Forwarded-For
    entry (TRUST_PROXY=1); otherwise the socket's peer."""

    def __init__(self, per_hour: int, clock=None):
        import threading
        import time
        self.per_hour, self.clock = per_hour, clock or time.time
        self.seen: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def address(request: Request) -> str:
        fwd = request.headers.get("x-forwarded-for", "") if config.TRUST_PROXY else ""
        return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")

    def wait_seconds(self, addr: str) -> int:
        """0 and counted when allowed; else seconds until the oldest upload in the window ages out."""
        now = self.clock()
        with self._lock:
            hits = [t for t in self.seen.get(addr, []) if now - t < 3600]
            if len(hits) >= self.per_hour:
                self.seen[addr] = hits
                return int(3600 - (now - hits[0])) + 1
            hits.append(now)
            self.seen[addr] = hits
            return 0


def create_app(deps: Deps | None = None) -> FastAPI:
    from contextlib import asynccontextmanager
    if config.REQUIRE_DEMO_EMAIL_TO and not config.DEMO_EMAIL_TO:
        raise RuntimeError("DEMO_EMAIL_TO must be set: every email goes to that address only (REQUIRE_DEMO_EMAIL_TO=1).")
    state = State(deps)
    uploads = UploadRateLimit(config.UPLOAD_RATE_PER_HOUR)

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

    @app.middleware("http")
    async def upload_rate_limit(request: Request, call_next):
        if request.method == "POST" and config.UPLOAD_RATE_PER_HOUR > 0 and UPLOAD_PATHS.match(request.url.path):
            wait = uploads.wait_seconds(UploadRateLimit.address(request))
            if wait:
                return JSONResponse(status_code=429, headers={"Retry-After": str(wait)}, content={
                    "detail": f"Too many uploads from this address. Try again in {max(1, round(wait / 60))} min."})
        return await call_next(request)

    app.add_middleware(CORSMiddleware, allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
                       allow_methods=["*"], allow_headers=["*"])     # local mock: any local web port
    app.state.rs = state
    tasks = TaskStore(state.db_path)
    app.state.tasks = tasks
    conn0 = db.init_app_db(state.db_path)
    conn0.close()

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):           # stack traces stay in the log
        log.exception("request failed: %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": clean_error(exc)})

    api = Api(app, state, tasks)
    for router in ROUTERS:
        router.register(api)
    return app
