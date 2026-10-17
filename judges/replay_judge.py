"""Replays saved judgments (offline gate reports, specs/18 §4.4): the answer recorded for exactly this question and
input (same input hash), from any earlier run. A question never asked before has no recording: it raises
JudgeError and is counted, never guessed."""
from __future__ import annotations

import sqlite3
import threading

from judges.base import Judgment, JudgeError
from judges.service import RULE_MODEL, input_hash


class ReplayJudge:
    def __init__(self, conn: sqlite3.Connection):
        self.rows: dict[tuple[str, str], Judgment] = {}
        self.models: set[str] = set()
        for r in conn.execute("SELECT question, input_hash, label, quote, reasoning, method, model_version, "
                              "probabilities, confidence, score FROM judgments WHERE used = 1 AND input_hash IS NOT NULL "
                              "AND model_version != ? ORDER BY created_at, rowid", (RULE_MODEL,)):
            import json
            self.rows[(r[0], r[1])] = Judgment(label=r[2], quote=r[3], reasoning=r[4], method=r[5], model_version=r[6],
                                               probabilities=json.loads(r[7]) if r[7] else None, confidence=r[8],
                                               score=r[9])
            self.models.add(r[6])
        self.hits = 0
        self.misses: list[str] = []
        self._lock = threading.Lock()

    def models_for(self, key: str) -> list[str]:
        return sorted(self.models)

    def _get(self, key: str, context: dict) -> Judgment:
        j = self.rows.get((key, input_hash(key, context)))
        with self._lock:
            if j is None:
                self.misses.append(key)
            else:
                self.hits += 1
        if j is None:
            raise JudgeError(f"no recorded judgment for {key} with this input")
        return j

    def yes_no(self, question_key: str, context: dict) -> Judgment:
        return self._get(question_key, context)

    def choice(self, question_key: str, options: list[str], context: dict) -> Judgment:
        return self._get(question_key, context)
