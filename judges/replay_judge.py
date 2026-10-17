"""Replays saved judgments (offline gate reports, specs/18 §4.4): the answer recorded for exactly this question and
input (same input hash), from any earlier run. That includes a rule outcome recorded when the model's label had no
quote in the input (the judge was asked; code recorded the none-option). A question never asked before has no recording: it raises
JudgeError and is counted, never guessed."""
from __future__ import annotations

import sqlite3
import threading

from judges.base import Judgment, JudgeError
from judges.service import input_hash


class ReplayJudge:
    """`prefer`: candidates whose own answers win. The same text can have been judged for several candidates with
    different answers (a low-confidence Jev label falls back to an LLM, which is not deterministic), so a replay of
    candidate X must use X's answers first, then anyone's."""

    def __init__(self, conn: sqlite3.Connection, prefer: tuple[str, ...] = ()):
        import json
        self.rows: dict[tuple[str, str], Judgment] = {}
        self.models: set[str] = set()
        mine = ",".join("?" * len(prefer)) or "NULL"
        for r in conn.execute("SELECT question, input_hash, label, quote, reasoning, method, model_version, "
                              "probabilities, confidence, score FROM judgments WHERE used = 1 AND input_hash IS NOT NULL "
                              f"ORDER BY candidate_id IN ({mine}), created_at, rowid", prefer):
            self.rows[(r[0], r[1])] = Judgment(label=r[2], quote=r[3], reasoning=r[4], method=r[5], model_version=r[6],
                                               probabilities=json.loads(r[7]) if r[7] else None, confidence=r[8],
                                               score=r[9])
            self.models.add(r[6])
        self.hits = 0
        self.misses: list[str] = []                # questions with no recording (main judge: Jev first)
        self.route_misses: list[str] = []          # … asked through the fallback (route) judge directly
        self.missed_inputs: set[tuple[str, str, bool]] = set()   # (question, input hash, via route): unique asks
        self.missed_subjects: list[tuple[str, str, bool]] = []    # (question, subject id, via route)
        self._lock = threading.Lock()
        self.fallback = _Route(self)               # Deps uses judge.fallback for asks that need a quote

    def models_for(self, key: str) -> list[str]:
        return sorted(self.models)

    def _get(self, key: str, context: dict, route: bool = False) -> Judgment:
        j = self.rows.get((key, input_hash(key, context)))
        with self._lock:
            if j is None:
                (self.route_misses if route else self.misses).append(key)
                self.missed_inputs.add((key, input_hash(key, context), route))
                self.missed_subjects.append((key, str(context.get("_subject_id")), route))
            else:
                self.hits += 1
        if j is None:
            raise JudgeError(f"no recorded judgment for {key} with this input")
        return j

    def yes_no(self, question_key: str, context: dict) -> Judgment:
        return self._get(question_key, context)

    def choice(self, question_key: str, options: list[str], context: dict) -> Judgment:
        return self._get(question_key, context)


class _Route:
    def __init__(self, main: ReplayJudge):
        self.main = main

    def models_for(self, key: str) -> list[str]:
        return self.main.models_for(key)

    def yes_no(self, question_key: str, context: dict) -> Judgment:
        return self.main._get(question_key, context, route=True)

    def choice(self, question_key: str, options: list[str], context: dict) -> Judgment:
        return self.main._get(question_key, context, route=True)
