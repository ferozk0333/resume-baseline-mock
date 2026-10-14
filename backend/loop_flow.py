""""Start interview loop" as a small LangGraph graph with a human review step (specs/13 §3).

    START → draft (model per candidate, code-checked, template fallback) → review (interrupt) → apply ─┐
                                                                             ↑ while drafts are pending ┘

The recruiter edits drafts outside the graph (saved on the email rows), then resumes review with
{"action": "send" | "remove", "email_ids": [...]}. Sending moves each candidate to Scheduling. Checkpointed to
SQLite, so a paused loop survives a server restart.
"""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from backend import recruiter as rc
from backend import workflows as wf
from backend.rejection_flow import checkpointer
from core import db
from core.db import new_id


class State(TypedDict, total=False):
    job_id: str
    application_ids: list[str]
    batch_id: str
    email_ids: list[str]
    decision: dict


class LoopFlow:
    def __init__(self, db_path: str, writer, saver=None):
        self.db_path, self.writer = db_path, writer
        self.graph = self._build().compile(checkpointer=saver or checkpointer(db_path))

    def _pending(self, conn, state: State) -> list[str]:
        return [e["id"] for e in wf.list_emails(conn, batch_id=state["batch_id"]) if e["status"] == "draft"]

    def draft(self, state: State) -> dict:
        conn = db.connect(self.db_path)
        job = rc.get_job(conn, state["job_id"])
        stage, apps = wf.loop_targets(conn, job, state["application_ids"])
        ids = [wf.draft_scheduling(conn, a, job, stage, self.writer, state["batch_id"])["id"] for a in apps]
        return {"email_ids": ids}

    def review(self, state: State) -> dict:
        conn = db.connect(self.db_path)
        return {"decision": interrupt({"batch_id": state["batch_id"], "pending": self._pending(conn, state)})}

    def apply(self, state: State) -> Command:
        conn = db.connect(self.db_path)
        d = state.get("decision") or {}
        mine = set(self._pending(conn, state))
        for eid in d.get("email_ids", []):
            if eid not in mine:
                continue
            if d.get("action") == "send":
                wf.send_scheduling(conn, eid)
            elif d.get("action") == "remove":
                wf.cancel_email(conn, eid)
        return Command(goto="review" if self._pending(conn, state) else END)

    def _build(self) -> StateGraph:
        g = StateGraph(State)
        g.add_node("draft", self.draft)
        g.add_node("review", self.review)
        g.add_node("apply", self.apply, destinations=("review", END))
        g.add_edge(START, "draft")
        g.add_edge("draft", "review")
        g.add_edge("review", "apply")
        return g

    @staticmethod
    def _thread(batch_id: str) -> dict:
        return {"configurable": {"thread_id": f"loop:{batch_id}"}}

    def start(self, job: dict, application_ids: list[str]) -> dict:
        conn = db.connect(self.db_path)
        stage, _ = wf.loop_targets(conn, job, application_ids)          # validate before drafting
        batch = new_id()
        self.graph.invoke({"job_id": job["id"], "application_ids": application_ids, "batch_id": batch},
                          self._thread(batch))
        return {"batch_id": batch, "stage": stage, "drafts": wf.list_emails(conn, batch_id=batch)}

    def paused(self, batch_id: str) -> bool:
        return bool(self.graph.get_state(self._thread(batch_id)).interrupts)

    def decide(self, batch_id: str, action: str, email_ids: list[str]) -> list[dict]:
        """Resume the paused loop: send or remove these drafts. Returns them afterwards."""
        if not self.paused(batch_id):
            raise ValueError("This interview loop is not waiting for review (all drafts sent or removed).")
        self.graph.invoke(Command(resume={"action": action, "email_ids": email_ids}), self._thread(batch_id))
        conn = db.connect(self.db_path)
        return [wf.get_email(conn, eid) for eid in email_ids]
