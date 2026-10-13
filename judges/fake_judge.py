"""Scripted judge for tests: no model calls."""
from __future__ import annotations

import threading
import time
from typing import Callable

from judges.base import Judgment, JudgeError, question

Script = Callable[[str, dict], "str | Exception | None"]


class FakeJudge:
    """Label lookup order: script(question, context) → labels[(question, subject_id)] → labels[question] → default.

    A value that is an Exception is raised (as JudgeError) to simulate a failed call.
    A value may be a (label, quote) tuple to return a supporting quote.
    `context["_subject_id"]` is set by the judgment service.
    """

    MODEL = "fake-judge-1"

    def __init__(self, labels: dict | None = None, default: str | None = None, script: Script | None = None,
                 delay: float = 0.0):
        self.labels = labels or {}
        self.default = default
        self.script = script
        self.delay = delay              # artificial latency per call (concurrency tests)
        self.calls: list[tuple[str, dict]] = []
        self.spans: list[tuple[str, float, float]] = []    # (question, start, end) when delay > 0
        self.in_flight = self.max_in_flight = 0
        self._lock = threading.Lock()

    def models_for(self, key: str) -> list[str]:
        return [self.MODEL]

    def _label(self, key: str, context: dict) -> Judgment:
        self.calls.append((key, context))
        if self.delay:
            with self._lock:
                self.in_flight += 1
                self.max_in_flight = max(self.max_in_flight, self.in_flight)
            start = time.perf_counter()
            time.sleep(self.delay)
            with self._lock:
                self.in_flight -= 1
                self.spans.append((key, start, time.perf_counter()))
        value = self.script(key, context) if self.script else None
        if value is None:
            value = self.labels.get((key, context.get("_subject_id")), self.labels.get(key, self.default))
        if isinstance(value, Exception):
            raise JudgeError(str(value))
        if value is None:
            raise JudgeError(f"FakeJudge has no label for {key}")
        value, quote = value if isinstance(value, tuple) else (value, None)
        if value not in question(key).options:
            raise ValueError(f"FakeJudge label {value!r} is not an option of {key}")
        return Judgment(label=value, quote=quote, method="claude", model_version=self.MODEL, reasoning="scripted")

    def choice(self, question_key: str, options: list[str], context: dict) -> Judgment:
        return self._label(question_key, context)

    def yes_no(self, question_key: str, context: dict) -> Judgment:
        return self._label(question_key, context)
