"""Live view of the LangGraph pipeline for demos: which node is running, done, waiting or failed.

Feed it TaskEvents (Pipeline(..., on_event=view.on_event)); it redraws a Graphviz diagram
in a Streamlit placeholder. Pure DOT text: st.graphviz_chart renders it in the browser.
"""
from __future__ import annotations

import time

import streamlit as st

from core import config
from graph.pipeline import DIMENSIONS, TaskEvent

TOP_NODES = [
    ("ingest", "Read documents"),
    ("extract", "Extract (LLM)"),
    ("validate", "Validate quotes, dates,\nmerge sources"),
    ("review", "Human review"),
    ("prepare_match", "Create run ·\njudge relevance"),
    ("aggregate", "Aggregate scores\n(code)"),
    ("gap_analysis", "Rank gaps ·\nimprovement plan"),
]
STEP_TEXT = {"judge": "judging…", "compute": "computing…", "save": "saving…"}
STATUS_NOTES = {"na": "N/A", "missing": "missing evidence", "error": "error"}
COLORS = {
    "pending": ("#f3f4f6", "#9ca3af"), "running": ("#fde68a", "#b45309"), "done": ("#bbf7d0", "#15803d"),
    "scored": ("#bbf7d0", "#15803d"), "missing": ("#fed7aa", "#c2410c"), "na": ("#e5e7eb", "#6b7280"),
    "error": ("#fecaca", "#b91c1c"), "waiting": ("#bfdbfe", "#1d4ed8"), "skipped": ("#ffffff", "#d1d5db"),
}
LEGEND = "🟨 running · 🟩 scored/done · 🟧 missing evidence · ⬜ N/A or waiting · 🟥 error · 🟦 your review"


class GraphView:
    def __init__(self, placeholder=None, min_interval: float = 0.25):
        self.placeholder = placeholder
        self.min_interval = min_interval
        self.state: dict[str, str] = {}          # node or subcategory → status
        self.step: dict[str, str] = {}           # subcategory → current step text
        self.started = time.perf_counter()
        self._last_draw = 0.0
        self.finished = False

    # --- events -------------------------------------------------------------------------------
    def on_event(self, e: TaskEvent) -> None:
        if not e.path:                                   # top-level node or dimension subgraph
            if e.node == "review" and e.kind == "end" and e.interrupted:
                self.state["review"] = "waiting"
            elif e.kind == "start":
                self.state[e.node] = "running"
            else:
                self.state[e.node] = "error" if e.error else "done"
        elif len(e.path) == 1:                           # subcategory subgraph inside a dimension
            if e.kind == "start":
                self.state[e.node] = "running"
            elif e.error:
                self.state[e.node] = "error"
        elif len(e.path) == 2:                           # judge / compute / save inside a subcategory
            sub = e.path[1]
            if e.kind == "start":
                self.state[sub] = "running"
                self.step[sub] = STEP_TEXT.get(e.node, e.node)
            elif e.error:
                self.state[sub] = "error"
            elif e.node == "compute" and e.status:
                self.state[f"{sub}:result"] = e.status
            elif e.node == "save":
                self.state[sub] = self.state.get(f"{sub}:result", "done")
                self.step.pop(sub, None)
        self.draw()

    def finish(self) -> None:
        """Mark everything that never ran as skipped and draw the final picture."""
        self.finished = True
        self.draw(force=True)

    # --- drawing -----------------------------------------------------------------------------
    def _status(self, key: str) -> str:
        s = self.state.get(key)
        if s is None:
            return "skipped" if self.finished else "pending"
        return s

    def _node(self, key: str, label: str) -> str:
        status = self._status(key)
        fill, line = COLORS.get(status, COLORS["pending"])
        style = "rounded,filled,dashed" if status == "skipped" else "rounded,filled"
        return (f'"{key}" [label="{label}", shape=box, style="{style}", fillcolor="{fill}", color="{line}", '
                f'penwidth={2 if status == "running" else 1}, fontname="Helvetica", fontsize=11];')

    def dot(self) -> str:
        """Top row: onboarding chain. Middle: one column per dimension. Bottom row: aggregate → plan."""
        lines = ["digraph G {", 'rankdir=TB; compound=true; nodesep=0.25; ranksep=0.35; bgcolor="transparent";',
                 'newrank=true; node [fontname="Helvetica"]; edge [color="#9ca3af", arrowsize=0.6];']
        for key, label in TOP_NODES:
            lines.append(self._node(key, label))
        chain = ["ingest", "extract", "validate", "review", "prepare_match"]
        lines.append("{ rank=same; " + " ".join(f'"{k}";' for k in chain) + " }")
        lines.append('{ rank=same; "aggregate"; "gap_analysis"; }')
        for dim, subs in DIMENSIONS.items():
            fill, line = COLORS.get(self._status(dim), COLORS["pending"])
            lines.append(f'subgraph cluster_{dim} {{ label="{dim.title()}"; style="rounded,filled"; '
                         f'fillcolor="#fafafa"; color="{line}"; fontname="Helvetica"; fontsize=12;')
            for sub in subs:
                name = config.SUBCATEGORY_LABELS[sub.name]
                step = self.step.get(sub.name)
                status = self._status(sub.name)
                note = step if status == "running" and step else STATUS_NOTES.get(status, "")
                lines.append(self._node(sub.name, name + (f"\\n{note}" if note else "")))
            names = [s.name for s in subs]                      # stack the column
            lines += [f'"{x}" -> "{y}" [style=invis];' for x, y in zip(names, names[1:])]
            lines.append("}")
        lines += [f'"{x}" -> "{y}";' for x, y in zip(chain, chain[1:])]
        for dim, subs in DIMENSIONS.items():
            lines.append(f'"prepare_match" -> "{subs[0].name}" [lhead=cluster_{dim}];')
            lines.append(f'"{subs[-1].name}" -> "aggregate" [ltail=cluster_{dim}];')
        lines.append('"aggregate" -> "gap_analysis";')
        lines.append("}")
        return "\n".join(lines)

    def counts(self) -> dict[str, int]:
        subs = [s.name for subs in DIMENSIONS.values() for s in subs]
        out = {"done": 0, "running": 0, "pending": 0}
        for s in subs:
            st_ = self._status(s)
            out["running" if st_ == "running" else "pending" if st_ in ("pending", "skipped") else "done"] += 1
        return out

    def draw(self, force: bool = False) -> None:
        if self.placeholder is None:
            return
        now = time.perf_counter()
        if not force and now - self._last_draw < self.min_interval:
            return
        self._last_draw = now
        c = self.counts()
        with self.placeholder.container():
            st.graphviz_chart(self.dot(), use_container_width=True)
            st.caption(f"{LEGEND}  ·  subcategories: {c['done']}/19 done, {c['running']} running  ·  "
                       f"{now - self.started:.1f}s")


def show_last_run() -> None:
    """Static picture of the most recent run in this browser session."""
    view = st.session_state.get("last_graph")
    if view is not None:
        with st.expander("Pipeline view of the last run", expanded=False):
            view.placeholder = st.empty()
            view.draw(force=True)
