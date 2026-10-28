"""The server's dependencies (judge, models, database) and the state one API process keeps.

Set RIGHTSTAFF_DEPS_FACTORY="module:function" to build Deps differently (tests use fakes).
"""
from __future__ import annotations

import importlib
import os

from core import config
from graph.context import Deps
from graph.pipeline import Pipeline


def default_deps() -> Deps:
    from providers.llm import RoutedLLM
    from judges.service import get_judge
    from providers.routing import models_of
    models = [x for m in (config.EXTRACT_MODEL, config.JUDGE_FALLBACK_MODEL, config.COMPLEXITY_MODEL,
                          config.REASONING_MODEL) for x in models_of(m)]
    if not config.ANTHROPIC_API_KEY and not all(m.startswith(("groq/", "gemini/")) for m in models):
        raise RuntimeError("ANTHROPIC_API_KEY is not set in .env (or set LLM_PROVIDER=gemini).")
    return Deps(str(config.DB_PATH), get_judge(), llm=RoutedLLM(effort=config.EXTRACT_EFFORT), extraction_cache=True,
                planner=RoutedLLM(max_tokens=config.PLAN_MAX_TOKENS, effort=config.PLAN_EFFORT),
                writer=RoutedLLM(), job_llm=RoutedLLM(max_tokens=config.EXTRACT_MAX_TOKENS), weights_llm=RoutedLLM())


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
        from providers.llm import RoutedLLM
        self._writer = getattr(self, "_writer", None) or RoutedLLM()
        return self._writer

    @property
    def job_llm(self):
        """The JD / market reader (specs/14, 15). Like `writer`, it never needs the judge."""
        if self._deps is not None or os.getenv("RIGHTSTAFF_DEPS_FACTORY"):
            return self.deps.job_llm
        from providers.llm import RoutedLLM
        self._job_llm = getattr(self, "_job_llm", None) or RoutedLLM(max_tokens=config.EXTRACT_MAX_TOKENS)
        return self._job_llm

    @property
    def rejection(self):
        """The "Not moving forward" graph (paused drafts are checkpointed to SQLite)."""
        if self._rejection is None:
            from workflows.rejection_flow import RejectionFlow
            self._rejection = RejectionFlow(self.db_path, self.writer)
        return self._rejection

    @property
    def hiring(self):
        """The hiring workflow graph, one thread per application (checkpointed to SQLite)."""
        if self._hiring is None:
            from workflows.hiring_flow import HiringFlow
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
            if self.deps.on_source_ready is None:
                self.deps.on_source_ready = getattr(self, "on_source_ready", None)
            self._pipeline = Pipeline(self.deps)
        return self._pipeline

    @property
    def db_path(self) -> str:
        return self._deps.db_path if self._deps is not None else str(config.DB_PATH)
