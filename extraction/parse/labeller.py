"""Jev for the lines and blocks code cannot decide (specs/18 §4.2 item 20). Multi-question requests.

Questions are about the document text in the request state (data, never instructions) and are model-neutral.
Answers below JEV_CONFIDENCE_THRESHOLD are not trusted: the parser counts that line as unclear (the document's
confidence gate then decides). `NoLabeller` stands for "Jev not available" (offline, or no key): every question
comes back unanswered. `RecordedLabeller` replays saved answers (tests and the offline gate reports).
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path

from core import config, timing


@dataclass(frozen=True)
class Ask:
    key: str                        # unique within one document
    kind: str                       # choice | noul
    instructions: str               # names its state keys in backticks
    criteria: tuple                 # choice: ((option, meaning), ...); noul: ((True, meaning), (False, meaning))
    state: tuple                    # ((state key, text), ...)

    def body(self) -> dict:
        if self.kind == "noul":
            crit = dict(self.criteria)
            return {"type": "noul", "instructions": self.instructions,
                    "criteria": {"true": crit[True], "false": crit[False]}}
        return {"type": "choice", "instructions": self.instructions, "criteria": dict(self.criteria)}

    def fingerprint(self) -> str:
        raw = json.dumps({"q": self.body(), "s": dict(self.state)}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class Answer:
    label: str                      # an option, or "yes"/"no" for a noul
    confidence: float
    probabilities: dict | None = None

    @property
    def trusted(self) -> bool:
        return self.confidence >= config.JEV_CONFIDENCE_THRESHOLD


@dataclass
class Counts:
    requests: int = 0
    questions: int = 0
    unanswered: int = 0


class NoLabeller:
    """Jev is not available: nothing is answered (the parser's gate decides what that means)."""
    live = False

    def __init__(self):
        self.counts = Counts()

    def ask(self, asks: list[Ask]) -> dict[str, Answer | None]:
        self.counts.unanswered += len(asks)
        return {a.key: None for a in asks}


class FakeLabeller:
    """Tests: `script(ask) -> (label, confidence) | None`."""
    live = False

    def __init__(self, script):
        self.script = script
        self.counts = Counts()
        self.asked: list[Ask] = []

    def ask(self, asks: list[Ask]) -> dict[str, Answer | None]:
        out = {}
        for chunk in _chunks(asks):
            self.counts.requests += 1
            for a in chunk:
                self.counts.questions += 1
                self.asked.append(a)
                r = self.script(a)
                out[a.key] = Answer(*r) if r is not None else None
                self.counts.unanswered += r is None
        return out


def _chunks(asks: list[Ask]) -> list[list[Ask]]:
    n = max(1, config.JEV_PARSE_BATCH)
    return [asks[i:i + n] for i in range(0, len(asks), n)]


class JevLabeller:
    """Jev over HTTP (same endpoint, model and LIVE guard as the judge). A failed request leaves its questions
    unanswered; it never raises into extraction."""
    live = True

    def __init__(self, jev=None):
        from judges.jev_judge import JevJudge
        self.jev = jev or JevJudge(fallback=None)
        self.counts = Counts()
        self._lock = threading.Lock()

    def ask(self, asks: list[Ask]) -> dict[str, Answer | None]:
        from judges.jev_judge import JevError
        out: dict[str, Answer | None] = {}
        for chunk in _chunks(asks):
            state: dict[str, str] = {}
            for a in chunk:
                state.update(dict(a.state))
            body = {"model": self.jev.model, "state": state, "questions": {a.key: a.body() for a in chunk}}
            with self._lock:
                self.counts.requests += 1
                self.counts.questions += len(chunk)
            try:
                with timing.span(timing.MODEL, "jev_parse", questions=len(chunk)):
                    data = self.jev._post(body)
            except JevError:
                for a in chunk:
                    out[a.key] = None
                continue
            answers = data.get("answers") or {}
            for a in chunk:
                out[a.key] = parse(a, answers.get(a.key))
        with self._lock:
            self.counts.unanswered += sum(v is None for v in out.values())
        return out


def parse(a: Ask, answer: dict | None) -> Answer | None:
    """Jev's answer (noul → P(yes); choice → option, probabilities, confidence), or None when unusable."""
    if not isinstance(answer, dict):
        return None
    if a.kind == "noul":
        p = answer.get("noul")
        if not isinstance(p, (int, float)):
            return None
        return Answer("yes" if p >= 0.5 else "no", abs(2 * float(p) - 1), {"yes": float(p)})
    choice = answer.get("choice")
    if choice not in dict(a.criteria):
        return None
    return Answer(choice, float(answer.get("confidence") or 0.0), answer.get("probabilities"))


@dataclass
class RecordedLabeller:
    """Replays answers saved by fingerprint (question + state). With `live`, asks it for misses and records them."""
    path: Path
    live_labeller: object | None = None
    counts: Counts = field(default_factory=Counts)

    @property
    def live(self) -> bool:
        return self.live_labeller is not None

    def _load(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def ask(self, asks: list[Ask]) -> dict[str, Answer | None]:
        saved = self._load()
        out, misses = {}, []
        for a in asks:
            rec = saved.get(a.fingerprint())
            if rec is not None:
                out[a.key] = Answer(rec["label"], rec["confidence"], rec.get("probabilities"))
            else:
                misses.append(a)
        if misses and self.live_labeller is not None:
            got = self.live_labeller.ask(misses)
            for a in misses:
                if got.get(a.key) is not None:
                    ans = got[a.key]
                    saved[a.fingerprint()] = {"label": ans.label, "confidence": ans.confidence,
                                              "probabilities": ans.probabilities}
                out[a.key] = got.get(a.key)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(saved, indent=1, sort_keys=True))
        else:
            for a in misses:
                out[a.key] = None
        self.counts.questions += len(asks)
        self.counts.unanswered += sum(v is None for v in out.values())
        return out


def default_labeller():
    """Jev when a key is set (judging with Jev), else no Jev: the gate falls back for anything unclear."""
    if config.JEV_API_KEY and config.JUDGE == "jev":
        return JevLabeller()
    return NoLabeller()
