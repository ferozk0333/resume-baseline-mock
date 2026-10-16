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

from scoring.explain import FORMULAS  # noqa: E402,F401  (re-exported for the pages)


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
