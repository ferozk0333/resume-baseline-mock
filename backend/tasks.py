"""Background tasks for the API (specs/10 §2): progress is stored in SQLite and streamed over SSE.

A task runs in the shared background pool. Its function receives a `Progress` and reports steps;
every report is one row in `task_events`, read by `/tasks/{id}/events`.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

from core import db, timing
from core.db import new_id, now_iso
from core.llm import LLMError
from graph.pipeline import STEP_LABELS, TaskEvent, describe
from ingest.documents import DocumentError

log = logging.getLogger("rightstaff.api")

POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rightstaff-api")
SUBCATEGORY_COUNT = 19
USER_FACING = (LLMError, DocumentError, ValueError)


def clean_error(exc: BaseException) -> str:
    """A message for the user; the stack trace goes to the server log only."""
    if isinstance(exc, USER_FACING):
        return str(exc)
    return f"Something went wrong ({exc.__class__.__name__}). The details are in the server log."


class TaskStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self.futures: dict[str, Future] = {}

    def _conn(self):
        return db.connect(self.db_path)

    def create(self, kind: str, candidate_id: str | None = None, run_id: str | None = None) -> str:
        task_id = new_id()
        conn = self._conn()
        try:
            with conn:
                conn.execute("INSERT INTO tasks (id, kind, candidate_id, run_id, status, step, message, percent, "
                             "created_at, updated_at) VALUES (?, ?, ?, ?, 'running', 'queued', 'Waiting to start', 0, ?, ?)",
                             (task_id, kind, candidate_id, run_id, now_iso(), now_iso()))
        finally:
            conn.close()
        return task_id

    def event(self, task_id: str, step: str, message: str, percent: float | None = None,
              status: str = "running", **fields) -> None:
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM task_events WHERE task_id = ?",
                                       (task_id,)).fetchone()[0]
                    conn.execute("INSERT INTO task_events (task_id, seq, step, message, percent, status, created_at) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (task_id, seq, step, message, percent, status, now_iso()))
                    sets = ["step = ?", "message = ?", "status = ?", "updated_at = ?"]
                    params = [step, message, status, now_iso()]
                    if percent is not None:
                        sets.append("percent = ?")
                        params.append(percent)
                    for k, v in fields.items():
                        sets.append(f"{k} = ?")
                        params.append(json.dumps(v) if k == "result_json" else v)
                    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", (*params, task_id))
            finally:
                conn.close()

    def get(self, task_id: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            out = dict(row)
            out["result"] = json.loads(out.pop("result_json") or "null")
            return out
        finally:
            conn.close()

    def events_after(self, task_id: str, seq: int) -> list[dict]:
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM task_events WHERE task_id = ? AND seq > ? ORDER BY seq",
                                                  (task_id, seq))]
        finally:
            conn.close()

    def submit(self, task_id: str, fn) -> None:
        """Run fn(progress) in the background; its return value (a dict) becomes the task result."""
        def run():
            progress = Progress(self, task_id)
            try:
                result = fn(progress) or {}
                status = result.pop("_status", "done")
                message = result.pop("_message", "Finished")
                self.event(task_id, status, message, 100 if status == "done" else progress.percent, status=status,
                           result_json=result, run_id=result.get("run_id"))
            except Exception as exc:  # noqa: BLE001 - every failure ends the task with a message
                log.exception("task %s failed", task_id)
                self.event(task_id, "error", clean_error(exc), progress.percent, status="error", error=clean_error(exc))
        self.futures[task_id] = POOL.submit(timing.bind(run))

    def wait(self, task_id: str, timeout: float = 120) -> dict | None:
        fut = self.futures.get(task_id)
        if fut is not None:
            fut.result(timeout)
        return self.get(task_id)


class Progress:
    """Turns graph events into progress lines: steps, and "Scoring 7/19 subcategories"."""

    ONBOARD_PERCENT = {"ingest": 10, "extract": 70, "validate": 95}

    def __init__(self, store: TaskStore, task_id: str):
        self.store, self.task_id = store, task_id
        self.percent = 0.0
        self.scored = 0

    def say(self, step: str, message: str, percent: float | None = None) -> None:
        if percent is not None:
            self.percent = percent
        self.store.event(self.task_id, step, message, self.percent)

    def on_event(self, e: TaskEvent) -> None:
        if not e.path and e.kind == "start" and e.node in STEP_LABELS and e.node != "review":
            self.say(e.node, STEP_LABELS[e.node] + "…")
        elif not e.path and e.kind == "end" and e.node in self.ONBOARD_PERCENT and not e.error:
            self.say(e.node, f"{STEP_LABELS[e.node]}: done", self.ONBOARD_PERCENT[e.node])
        elif len(e.path) == 2 and e.node == "save" and e.kind == "end":
            self.scored += 1
            line = describe(e.path, "save") or ""
            self.say("score", f"Scoring {self.scored}/{SUBCATEGORY_COUNT} · {line}",
                     5 + 85 * self.scored / SUBCATEGORY_COUNT)
        elif not e.path and e.node == "aggregate" and e.kind == "end":
            self.say("aggregate", "Combining scores", 95)
