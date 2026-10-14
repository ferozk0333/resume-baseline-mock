"""Non-visual helpers shared by the Streamlit pages (connections, pipeline, lookups).

Set RIGHTSTAFF_DEPS_FACTORY="package.module:function" to build Deps differently
(tests use fakes; the function takes no arguments and returns graph.context.Deps).
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from core import config, db  # noqa: E402
from graph.context import Deps  # noqa: E402
from graph.pipeline import Pipeline  # noqa: E402

log = logging.getLogger("rightstaff.ui")

STATUS_BADGES = {"scored": "🟢 scored", "missing": "🟠 missing", "na": "⚪ N/A", "error": "🔴 error"}

FORMULAS = {
    "relevance": "Σ(months × relevance) / Σ months over roles",
    "depth": "relevant years ÷ max(1, required years), capped at 1; entry level: max(work, Σ project relevance ÷ 3)",
    "scope": "most recent relevant role vs the baseline's scope: meets 1 · partly 0.5 · below 0",
    "progression": "most recent role vs earlier ones: more impactful 1 · same 0.5 · less 0 (N/A with one role)",
    "recency": "max(relevance × 0.9^years since the activity ended)",
    "role_skills": "(2 × required + preferred) ÷ 3; per skill: bullet 1 · keyword 0.5 · missing 0",
    "applied_knowledge": "(2 × required + preferred) ÷ 3; per skill: task+result 1 · task 0.5 · mention 0",
    "tools": "(2 × required + preferred) ÷ 3; per tool: used/certified 1 · listed 0.5 · missing 0",
    "complexity": "max over relevant roles/projects: meets 1 · partly 0.5 · no 0",
    "work_samples": "1 if any repo, portfolio project, presentation or publication is relevant, else 0",
    "outcomes": "max over bullets of (metric + direction: both 1 · one 0.5) × relevance",
    "impact": "max over bullets of (business 1 · operational 0.5) × relevance",
    "ownership": "max over bullets of (led 1 · contributed 0.5) × relevance",
    "scope_of_impact": "expected reach − best reach: ≤0 → 1 · 1 → 0.5 · ≥2 → 0",
    "recognition": "(relevant award + relevant publication) ÷ 2",
    "proof_density": "average best support per claim: artifact 1 · bullet 0.5 · claimed only 0",
    "validation_strength": "manager/supervisor reference confirming relevant work 1 · verified relevant certificate 0.5",
    "consistency": "1 if LinkedIn, GitHub or portfolio aligns with the resume, else 0",
    "evidence_quality": "average over roles/projects of (yes checks ÷ 5)",
}


@contextmanager
def connection():
    conn = db.init_app_db(config.DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


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


def pipeline() -> Pipeline:
    """One pipeline per browser session (it holds the paused review state)."""
    if "pipeline" not in st.session_state:
        st.session_state.pipeline = Pipeline(build_deps())
    return st.session_state.pipeline


def show_error(action: str, exc: Exception) -> None:
    """Inline error, never a stack trace (the trace goes to the console log)."""
    log.exception("%s failed", action)
    st.error(f"{action} failed: {exc}")


def latest_run(conn, candidate_id: str, baseline_id: str) -> dict | None:
    return next((r for r in db.list_runs(conn, candidate_id)
                 if r["baseline_id"] == baseline_id and r["status"] == "done"), None)


def unused_judgments(conn, candidate_id: str, subject_id: str, question: str) -> list[dict]:
    rows = conn.execute(
        "SELECT method, label, confidence, model_version, reasoning, created_at FROM judgments WHERE candidate_id = ? "
        "AND subject_id = ? AND question = ? AND used = 0 ORDER BY created_at DESC, rowid DESC LIMIT 3",
        (candidate_id, subject_id, question)).fetchall()
    return [dict(r) for r in rows]


def candidate_label(c: dict) -> str:
    return f"{c.get('name') or 'Unnamed candidate'} · {c['created_at'][:16].replace('T', ' ')}"


def pick_candidate_and_baseline(conn, *, key: str) -> tuple[str | None, str | None]:
    """Shared selectors; remember the choice in session state."""
    from extraction import store
    candidates = store.list_candidates(conn)
    baselines = db.list_baselines(conn)
    if not candidates or not baselines:
        return None, None
    cids = [c["id"] for c in candidates]
    bids = [b.id for b in baselines]
    cur_c = st.session_state.get("candidate_id")
    cur_b = st.session_state.get("baseline_id")
    col1, col2 = st.columns(2)
    cid = col1.selectbox("Candidate", cids, index=cids.index(cur_c) if cur_c in cids else 0,
                         format_func=lambda i: candidate_label(next(c for c in candidates if c["id"] == i)),
                         key=f"{key}_candidate")
    bid = col2.selectbox("Baseline", bids, index=bids.index(cur_b) if cur_b in bids else 0,
                         format_func=lambda i: next(b.title for b in baselines if b.id == i), key=f"{key}_baseline")
    st.session_state.candidate_id, st.session_state.baseline_id = cid, bid
    return cid, bid


def details_without_trail(details: dict) -> dict:
    return {k: v for k, v in details.items() if k != "judgments"}


def pretty(obj) -> str:
    return json.dumps(obj, indent=1, default=str)
