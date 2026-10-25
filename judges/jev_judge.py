"""Jev (TypeSafe AI) as judge, with Claude fallback (specs/02, specs/05 §B).

Request/response format verified against https://docs.typesafe.ai/api and a live
probe (phase 4): POST /v1/systemone {model, state, questions}; answers per type:
  noul   → {"noul": P(yes)}                                   (no probabilities/confidence)
  choice → {"choice", "probabilities": {option: p}, "confidence"}
  score  → {"score", "legend": {"0": level, …}, "probabilities": {"0": p, …}, "confidence"}
Jev is not deterministic (±0.01 between identical calls), returns no quote and no
reasoning, and has no temperature setting.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from core import config, timing
from providers.limits import jev_slots
from judges.base import Judge, Judgment, JudgeError, _as_text, question
from judges.questions import Question

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504, 529}


class JevError(Exception):
    pass


def jev_instructions(q: Question) -> str:
    # Catalogue wording points at context tags (<baseline>); Jev names state keys (`baseline`).
    return re.sub(r"<(\w+)>", r"`\1`", q.instructions)


def levels(q: Question) -> list[str]:
    """Score levels for Jev, worst first, so a higher Jev `score` means a better level."""
    return list(reversed(q.options))


def build_question(q: Question) -> dict:
    body = {"instructions": jev_instructions(q)}
    if q.kind == "yes_no":
        return {"type": "noul", **body, "criteria": {"true": q.options["yes"], "false": q.options["no"]}}
    if q.kind == "graded":
        return {"type": "score", **body, "criteria": [q.options[name] for name in levels(q)]}
    return {"type": "choice", **body, "criteria": dict(q.options)}


def build_state(context: dict) -> dict:
    """Candidate and baseline text as Jev state (data, never instructions). '_' keys are metadata."""
    return {k: _as_text(v) for k, v in context.items() if not k.startswith("_")}


def parse_answer(q: Question, answer: dict, model: str) -> Judgment:
    kind = answer.get("type")
    if q.kind == "yes_no":
        if kind != "noul" or not isinstance(answer.get("noul"), (int, float)):
            raise JevError(f"expected a noul answer, got {answer!r}")
        p = float(answer["noul"])
        return Judgment(label="yes" if p >= 0.5 else "no", probabilities={"yes": p, "no": round(1 - p, 6)},
                        confidence=abs(2 * p - 1), method="jev", model_version=model)
    if q.kind == "graded":
        if kind != "score":
            raise JevError(f"expected a score answer, got {answer!r}")
        names = levels(q)
        by_level = {names[int(i)]: float(p) for i, p in answer["probabilities"].items()}
        # Highest probability wins; a tie goes to the lower (worse) level.
        label = max(names, key=lambda n: (by_level.get(n, 0.0), -names.index(n)))
        return Judgment(label=label, probabilities=by_level, confidence=answer.get("confidence"),
                        score=answer.get("score"), method="jev", model_version=model)
    if kind != "choice" or answer.get("choice") not in q.options:
        raise JevError(f"expected a choice among {list(q.options)}, got {answer!r}")
    return Judgment(label=answer["choice"], probabilities={k: float(v) for k, v in answer["probabilities"].items()},
                    confidence=answer.get("confidence"), method="jev", model_version=model)


class JevJudge:
    """Jev first; Claude when Jev fails or its confidence is below JEV_CONFIDENCE_THRESHOLD.

    Complexity always goes to Claude (it must reason first; Jev returns no reasoning).
    """

    CLAUDE_ONLY = {"complexity"}

    def __init__(self, fallback: Judge, session: requests.Session | None = None, api_key: str | None = None,
                 model: str | None = None, threshold: float | None = None, max_retries: int = 2,
                 backoff: float = 0.5):
        self.fallback = fallback
        self._real = session is None
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else config.JEV_API_KEY
        self.model = model or config.JEV_MODEL
        self.threshold = config.JEV_CONFIDENCE_THRESHOLD if threshold is None else threshold
        self.max_retries = max_retries
        self.backoff = backoff
        self.fallbacks = 0  # how often Claude was used instead of Jev (for the judge report)
        self._lock = threading.Lock()

    def models_for(self, key: str) -> list[str]:
        claude = self.fallback.models_for(key)
        return claude if key in self.CLAUDE_ONLY else [self.model, *claude]

    def _post(self, body: dict) -> dict:
        if self._real and not config.LIVE:
            raise JevError("Real Jev calls are off: set LIVE=1 to call providers (make live does).")
        if self._real:
            from providers.spend import SpendCapReached, charge
            try:
                charge("jev")                          # daily cap (deploy item 6): past it, the free route judges
            except SpendCapReached as exc:
                raise JevError(str(exc)) from exc
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(self.max_retries + 1):
            try:
                with jev_slots, timing.span(timing.MODEL, "jev", model=self.model,
                                            questions=len(body.get("questions") or {})):
                    resp = self.session.post(config.JEV_URL, json=body, headers=headers, timeout=30)
            except requests.RequestException as exc:
                error = JevError(f"connection error: {exc.__class__.__name__}")
            else:
                if resp.status_code == 200:
                    return resp.json()
                error = JevError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                if resp.status_code in (429, 529):
                    timing.count(f"jev_{resp.status_code}")
                if resp.status_code not in RETRY_STATUSES:
                    raise error
            if attempt < self.max_retries:
                timing.count("jev_retries")
                time.sleep(self.backoff * 2 ** attempt)
        raise error

    def call(self, key: str, context: dict) -> Judgment:
        """One Jev judgment, no fallback. Raises JevError."""
        q = question(key)
        data = self._post({"model": self.model, "state": build_state(context),
                           "questions": {key: build_question(q)}})
        answer = (data.get("answers") or {}).get(key)
        if answer is None:
            raise JevError(f"no answer for {key} in response")
        served = data.get("model", self.model)
        if served != self.model:
            log.warning("Jev served %s but %s was requested; cache matches on the requested version", served,
                        self.model)
        return parse_answer(q, answer, self.model)

    def call_many(self, keys: list[str], context: dict) -> dict[str, Judgment | JevError]:
        """Several questions about the same state in one Jev request (specs/09 §2.3)."""
        qs = {k: question(k) for k in keys}
        data = self._post({"model": self.model, "state": build_state(context),
                           "questions": {k: build_question(q) for k, q in qs.items()}})
        answers = data.get("answers") or {}
        out: dict[str, Judgment | JevError] = {}
        for k, q in qs.items():
            try:
                if k not in answers:
                    raise JevError(f"no answer for {k} in response")
                out[k] = parse_answer(q, answers[k], self.model)
            except (JevError, KeyError, TypeError, ValueError) as exc:
                out[k] = exc if isinstance(exc, JevError) else JevError(f"bad answer for {k}: {exc}")
        return out

    def group(self, keys: list[str], context: dict) -> dict[str, Judgment | JudgeError]:
        """One Jev request for all `keys`; each low-confidence or failed answer falls back to Claude alone."""
        keys = [k for k in keys if k not in self.CLAUDE_ONLY]
        try:
            jev = self.call_many(keys, context)
        except JevError as exc:
            log.warning("Jev failed for %s, using Claude: %s", ",".join(keys), exc)
            jev = {k: exc for k in keys}

        def one(k: str) -> tuple[str, Judgment | JudgeError]:
            answer = jev[k]
            if isinstance(answer, JevError):
                log.warning("Jev failed for %s, using Claude: %s", k, answer)
                answer = None
            q = question(k)
            fallback = (lambda: self.fallback.yes_no(k, context)) if q.kind == "yes_no" \
                else (lambda: self.fallback.choice(k, list(q.options), context))
            try:
                return k, self._judge(k, context, fallback, jev=answer)
            except JudgeError as exc:
                return k, exc

        with ThreadPoolExecutor(max_workers=len(keys) or 1) as pool:   # fallbacks run side by side
            return dict(pool.map(timing.bind(one), keys))

    def _judge(self, key: str, context: dict, ask_fallback, jev: Judgment | None | bool = False) -> Judgment:
        """jev=False: ask Jev now. Otherwise `jev` is the answer already fetched (None = Jev failed)."""
        if key in self.CLAUDE_ONLY:
            return ask_fallback()
        if jev is False:
            try:
                jev = self.call(key, context)
            except JevError as exc:
                log.warning("Jev failed for %s, using Claude: %s", key, exc)
                jev = None
        if jev is not None and jev.confidence is not None and jev.confidence >= self.threshold:
            return jev
        with self._lock:
            self.fallbacks += 1
        try:
            claude = ask_fallback()
        except JudgeError as exc:
            # A label below the threshold is not trusted: the item counts as failed, Jev's answer is kept as unused.
            raise JudgeError(f"Claude fallback failed: {exc}", discarded=jev) from exc
        return claude.model_copy(update={"fallback_from": jev})

    def choice(self, question_key: str, options: list[str], context: dict) -> Judgment:
        return self._judge(question_key, context, lambda: self.fallback.choice(question_key, options, context))

    def yes_no(self, question_key: str, context: dict) -> Judgment:
        return self._judge(question_key, context, lambda: self.fallback.yes_no(question_key, context))


__all__ = ["JevJudge", "JevError", "JudgeError", "build_question", "build_state", "parse_answer"]
