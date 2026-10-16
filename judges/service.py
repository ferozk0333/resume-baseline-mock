"""Ask judges through a cache, and save every judgment (CLAUDE.md rule 2).

Cache key: (subject_id, question, baseline_id or NULL, baseline_hash or NULL,
model_version, input_hash). `input_hash` is the SHA-256 of the question key and
the exact context text sent to the judge, so an edited bullet or role is judged
again while an unchanged one never is. Nothing run-specific (run id, timestamps)
enters the key. Job-dependent questions also carry the baseline id and content
hash; job-independent ones are stored with NULLs and reused across baselines.

Thread-safe: graph nodes judge items in parallel through one service.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading

from core import config, timing
from core.db import new_id, now_iso
from core.models import Baseline
from judges.base import Judge, Judgment, JudgeError, ask, question, render_context

log = logging.getLogger(__name__)

RULE_MODEL = f"rule-{config.LOGIC_VERSION}"


# Single flight (specs/09 §2.6): a judgment being asked right now (e.g. by the early-start job during
# review) is awaited rather than asked again, so overlapping work never pays twice.
_INFLIGHT: dict[tuple, threading.Event] = {}
_INFLIGHT_LOCK = threading.Lock()
INFLIGHT_WAIT_SECONDS = 180


def input_hash(key: str, context: dict) -> str:
    return hashlib.sha256(f"{key}\n{render_context(context)}".encode("utf-8")).hexdigest()


class JudgmentService:
    def __init__(self, conn: sqlite3.Connection, judge: Judge, candidate_id: str, run_id: str | None = None):
        self.conn = conn
        self.judge = judge
        self.candidate_id = candidate_id
        self.run_id = run_id
        self.errors: list[str] = []
        self.calls = 0       # judge calls made (cache misses)
        self.cache_hits = 0
        self._lock = threading.RLock()

    @staticmethod
    def _scope(key: str, baseline: Baseline | None) -> tuple[str | None, str | None]:
        if not question(key).job_dependent:
            return None, None
        if baseline is None or baseline.id is None:
            raise ValueError(f"{key} is job-dependent and needs a saved baseline")
        return baseline.id, baseline.content_hash()

    def _lookup(self, key: str, subject_id: str, context: dict, baseline: Baseline | None,
                models: list[str]) -> Judgment | None:
        baseline_id, baseline_hash = self._scope(key, baseline)
        with self._lock:
            row = self.conn.execute(
                f"SELECT * FROM judgments WHERE candidate_id = ? AND subject_id = ? AND question = ? "
                f"AND baseline_id IS ? AND baseline_hash IS ? AND input_hash = ? AND used = 1 "
                f"AND model_version IN ({','.join('?' * len(models))}) "
                f"ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (self.candidate_id, subject_id, key, baseline_id, baseline_hash, input_hash(key, context), *models),
            ).fetchone()
        if row is None:
            return None
        return Judgment(
            label=row["label"], quote=row["quote"], reasoning=row["reasoning"], method=row["method"],
            model_version=row["model_version"],
            probabilities=json.loads(row["probabilities"]) if row["probabilities"] else None,
            confidence=row["confidence"], score=row["score"],
        )

    def _flight(self, key: str, subject_id: str, context: dict, baseline: Baseline | None) -> tuple:
        return (self.candidate_id, subject_id, key, *self._scope(key, baseline), input_hash(key, context))

    @staticmethod
    def claim(flight: tuple) -> threading.Event | None:
        """None: the caller now owns this judgment and must release() it. An Event: another thread is asking."""
        with _INFLIGHT_LOCK:
            event = _INFLIGHT.get(flight)
            if event is None:
                _INFLIGHT[flight] = threading.Event()
            return event

    @staticmethod
    def release(flight: tuple) -> None:
        with _INFLIGHT_LOCK:
            event = _INFLIGHT.pop(flight, None)
        if event is not None:
            event.set()

    def cached(self, key: str, subject_id: str, context: dict, baseline: Baseline | None = None,
               judge: Judge | None = None) -> Judgment | None:
        """Saved judgment for exactly this input, from a model the judge would use (or a rule). No model call."""
        return self._lookup(key, subject_id, context, baseline, [*(judge or self.judge).models_for(key), RULE_MODEL])

    def _call(self, judge: Judge, key: str, subject_id: str, context: dict) -> Judgment:
        with self._lock:
            self.calls += 1
        with timing.span(timing.JUDGE, key) as t:
            judgment = ask(judge, question(key), {**context, "_subject_id": subject_id})
            t.update(method=judgment.method, model=judgment.model_version,
                     fallback=judgment.fallback_from is not None)
        return judgment

    def ask(self, key: str, subject_type: str, subject_id: str, context: dict,
            baseline: Baseline | None = None, *, judge: Judge | None = None,
            require_quote: bool = False) -> Judgment | None:
        """Cached judgment, or a new one from the judge. None if the judge failed (subcategory may be `error`).

        judge: route this question to a specific judge (e.g. Claude when a quote is needed).
        require_quote: a label above the none-option must carry a quote found in the input; otherwise
        retry once, then record the none-option by rule (both answers kept as not used).
        """
        judge = judge or self.judge
        hit = self.cached(key, subject_id, context, baseline, judge)
        if hit is not None:
            with self._lock:
                self.cache_hits += 1
            return hit
        flight = self._flight(key, subject_id, context, baseline)
        busy = self.claim(flight)
        if busy is not None:                      # someone is asking this right now: use their answer
            busy.wait(INFLIGHT_WAIT_SECONDS)
            hit = self.cached(key, subject_id, context, baseline, judge)
            if hit is not None:
                with self._lock:
                    self.cache_hits += 1
                return hit
            return self._ask(key, subject_type, subject_id, context, baseline, judge, require_quote)
        try:
            return self._ask(key, subject_type, subject_id, context, baseline, judge, require_quote)
        finally:
            self.release(flight)

    def _ask(self, key: str, subject_type: str, subject_id: str, context: dict, baseline: Baseline | None,
             judge: Judge, require_quote: bool) -> Judgment | None:
        q = question(key)
        try:
            judgment = self._call(judge, key, subject_id, context)
            if require_quote and judgment.label != q.none_option and not judgment.quote:
                unsupported = [judgment]
                judgment = self._call(judge, key, subject_id, context)
                if judgment.label != q.none_option and not judgment.quote:
                    unsupported.append(judgment)
                    for u in unsupported:
                        self._save(key, subject_type, subject_id, u, context, baseline, used=False)
                    reason = (f"{judgment.method} labelled it '{judgment.label}' twice without a quote found in "
                              f"the input, so it counts as {q.none_option}.")
                    final = Judgment(label=q.none_option, reasoning=reason, method="rule", model_version=RULE_MODEL)
                    self._save(key, subject_type, subject_id, final, context, baseline, used=True)
                    return final
                for u in unsupported:
                    self._save(key, subject_type, subject_id, u, context, baseline, used=False)
        except JudgeError as exc:
            msg = f"{key} for {subject_type} {subject_id}: {exc}"
            log.warning("judgment failed: %s", msg)
            with self._lock:
                self.errors.append(msg)
            if exc.discarded is not None:
                self._save(key, subject_type, subject_id, exc.discarded, context, baseline, used=False)
            return None
        if judgment.fallback_from is not None:
            self._save(key, subject_type, subject_id, judgment.fallback_from, context, baseline, used=False)
        self._save(key, subject_type, subject_id, judgment, context, baseline, used=True)
        return judgment

    def ask_group(self, keys: list[str], subject_type: str, subject_id: str, context: dict,
                  baseline: Baseline | None) -> dict[str, Judgment | None]:
        """Several questions about one subject in one judge call (specs/09 §2.3). Each answer is saved as
        its own row with the same cache key as a single ask; fallbacks and failures are per question."""
        flights = {k: self._flight(k, subject_id, context, baseline) for k in keys}
        keys = [k for k in keys if self.claim(flights[k]) is None]     # the rest are being asked elsewhere
        if not keys:
            return {}
        try:
            return self._ask_group(keys, subject_type, subject_id, context, baseline)
        finally:
            for k in keys:
                self.release(flights[k])

    def _ask_group(self, keys: list[str], subject_type: str, subject_id: str, context: dict,
                   baseline: Baseline | None) -> dict[str, Judgment | None]:
        with self._lock:
            self.calls += 1
        with timing.span(timing.JUDGE, "+".join(keys), group=len(keys)) as t:
            answers = self.judge.group(keys, {**context, "_subject_id": subject_id})
            t.update(method="group", fallbacks=sum(1 for a in answers.values()
                                                   if isinstance(a, Judgment) and a.fallback_from is not None))
        out: dict[str, Judgment | None] = {}
        for key in keys:
            j = answers.get(key)
            if isinstance(j, Judgment):
                if j.fallback_from is not None:
                    self._save(key, subject_type, subject_id, j.fallback_from, context, baseline, used=False)
                self._save(key, subject_type, subject_id, j, context, baseline, used=True)
                out[key] = j
                continue
            exc = j if isinstance(j, JudgeError) else JudgeError("no answer in the grouped call")
            msg = f"{key} for {subject_type} {subject_id}: {exc}"
            log.warning("judgment failed: %s", msg)
            with self._lock:
                self.errors.append(msg)
            if exc.discarded is not None:
                self._save(key, subject_type, subject_id, exc.discarded, context, baseline, used=False)
            out[key] = None
        return out

    def ask_batch(self, key: str, subject_type: str, items: list[tuple[str, dict]], baseline: Baseline | None,
                  judge: Judge) -> dict[str, Judgment | None]:
        """Several criteria in one call (specs/09 §2.2), each saved as its own row with its own cache key.

        Same rules as ask(require_quote=True): a label above the none-option needs a quote found in the
        input. A criterion that fails the check (or that the batch left out) is retried alone once; if it
        still fails, it is recorded as the none-option by rule. Failed answers are kept as not used.
        """
        flights = {sid: self._flight(key, sid, ctx, baseline) for sid, ctx in items}
        items = [(sid, ctx) for sid, ctx in items if self.claim(flights[sid]) is None]
        if not items:
            return {}
        try:
            return self._ask_batch(key, subject_type, items, baseline, judge)
        finally:
            for sid, _ in items:
                self.release(flights[sid])

    def _ask_batch(self, key: str, subject_type: str, items: list[tuple[str, dict]], baseline: Baseline | None,
                   judge: Judge) -> dict[str, Judgment | None]:
        q = question(key)
        with self._lock:
            self.calls += 1
        try:
            with timing.span(timing.JUDGE, key, batch=len(items)) as t:
                answers = judge.batch(key, [{**ctx, "_subject_id": sid} for sid, ctx in items])
                t.update(method="batch", model=next((a.model_version for a in answers if a), None))
        except JudgeError as exc:
            log.warning("batched %s failed, asking each criterion alone: %s", key, exc)
            answers = [None] * len(items)
        out: dict[str, Judgment | None] = {}
        for (sid, ctx), j in zip(items, answers):
            if j is not None and (j.label == q.none_option or j.quote):
                self._save(key, subject_type, sid, j, ctx, baseline, used=True)
                out[sid] = j
            else:
                out[sid] = self._solo_retry(key, subject_type, sid, ctx, baseline, judge, [j] if j else [])
        return out

    def _solo_retry(self, key: str, subject_type: str, subject_id: str, context: dict, baseline: Baseline | None,
                    judge: Judge, unsupported: list[Judgment]) -> Judgment | None:
        q = question(key)
        try:
            judgment = self._call(judge, key, subject_id, context)
        except JudgeError as exc:
            for u in unsupported:
                self._save(key, subject_type, subject_id, u, context, baseline, used=False)
            with self._lock:
                self.errors.append(f"{key} for {subject_type} {subject_id}: {exc}")
            return None
        if judgment.label != q.none_option and not judgment.quote:
            unsupported.append(judgment)
            judgment = Judgment(label=q.none_option, method="rule", model_version=RULE_MODEL,
                                reasoning=f"{unsupported[-1].method} labelled it '{unsupported[-1].label}' without a "
                                          f"quote found in the input, in the batch and alone, so it counts as "
                                          f"{q.none_option}.")
        for u in unsupported:
            self._save(key, subject_type, subject_id, u, context, baseline, used=False)
        self._save(key, subject_type, subject_id, judgment, context, baseline, used=True)
        return judgment

    def rule(self, key: str, subject_type: str, subject_id: str, label: str, context: dict,
             baseline: Baseline | None = None, reasoning: str | None = None) -> Judgment:
        """A label decided by code (e.g. skill pre-filter found no mention → missing, specs/05).

        Saved once per input; re-runs reuse the saved row.
        """
        if label not in question(key).options:
            raise ValueError(f"{label!r} is not an option of {key}")
        hit = self._lookup(key, subject_id, context, baseline, [RULE_MODEL])
        if hit is not None and hit.label == label:
            return hit
        judgment = Judgment(label=label, reasoning=reasoning, method="rule", model_version=RULE_MODEL)
        self._save(key, subject_type, subject_id, judgment, context, baseline, used=True)
        return judgment

    def _save(self, key: str, subject_type: str, subject_id: str, j: Judgment, context: dict,
              baseline: Baseline | None, used: bool) -> None:
        baseline_id, baseline_hash = self._scope(key, baseline)
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO judgments (id, candidate_id, baseline_id, baseline_hash, run_id, subject_type, "
                "subject_id, question, label, probabilities, quote, reasoning, method, model_version, used, "
                "confidence, score, input_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), self.candidate_id, baseline_id, baseline_hash, self.run_id, subject_type, subject_id,
                 key, j.label, json.dumps(j.probabilities) if j.probabilities else None, j.quote, j.reasoning,
                 j.method, j.model_version, int(used), j.confidence, j.score, input_hash(key, context), now_iso()),
            )


def clear_judgments(conn: sqlite3.Connection, candidate_id: str, baseline_id: str | None = None) -> int:
    """Mark saved judgments unused so the next run asks the judge again ("Re-judge", phase 7).

    With baseline_id, only that baseline's job-dependent judgments are cleared. Rows are kept (append-only).
    """
    sql = "UPDATE judgments SET used = 0 WHERE candidate_id = ? AND used = 1"
    params: list = [candidate_id]
    if baseline_id is not None:
        sql += " AND baseline_id = ?"
        params.append(baseline_id)
    with conn:
        return conn.execute(sql, params).rowcount


def judge_models() -> dict:
    """Model settings recorded on every run."""
    return {
        "llm_provider": config.LLM_PROVIDER,          # provider for the roles below that are not Jev
        "judge": config.JUDGE,
        "jev_model": config.JEV_MODEL if config.JUDGE == "jev" else None,
        "jev_confidence_threshold": config.JEV_CONFIDENCE_THRESHOLD if config.JUDGE == "jev" else None,
        "judge_fallback_model": config.JUDGE_FALLBACK_MODEL,
        "complexity_model": config.COMPLEXITY_MODEL,
        "extract_model": config.EXTRACT_MODEL,
        "plan_model": config.REASONING_MODEL,
    }


def get_judge() -> Judge:
    """Judge selected by JUDGE in .env (jev | claude)."""
    from judges.claude_judge import ClaudeJudge
    from judges.jev_judge import JevJudge

    if config.JUDGE == "claude":
        return ClaudeJudge()
    if config.JUDGE == "jev":
        if not config.JEV_API_KEY:
            raise ValueError("JUDGE=jev needs JEV_API_KEY in .env (or set JUDGE=claude).")
        return JevJudge(fallback=ClaudeJudge())
    raise ValueError(f"JUDGE must be 'jev' or 'claude', got {config.JUDGE!r}")
