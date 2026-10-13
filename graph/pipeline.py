"""LangGraph pipeline (specs/02).

START ─┬─ ingest → extract → validate → review (interrupt; human confirms) ─┐
       └────────────────────────────────────────── (scoring-only run) ───────┴─ prepare_match
prepare_match → [experience | expertise | authority | trust] (parallel subgraphs) → aggregate → gap_analysis → END

Each dimension is a subgraph whose subcategories run in parallel; each subcategory is
a subgraph judge → compute → save. Judging goes through the judgment cache; compute
reads saved judgments only, so it can never call a model. One failing judgment or
subcategory never crashes the run: it becomes `error` and is excluded (specs/04).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Annotated, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from core import config, db, timing
from core.db import new_id, now_iso
from extraction import schemas, store
from extraction.extract import extract_all, output_type
from extraction.onboard import (
    NO_RESUME_MESSAGE, CertificationForm, OnboardingInputs, ReferenceForm, Upload, docs_to_extract,
    finish_onboarding, form_profile, ingest, validate_results,
)
from extraction.records import scoring_blocker
from extraction.validate import SourceDoc
from graph.context import Deps, open_ctx, resolve
from graph.nodes import authority, experience, expertise, trust
from graph.nodes.base import Subcategory
from graph.nodes.prepare import relevance_asks
from ingest.github import Repo
from judges.service import judge_models
from scoring.aggregate import Aggregate, aggregate
from scoring.formulas import SubScore

log = logging.getLogger(__name__)

DIMENSIONS: dict[str, list[Subcategory]] = {
    "experience": experience.SUBCATEGORIES,
    "expertise": expertise.SUBCATEGORIES,
    "authority": authority.SUBCATEGORIES,
    "trust": trust.SUBCATEGORIES,
}


# --- State -------------------------------------------------------------------------------

def add_unique(a: list | None, b: list | None) -> list:
    """Append without duplicates: subgraphs may hand back errors they received."""
    a = list(a or [])
    return a + [x for x in (b or []) if x not in a]


def merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class RunState(TypedDict, total=False):
    run_id: str
    candidate_id: str
    baseline_id: str
    weights: dict                                         # snapshot used for this run
    errors: Annotated[list[str], add_unique]
    subcategory_scores: Annotated[dict, merge]            # subcategory -> {status, score, details, guidance}
    # onboarding (plain data only, so the checkpointer can store it)
    inputs: dict
    docs: list[dict]
    extractions: dict                                     # evidence_id -> {"ok": dump} | {"error": msg}
    warnings: list[str]
    blocked: str | None
    review: dict | None
    # results
    dimension_scores: dict
    final: float | None
    ranked_gaps: list[dict]
    plan_markdown: str | None
    plan_error: str | None


class ScoreOut(TypedDict, total=False):
    """What a dimension/subcategory subgraph hands back: reducer keys only (safe in parallel)."""
    errors: Annotated[list[str], add_unique]
    subcategory_scores: Annotated[dict, merge]


# --- Plain-data conversions for onboarding state ---------------------------------------

def inputs_to_state(inputs: OnboardingInputs) -> dict:
    return asdict(inputs)


def inputs_from_state(d: dict) -> OnboardingInputs:
    up = lambda u: Upload(**u) if u else None  # noqa: E731
    return OnboardingInputs(
        resume=up(d.get("resume")), linkedin_pdf=up(d.get("linkedin_pdf")), github=d.get("github"),
        portfolio_url=d.get("portfolio_url"), portfolio_text=d.get("portfolio_text"),
        certifications=[CertificationForm(**c) for c in d.get("certifications", [])],
        references=[ReferenceForm(**r) for r in d.get("references", [])],
        name=d.get("name"), target_role=d.get("target_role"), candidate_id=d.get("candidate_id"),
    )


def doc_to_state(doc: SourceDoc) -> dict:
    return {**asdict(doc), "repo": asdict(doc.repo) if doc.repo else None}


def doc_from_state(d: dict) -> SourceDoc:
    return SourceDoc(d["evidence_id"], d["type"], d["text"], d["label"], Repo(**d["repo"]) if d.get("repo") else None)


# --- Onboarding nodes ------------------------------------------------------------------------

def _ingest(deps: Deps):
    def node(state: RunState) -> dict:
        inputs = inputs_from_state(state["inputs"])
        if inputs.resume is None:
            return {"blocked": NO_RESUME_MESSAGE}
        conn = db.connect(deps.db_path)
        try:
            cid, docs, warnings = ingest(conn, inputs, github_client=deps.github_client,
                                         portfolio_session=deps.portfolio_session)
        finally:
            conn.close()
        return {"candidate_id": cid, "docs": [doc_to_state(d) for d in docs], "warnings": warnings, "blocked": None}
    return node


def _extract(deps: Deps):
    def node(state: RunState) -> dict:
        docs = docs_to_extract([doc_from_state(d) for d in state["docs"]])
        results = extract_all(docs, deps.llm) if docs else {}
        return {"extractions": {eid: ({"error": str(r)} if isinstance(r, Exception) else {"ok": r.model_dump()})
                                for eid, r in results.items()}}
    return node


def _validate(deps: Deps):
    def node(state: RunState) -> dict:
        docs = [doc_from_state(d) for d in state["docs"]]
        by_id = {d.evidence_id: d for d in docs}
        results: dict[str, object] = {}
        for eid, r in state.get("extractions", {}).items():
            results[eid] = RuntimeError(r["error"]) if "error" in r else output_type(by_id[eid]).model_validate(r["ok"])
        conn = db.connect(deps.db_path)
        try:
            forms = form_profile(conn, state["candidate_id"], inputs_from_state(state["inputs"]))
            profiles, report = validate_results(conn, docs, results, deps.today)
            result = finish_onboarding(conn, state["candidate_id"], forms, profiles, report, state.get("warnings", []))
        finally:
            conn.close()
        # Uploaded bytes are no longer needed in state.
        return {"warnings": result.warnings, "blocked": result.blocked, "inputs": {}, "docs": [], "extractions": {}}
    return node


def _review(deps: Deps):
    def node(state: RunState) -> dict:
        conn = db.connect(deps.db_path)
        try:
            blocker = scoring_blocker(store.load_profile(conn, state["candidate_id"]))
            answer = interrupt({"candidate_id": state["candidate_id"], "warnings": state.get("warnings", []),
                                "blocked": blocker,
                                "dropped": len(store.list_dropped(conn, state["candidate_id"]))})
            # The user may have edited or added back items before confirming.
            blocker = scoring_blocker(store.load_profile(conn, state["candidate_id"]))
        finally:
            conn.close()
        return {"blocked": blocker, "review": answer if isinstance(answer, dict) else {"answer": answer}}
    return node


def _after_review(state: RunState) -> str:
    if (state.get("review") or {}).get("cancel"):
        return END
    return "review" if state.get("blocked") else "prepare_match"


# --- Scoring nodes -------------------------------------------------------------------------

def _prepare(deps: Deps):
    def node(state: RunState) -> dict:
        weights = state.get("weights") or config.default_weights()
        with open_ctx(deps, state, with_relevance=False) as ctx:
            blocker = scoring_blocker(ctx.profile)
            if blocker:
                return {"blocked": blocker, "weights": weights}
            run_id = db.create_run(ctx.conn, ctx.candidate_id, ctx.baseline.id, weights,
                                   baseline_hash=ctx.baseline.content_hash(), models=judge_models())
            ctx.svc.run_id = run_id
            resolve(ctx, relevance_asks(ctx), call=True)
            errors = [f"prepare_match: {e}" for e in ctx.svc.errors]
        return {"run_id": run_id, "weights": weights, "blocked": None, "errors": errors}
    return node


def _fan_out(state: RunState):
    return END if state.get("blocked") else list(DIMENSIONS)


def _error_score(reason: str) -> dict:
    return asdict(SubScore("error", None, {"reason": reason}))


def subcategory_graph(deps: Deps, sub: Subcategory):
    """judge → compute → save for one subcategory."""

    def judge(state: RunState) -> dict:
        try:
            with open_ctx(deps, state) as ctx:
                resolve(ctx, sub.plan(ctx), call=True)
                return {"errors": [f"{sub.name}: {e}" for e in ctx.svc.errors]}
        except Exception as exc:  # never crash the run
            log.exception("judging %s failed", sub.name)
            return {"errors": [f"{sub.name}: judging failed: {exc.__class__.__name__}: {exc}"]}

    def compute(state: RunState) -> dict:
        try:
            with open_ctx(deps, state) as ctx:
                labels = resolve(ctx, sub.plan(ctx), call=False)   # saved judgments only
                score = sub.compute(ctx, labels)
                score.details["judgments"] = labels.trail()
                result = asdict(score)
        except Exception as exc:
            log.exception("computing %s failed", sub.name)
            result = _error_score(f"{exc.__class__.__name__}: {exc}")
        return {"subcategory_scores": {sub.name: result}}

    def save(state: RunState) -> dict:
        s = state["subcategory_scores"][sub.name]
        conn = db.connect(deps.db_path)
        try:
            with conn:
                conn.execute(
                    "INSERT INTO scores (id, run_id, candidate_id, baseline_id, subcategory, score, status, details, "
                    "guidance, logic_version, computed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), state["run_id"], state["candidate_id"], state["baseline_id"], sub.name, s["score"],
                     s["status"], json.dumps(s["details"], default=str), s["guidance"], config.LOGIC_VERSION,
                     now_iso()),
                )
        finally:
            conn.close()
        return {}

    g = StateGraph(RunState, output_schema=ScoreOut)
    g.add_node("judge", judge)
    g.add_node("compute", compute)
    g.add_node("save", save)
    g.add_edge(START, "judge")
    g.add_edge("judge", "compute")
    g.add_edge("compute", "save")
    g.add_edge("save", END)
    return g.compile(name=sub.name)


def dimension_graph(deps: Deps, name: str):
    g = StateGraph(RunState, output_schema=ScoreOut)
    for sub in DIMENSIONS[name]:
        g.add_node(sub.name, subcategory_graph(deps, sub))
        g.add_edge(START, sub.name)
        g.add_edge(sub.name, END)
    return g.compile(name=name)


def _aggregate(deps: Deps):
    def node(state: RunState) -> dict:
        agg = aggregate(state.get("subcategory_scores", {}), state["weights"])
        conn = db.connect(deps.db_path)
        try:
            db.finish_run(conn, state["run_id"], "done")
        finally:
            conn.close()
        return {"dimension_scores": agg.dimensions, "final": agg.final}
    return node


def _gap_analysis(deps: Deps):
    def node(state: RunState) -> dict:
        from scoring.gaps import rank_gaps
        from scoring.plan import generate_plan
        subscores = state.get("subcategory_scores", {})
        conn = db.connect(deps.db_path)
        try:
            baseline = db.get_baseline(conn, state["baseline_id"])
            ranked = rank_gaps(subscores, state["weights"], baseline)
            with timing.span(timing.PLAN, "improvement_plan"):
                plan = generate_plan(conn, state["run_id"], subscores, state["weights"], baseline,
                                     store.load_profile(conn, state["candidate_id"]), deps.planner)
        except Exception as exc:  # the scores are saved; a plan problem must not fail the run
            log.exception("gap analysis failed")
            return {"plan_error": f"{exc.__class__.__name__}: {exc}", "ranked_gaps": []}
        finally:
            conn.close()
        return {"ranked_gaps": [g.as_dict() for g in ranked], "plan_markdown": plan.markdown,
                "plan_error": plan.error}
    return node


def build_graph(deps: Deps) -> StateGraph:
    g = StateGraph(RunState)
    g.add_node("ingest", _ingest(deps))
    g.add_node("extract", _extract(deps))
    g.add_node("validate", _validate(deps))
    g.add_node("review", _review(deps))
    g.add_node("prepare_match", _prepare(deps))
    for name in DIMENSIONS:
        g.add_node(name, dimension_graph(deps, name))
    g.add_node("aggregate", _aggregate(deps))
    g.add_node("gap_analysis", _gap_analysis(deps))

    g.add_conditional_edges(START, lambda s: "ingest" if s.get("inputs") else "prepare_match",
                            ["ingest", "prepare_match"])
    g.add_conditional_edges("ingest", lambda s: END if s.get("blocked") else "extract", ["extract", END])
    g.add_edge("extract", "validate")
    g.add_edge("validate", "review")
    g.add_conditional_edges("review", _after_review, ["review", "prepare_match", END])
    g.add_conditional_edges("prepare_match", _fan_out, [*DIMENSIONS, END])
    g.add_edge(list(DIMENSIONS), "aggregate")
    g.add_edge("aggregate", "gap_analysis")
    g.add_edge("gap_analysis", END)
    return g


# --- Running it -----------------------------------------------------------------------------

STEP_LABELS = {
    "ingest": "Reading documents", "extract": "Extracting with the model", "validate": "Checking quotes and dates",
    "review": "Waiting for your review", "prepare_match": "Judging relevance of roles and projects",
    "aggregate": "Combining scores", "gap_analysis": "Ranking gaps and writing the plan",
}


def describe(namespace: tuple, node: str) -> str | None:
    """Human-readable progress line for a finished graph step (None for internal steps)."""
    path = [p.split(":")[0] for p in namespace]
    if not path:
        return STEP_LABELS.get(node, node)
    if len(path) == 2 and node == "save":                    # dimension › subcategory › save = done
        return f"{path[0].title()} › {config.SUBCATEGORY_LABELS.get(path[1], path[1])} done"
    return None


@dataclass
class TaskEvent:
    """A graph node started or ended. `path` = enclosing subgraph names, e.g. ("expertise", "tools")."""
    kind: str                       # start | end
    path: tuple[str, ...]
    node: str
    error: str | None = None
    status: str | None = None       # for a subcategory's compute step: scored | missing | na | error
    interrupted: bool = False


def task_event(namespace: tuple, chunk: dict) -> TaskEvent:
    path = tuple(p.split(":")[0] for p in namespace)
    if "input" in chunk:
        return TaskEvent("start", path, chunk["name"])
    status = None
    writes = chunk.get("result") or []
    items = writes.items() if isinstance(writes, dict) else writes
    for channel, value in items:
        if channel == "subcategory_scores" and isinstance(value, dict) and len(path) == 2:
            status = (value.get(path[1]) or {}).get("status")
    error = chunk.get("error")
    return TaskEvent("end", path, chunk["name"], str(error) if error else None, status, bool(chunk.get("interrupts")))


class Pipeline:
    """Onboarding (pauses for review) and scoring. Keep one instance per app session.

    `on_progress(line)` receives a short line each time a step finishes (for the UI's progress log).
    """

    def __init__(self, deps: Deps, checkpointer=None, print_timings: bool | None = None):
        self.deps = deps
        self.graph = build_graph(deps).compile(checkpointer=checkpointer or InMemorySaver())
        self.print_timings = config.PRINT_TIMINGS if print_timings is None else print_timings
        self.recorders: dict[str, timing.Recorder] = {}     # thread_id → timings (onboarding + scoring)

    @staticmethod
    def _cfg(thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def _run(self, payload, thread_id: str, on_progress=None, on_event=None) -> dict:
        """Run the graph, reporting each node start/end.

        on_progress(line): a short line per finished step (status log).
        on_event(event):   every node start/end, including inside subgraphs (live graph view).
        """
        cfg = self._cfg(thread_id)
        rec = self.recorders.setdefault(thread_id, timing.Recorder())
        started: dict[str, float] = {}
        with timing.recording(rec):
            for namespace, _mode, chunk in self.graph.stream(payload, cfg, stream_mode=["tasks"], subgraphs=True):
                event = task_event(namespace, chunk)
                self._time(rec, started, event, chunk)
                if on_event is not None:
                    on_event(event)
                if on_progress is not None and event.kind == "end" and not event.error:
                    line = describe(namespace, event.node)
                    if line:
                        on_progress(line)
        snapshot = self.graph.get_state(cfg)
        pending = snapshot.interrupts
        values = snapshot.values
        if values.get("run_id") and values.get("final") is not None and not pending:
            self._save_timings(values["run_id"], rec)
        return {**values, "thread_id": thread_id, "awaiting_review": pending[0].value if pending else None}

    @staticmethod
    def _time(rec: timing.Recorder, started: dict, event: TaskEvent, chunk: dict) -> None:
        """Wall-clock time of every graph node, from the task stream (specs/09 §1)."""
        task_id = chunk.get("id")
        if event.kind == "start":
            started[task_id] = rec.now()
            return
        start = started.pop(task_id, None)
        if start is None:
            return
        attrs = {"error": event.error} if event.error else {}
        if event.path:
            rec.add(timing.NODE, event.node, start, rec.now() - start, path=list(event.path), **attrs)
        else:
            rec.add(timing.STEP, event.node, start, rec.now() - start, **attrs)

    def _save_timings(self, run_id: str, rec: timing.Recorder) -> None:
        summary = rec.summary()
        conn = db.connect(self.deps.db_path)
        try:
            db.save_run_timings(conn, run_id, summary)
        finally:
            conn.close()
        if self.print_timings:
            print(f"\nTimings for run {run_id}:\n{timing.table(summary)}", flush=True)

    def start_onboarding(self, inputs: OnboardingInputs, baseline_id: str, weights: dict | None = None,
                         on_progress=None, on_event=None) -> dict:
        """Ingest, extract, validate; stops at review (result["awaiting_review"])."""
        tid = str(uuid.uuid4())
        return self._run({"inputs": inputs_to_state(inputs), "baseline_id": baseline_id,
                          "weights": weights or config.default_weights()}, tid, on_progress, on_event)

    def confirm_review(self, thread_id: str, answer: dict | None = None, on_progress=None, on_event=None) -> dict:
        """Resume after the user reviewed the profile; runs scoring unless still blocked or cancelled."""
        return self._run(Command(resume=answer or {"confirmed": True}), thread_id, on_progress, on_event)

    def score(self, candidate_id: str, baseline_id: str, weights: dict | None = None, on_progress=None,
              on_event=None) -> dict:
        """Score a confirmed profile against a baseline, without re-extracting (01 step 9)."""
        tid = str(uuid.uuid4())
        return self._run({"candidate_id": candidate_id, "baseline_id": baseline_id,
                          "weights": weights or config.default_weights()}, tid, on_progress, on_event)


def run_scores(conn, run_id: str) -> dict[str, dict]:
    """Saved subcategory scores of a run (latest row per subcategory)."""
    rows = conn.execute("SELECT * FROM scores WHERE run_id = ? ORDER BY computed_at, rowid", (run_id,)).fetchall()
    return {r["subcategory"]: {"status": r["status"], "score": r["score"], "guidance": r["guidance"],
                               "details": json.loads(r["details"] or "{}")} for r in rows}


def rescore(conn, run_id: str, weights: dict | None = None) -> Aggregate:
    """Re-aggregate saved scores with other weights. No judge, no model call (CLAUDE.md rule 6)."""
    if weights is None:
        row = conn.execute("SELECT weights_json FROM runs WHERE id = ?", (run_id,)).fetchone()
        weights = json.loads(row["weights_json"]) if row else config.default_weights()
    return aggregate(run_scores(conn, run_id), weights)


def run_gaps(conn, run_id: str, weights: dict | None = None):
    """Re-rank gaps for a run with (possibly new) weights. Code only: no model call."""
    from scoring.gaps import rank_gaps
    run = conn.execute("SELECT baseline_id, weights_json FROM runs WHERE id = ?", (run_id,)).fetchone()
    weights = weights or json.loads(run["weights_json"])
    return rank_gaps(run_scores(conn, run_id), weights, db.get_baseline(conn, run["baseline_id"]))


def _run_inputs(conn, run_id: str, weights: dict | None):
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    weights = weights or json.loads(run["weights_json"])
    return run, weights, db.get_baseline(conn, run["baseline_id"])


def current_plan(conn, run_id: str, weights: dict | None = None):
    """Stored plan re-priced for `weights` (slider change). Code only: no model call. None if no plan yet."""
    from scoring.plan import load_plan
    run, weights, baseline = _run_inputs(conn, run_id, weights)
    return load_plan(conn, run_id, run_scores(conn, run_id), weights, baseline)


def regenerate_plan(conn, run_id: str, planner, weights: dict | None = None):
    """'Regenerate plan' (specs/06): ask the model again for the current weights."""
    from scoring.plan import generate_plan
    run, weights, baseline = _run_inputs(conn, run_id, weights)
    return generate_plan(conn, run_id, run_scores(conn, run_id), weights, baseline,
                         store.load_profile(conn, run["candidate_id"]), planner, regenerate=True)


def save_run_weights(conn, run_id: str, weights: dict) -> None:
    """'Save weights to this run' (specs/06)."""
    with conn:
        conn.execute("UPDATE runs SET weights_json = ? WHERE id = ?", (json.dumps(weights, sort_keys=True), run_id))


__all__ = ["Pipeline", "Deps", "build_graph", "rescore", "run_scores", "save_run_weights", "schemas"]
