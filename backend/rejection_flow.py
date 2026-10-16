""""Not moving forward" as a small LangGraph graph with a human review step (specs/13 §2).

    START ─┬─ build_reasons (code) → draft_email (model) → validate (code) ─┬─→ review (interrupt) → send → log → END
           │                              ↑____ one redraft on failed checks ┘                         ↑
           └─ (a saved draft) ──────────────────────────────────────────────────→ review ──────────────┘

- draft_email asks EMAIL_MODEL; on LLMError (unavailable, over quota) validate uses the code template.
- validate runs the code checks; a failing draft is redrafted once with the errors, then the template is used.
- review pauses the graph. Only the recruiter's Send (resume {"action": "send"}) reaches send; "cancel" ends it.
- Paused drafts survive a server restart: graphs are checkpointed to SQLite (LangGraph SqliteSaver) next to the app
  database. If a checkpoint is missing (e.g. the file was deleted), a run started with the saved email_id goes
  straight to review.
"""
from __future__ import annotations

from typing import TypedDict

import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend import workflows as wf
from core import config, db
from core.llm import LLMError
from backend import recruiter as rc


class State(TypedDict, total=False):
    application_id: str
    reason_ids: list[str] | None         # None = code's pre-selection
    note: str | None
    reasons: list[dict]
    draft: dict | None                   # RejectionDraft as a dict
    model: str
    attempts: int
    errors: list[str]
    model_errors: list[str]
    email_id: str
    result: dict


def checkpointer(db_path: str) -> SqliteSaver:
    """Graph checkpoints in "<app db>.graphs.db" (gitignored with the other *.db files)."""
    path = Path(db_path).with_suffix(".graphs.db")
    return SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))


class RejectionFlow:
    def __init__(self, db_path: str, writer, saver: SqliteSaver | None = None):
        self.db_path, self.writer = db_path, writer
        self.graph = self._build().compile(checkpointer=saver or checkpointer(db_path))

    # --- nodes -----------------------------------------------------------------------------------
    def _ctx(self, state: State):
        conn = db.connect(self.db_path)
        app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (state["application_id"],)).fetchone())
        return conn, app, rc.get_job(conn, app["job_id"])

    def build_reasons(self, state: State) -> dict:
        conn, app, job = self._ctx(state)
        reasons = wf.build_reasons(conn, app, job)
        if state.get("reason_ids") is not None:
            ids = set(state["reason_ids"])
            reasons = [{**r, "selected": r["id"] in ids} for r in reasons]
        return {"reasons": reasons, "attempts": 0, "errors": [], "model_errors": []}

    def draft_email(self, state: State) -> dict:
        selected = [r for r in state["reasons"] if r["selected"]]
        if self.writer is None or not selected:
            return {"draft": None, "model": wf.TEMPLATE_MODEL}
        conn, _, job = self._ctx(state)
        try:
            d = wf.ask_rejection(self.writer, job, db.get_baseline(conn, job["baseline_id"]), selected,
                                 state.get("note"), state.get("errors"))
        except LLMError as exc:
            return {"draft": None, "model": wf.TEMPLATE_MODEL, "model_errors": [f"model unavailable: {exc}"]}
        return {"draft": d.model_dump(), "model": config.EMAIL_MODEL, "attempts": state.get("attempts", 0) + 1}

    def validate(self, state: State) -> Command:
        conn, app, job = self._ctx(state)
        selected = [r for r in state["reasons"] if r["selected"]]
        inp = wf.rejection_inputs(conn, app, job)
        if state.get("draft"):
            d = wf.RejectionDraft(**state["draft"])
            errors = wf.check_rejection(d, wf.rejection_body(inp["name"], job, inp["settings"], d), selected,
                                        inp["others"])
            if not errors:
                email = wf.save_rejection(conn, app, job, state["reasons"], state.get("note"), d, state["model"],
                                          state.get("model_errors", []))
                return Command(goto="review", update={"email_id": email["id"]})
            if state.get("attempts", 0) < 2:
                return Command(goto="draft_email", update={"errors": errors,
                                                           "model_errors": [*state.get("model_errors", []), *errors]})
        d = wf.rejection_template(job, selected)
        email = wf.save_rejection(conn, app, job, state["reasons"], state.get("note"), d, wf.TEMPLATE_MODEL,
                                  state.get("model_errors", []))
        return Command(goto="review", update={"email_id": email["id"]})

    def review(self, state: State) -> Command:
        answer = interrupt({"email_id": state["email_id"]})
        if (answer or {}).get("action") == "send":
            return Command(goto="send")
        conn = db.connect(self.db_path)
        return Command(goto=END, update={"result": wf.cancel_email(conn, state["email_id"])})

    def send(self, state: State) -> dict:
        conn = db.connect(self.db_path)
        return {"result": wf.send_email(conn, state["email_id"])}

    def log(self, state: State) -> dict:
        conn, app, _ = self._ctx(state)
        sent = state["result"]
        if sent["status"] == "sent":
            terminal = next(s for s in wf.stages(conn, app["job_id"]) if s["terminal"])
            wf.move(conn, app, terminal["id"], reason="decision email sent")
            from backend import hiring
            hiring.close(conn, app["id"], "not_moving_forward")         # an open hiring workflow ends here
            wf.log_activity(conn, app, "decision", "Decision: not moving forward",
                            {"email_id": sent["id"], "reasons": [r["title"] for r in (sent["reasons"] or {}).get(
                                "reasons", []) if r.get("selected")]})
        return {}

    def _build(self) -> StateGraph:
        g = StateGraph(State)
        g.add_node("build_reasons", self.build_reasons)
        g.add_node("draft_email", self.draft_email)
        g.add_node("validate", self.validate, destinations=("review", "draft_email"))
        g.add_node("review", self.review, destinations=("send", END))
        g.add_node("send", self.send)
        g.add_node("log", self.log)
        g.add_conditional_edges(START, lambda s: "review" if s.get("email_id") else "build_reasons",
                                ["review", "build_reasons"])
        g.add_edge("build_reasons", "draft_email")
        g.add_edge("draft_email", "validate")
        g.add_edge("send", "log")
        g.add_edge("log", END)
        return g

    # --- API ------------------------------------------------------------------------------------------
    def draft(self, application_id: str, reason_ids: list[str] | None = None, note: str | None = None) -> dict:
        """Run to the review step. Returns the saved draft and the reasons (nothing is sent)."""
        thread = {"configurable": {"thread_id": f"reject:{application_id}"}}
        out = self.graph.invoke({"application_id": application_id, "reason_ids": reason_ids, "note": note,
                                 "email_id": None, "draft": None}, thread)
        conn = db.connect(self.db_path)
        return {"email": wf.get_email(conn, out["email_id"]), "reasons": out["reasons"]}

    def decide(self, email_id: str, action: str) -> dict:
        """Resume at review with the recruiter's action: "send" or "cancel"."""
        conn = db.connect(self.db_path)
        email = wf.get_email(conn, email_id)
        if email is None or email["kind"] != "not_moving_forward" or email["status"] != "draft":
            raise ValueError("Only a not-moving-forward draft can be sent or cancelled here.")
        thread = {"configurable": {"thread_id": f"reject:{email['application_id']}"}}
        snapshot = self.graph.get_state(thread)
        if not snapshot.interrupts or self._paused_email(snapshot) != email_id:
            # The server restarted (or another draft replaced this one): start at review for this saved draft.
            thread = {"configurable": {"thread_id": f"reject:{email['application_id']}:{email_id}"}}
            self.graph.invoke({"application_id": email["application_id"], "email_id": email_id}, thread)
        out = self.graph.invoke(Command(resume={"action": action}), thread)
        return out["result"]

    @staticmethod
    def _paused_email(snapshot) -> str | None:
        return next((i.value.get("email_id") for i in snapshot.interrupts if isinstance(i.value, dict)), None)
