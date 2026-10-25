"""Model routing (specs/18 §2): an ordered provider/model list per task, a limiter per model, immediate failover,
a short wait only when every model is busy, and a fast failure when every model is exhausted.

A call names either a model ("gemini/gemini-3.8-flash", "groq/openai/gpt-oss-120b", a Claude id) or a task route
("route:judge_single"). A model alone is a one-model route, so every call follows the same rules:

- 429, 5xx, a timeout or an unusable answer: the call goes to the next model with capacity at once (no sleep);
- the provider's limit answer wins over our counters: the model is exhausted until the reset the provider states
  (Google's retryDelay or "per day" quota, Groq's retry-after and x-ratelimit-* headers);
- only when every model in the list is busy, wait min(the earliest free slot, ROUTE_MAX_WAIT_S) with jitter;
- at most ROUTE_MAX_ATTEMPTS model calls per request; every model exhausted for the day: fail fast with a message
  that starts "Daily free-tier limit" (the run is then reported as paused, saved work is kept).

The model that answered is recorded (`last_model()`), so judgments and extractions keep the real provider/model.
"""
from __future__ import annotations

import contextvars
import logging
import random
from contextlib import contextmanager
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from core import config, timing

log = logging.getLogger("rightstaff.routing")
ROUTE_PREFIX = "route:"
PACIFIC = ZoneInfo("America/Los_Angeles")


class Throttled(Exception):
    """The provider refused for a limit (429). `retry_after` seconds, or `daily` (until the stated reset)."""

    def __init__(self, message: str, retry_after: float | None = None, daily: bool = False,
                 reset_after: float | None = None):
        super().__init__(message)
        self.retry_after, self.daily, self.reset_after = retry_after, daily, reset_after


class Overloaded(Exception):
    """5xx, a timeout or no answer: the provider did not take the request (not counted toward the day)."""


class Unusable(Exception):
    """The model can't do this request (unknown model id, unsupported request, input too long)."""


def provider_of(model: str) -> str:
    return "gemini" if model.startswith("gemini/") else "groq" if model.startswith("groq/") else "claude"


def is_route(model: str) -> bool:
    return model.startswith(ROUTE_PREFIX)


def models_of(model: str) -> list[str]:
    """The ordered models of a route, or [model]."""
    if is_route(model):
        task = model.removeprefix(ROUTE_PREFIX)
        if task not in config.ROUTES:
            raise ValueError(f"unknown route {task!r} (config.ROUTES)")
        return list(config.ROUTES[task])
    return [model]


def task_of(model: str) -> str:
    return model.removeprefix(ROUTE_PREFIX) if is_route(model) else "direct"


# --- Answer budget and routed calls ---------------------------------------------------------------------------------

_budget: contextvars.ContextVar[int | None] = contextvars.ContextVar("answer_budget", default=None)
_routed: contextvars.ContextVar[bool] = contextvars.ContextVar("routed", default=False)
_timeout: contextvars.ContextVar[float | None] = contextvars.ContextVar("call_timeout", default=None)


def current_timeout(default: float) -> float:
    """The read timeout for this call: the task's (TASK_TIMEOUT_S) when it has one, else the provider's."""
    return _timeout.get() or default


@contextmanager
def answer_budget(tokens: int | None):
    """Max output tokens for the calls inside (the caller knows the kind of question; clients read it)."""
    token = _budget.set(tokens)
    try:
        yield
    finally:
        _budget.reset(token)


def current_budget(default: int) -> int:
    return _budget.get() or default


def routed() -> bool:
    """Inside a routed call, a client makes exactly one attempt: an invalid or cut-off answer goes to the next model."""
    return _routed.get()


# --- Which model answered ----------------------------------------------------------------------------------------

_last = threading.local()


def last_model(default: str | None = None) -> str | None:
    """The model that answered the last routed call on this thread (judgments and extractions record it)."""
    return getattr(_last, "model", None) or default


def _set_last(model: str | None) -> None:
    _last.model = model


def clear_last() -> None:
    """Call before a parse whose answering model you will read: a client that is not routed leaves it empty."""
    _last.model = None


def next_pacific_midnight(now: float) -> float:
    t = datetime.fromtimestamp(now, PACIFIC)
    return (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def pacific_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), PACIFIC).date().isoformat()


# --- Limiter ---------------------------------------------------------------------------------------------------------

@dataclass
class _State:
    window: list = field(default_factory=list)       # [(time, tokens)] in the last minute
    inflight: int = 0
    until: float = 0.0                               # exhausted until (provider said so, or overloaded)
    reason: str = ""


class ModelLimiter:
    """Requests and tokens per minute (memory) and per day (SQLite provider_usage, survives restarts), per model.
    Never blocks: `check` says whether a request fits now, or how long until it would."""

    def __init__(self, db_path: str | None = None, clock=None):
        self.db_path = db_path
        self.clock = clock or time.time
        self._lock = threading.Lock()
        self._state: dict[str, _State] = {}

    def _conn(self):
        from core import db
        return db.connect(self.db_path or config.DB_PATH)

    def _st(self, model: str) -> _State:
        return self._state.setdefault(model, _State())

    @staticmethod
    def _key(model: str) -> str:
        """Usage rows: Gemini models by bare name, as before routing, so the day's counts carry over."""
        return model.removeprefix("gemini/")

    def used_today(self, model: str) -> tuple[int, int]:
        conn = self._conn()
        try:
            row = conn.execute("SELECT requests, tokens FROM provider_usage WHERE provider = ? AND model = ? AND day = ?",
                               (provider_of(model), self._key(model), pacific_day(self.clock()))).fetchone()
            return (row[0], row[1] or 0) if row else (0, 0)
        finally:
            conn.close()

    def _persist(self, model: str, requests: int, tokens: int, *, at_least: bool = False) -> None:
        conn = self._conn()
        try:
            with conn:
                if at_least:                                 # the provider said the day is used up
                    conn.execute("INSERT INTO provider_usage (provider, model, day, requests, tokens) VALUES (?, ?, ?, ?, 0) "
                                 "ON CONFLICT(provider, model, day) DO UPDATE SET requests = MAX(requests, excluded.requests)",
                                 (provider_of(model), self._key(model), pacific_day(self.clock()), requests))
                else:
                    conn.execute("INSERT INTO provider_usage (provider, model, day, requests, tokens) VALUES (?, ?, ?, ?, ?) "
                                 "ON CONFLICT(provider, model, day) DO UPDATE SET requests = MAX(requests + excluded.requests, 0), "
                                 "tokens = MAX(tokens + excluded.tokens, 0)",
                                 (provider_of(model), self._key(model), pacific_day(self.clock()), max(requests, 0), max(tokens, 0)))
        finally:
            conn.close()

    def check(self, model: str, tokens: int) -> tuple[str, float]:
        """("ok", 0) | ("busy", seconds until it may fit) | ("exhausted", seconds until reset) | ("too_big", 0)."""
        lim = config.model_limits(model)
        now = self.clock()
        if lim.tpm and tokens > lim.tpm:
            return "too_big", 0.0
        with self._lock:
            st = self._st(model)
            if st.until > now:
                return ("exhausted" if st.until - now > config.ROUTE_BUSY_BUDGET_S else "busy"), st.until - now
        used, used_tokens = self.used_today(model)
        if (lim.rpd and used >= lim.rpd) or (lim.tpd and used_tokens + tokens > lim.tpd):
            return "exhausted", next_pacific_midnight(now) - now
        with self._lock:
            st = self._st(model)
            st.window = [(t, n) for t, n in st.window if now - t < 60]
            if st.inflight >= config.model_concurrency(model):
                return "busy", 0.5
            if lim.rpm and len(st.window) >= lim.rpm:
                return "busy", 60 - (now - st.window[0][0]) + 0.05
            if lim.tpm and st.window and sum(n for _, n in st.window) + tokens > lim.tpm:
                return "busy", 60 - (now - st.window[0][0]) + 0.05
        return "ok", 0.0

    def take(self, model: str, tokens: int) -> bool:
        """Reserve a slot if the request still fits (another thread may have taken it since `check`)."""
        if self.check(model, tokens)[0] != "ok":
            return False
        with self._lock:
            st = self._st(model)
            st.window.append((self.clock(), tokens))
            st.inflight += 1
        self._persist(model, 1, tokens)
        return True

    def release(self, model: str) -> None:
        with self._lock:
            st = self._st(model)
            st.inflight = max(0, st.inflight - 1)

    def refund(self, model: str, tokens: int = 0) -> None:
        """The provider did not take the request (overloaded / no answer): it does not count today."""
        conn = self._conn()
        try:
            with conn:
                conn.execute("UPDATE provider_usage SET requests = MAX(requests - 1, 0), tokens = MAX(tokens - ?, 0) "
                             "WHERE provider = ? AND model = ? AND day = ?",
                             (tokens, provider_of(model), self._key(model), pacific_day(self.clock())))
        finally:
            conn.close()

    def exhaust(self, model: str, seconds: float, reason: str, *, daily: bool = False) -> None:
        now = self.clock()
        until = next_pacific_midnight(now) if daily and seconds <= 0 else now + max(seconds, 0.5)
        with self._lock:
            st = self._st(model)
            st.until, st.reason = max(st.until, until), reason
        if daily:
            lim = config.model_limits(model)
            if lim.rpd:
                self._persist(model, lim.rpd, 0, at_least=True)

    def observe_headers(self, model: str, headers) -> None:
        """Groq's x-ratelimit-* headers on any answer: zero requests left today → exhausted until the reset."""
        remaining = headers.get("x-ratelimit-remaining-requests")
        if remaining is not None and str(remaining).strip() == "0":
            self.exhaust(model, parse_duration(headers.get("x-ratelimit-reset-requests")) or 0, "no requests left today",
                         daily=True)

    def reset(self) -> None:
        with self._lock:
            self._state.clear()


def parse_duration(text) -> float | None:
    """Groq reset values: "2m59.56s", "7.66s", "1h2m3s", "350ms"."""
    if text is None:
        return None
    import re
    s = str(text).strip()
    try:
        return float(s)
    except ValueError:
        pass
    total, found = 0.0, False
    for num, unit in re.findall(r"([\d.]+)\s*(ms|h|m|s)", s):
        found = True
        total += float(num) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total if found else None


LIMITER = ModelLimiter()


# --- Router ----------------------------------------------------------------------------------------------------------

def estimate_tokens(system: str, user: str, max_tokens: int) -> int:
    return (len(system) + len(user)) // 3 + max_tokens // 4


STEP_WORDS = {"extract_document": "reading documents", "extract_web": "reading project pages",
              "judge_single": "scoring", "judge_grouped": "scoring", "plan": "writing the plan", "direct": "this step"}


def daily_message(task: str, models: list[str], now: float) -> str:
    """Shown to people (wait screens, Review): names the step, never a model or provider. The log line names them."""
    hours = (next_pacific_midnight(now) - now) / 3600
    return (f"Daily free-tier limit reached for every model that can do this step ({STEP_WORDS.get(task, task)}). "
            f"Everything read and scored so far is saved: try again after the reset (midnight Pacific, in about "
            f"{hours:.0f} h) and it continues where it stopped.")


class Router:
    """Sends one structured call along a route. `client_for(model)` returns the provider client (one attempt per
    parse; a provider client never sleeps)."""

    def __init__(self, client_for, limiter: ModelLimiter | None = None, sleep=None, clock=None, max_tokens: int = 0):
        self.client_for = client_for
        self.limiter = limiter or LIMITER
        self.sleep = sleep or time.sleep
        self.clock = clock or time.time
        self.max_tokens = max_tokens or config.EXTRACT_MAX_TOKENS

    def parse(self, *, model: str, system: str, user: str, output_type):
        _set_last(None)
        return self._run(task_of(model), models_of(model), system, user, output_type)

    def _pick(self, models: list[str], tokens: int, tried: set[str]) -> tuple[str | None, list[tuple[str, str, float]]]:
        seen = []
        for m in models:
            if m in tried:
                continue
            state, wait = self.limiter.check(m, tokens)
            if state == "ok" and self.limiter.take(m, tokens):
                return m, seen
            seen.append((m, state if state != "ok" else "busy", wait))
        return None, seen

    def _run(self, task: str, models: list[str], system: str, user: str, output_type):
        from providers.llm import LLMError, DailyLimitReached
        tokens = estimate_tokens(system, user, current_budget(self.max_tokens))
        tried: set[str] = set()
        attempts, waited, last_error, bad = 0, 0.0, None, 0
        rounds, overload_only = 0, True                        # this round failed only on overloads (5xx, timeouts)
        while True:
            m, seen = self._pick(models, tokens, tried)
            if m is None:
                busy = [(x, w) for x, s, w in seen if s == "busy"]
                if not busy:                                   # no untried model can take it now
                    if any(s == "exhausted" for _, s, _ in seen) or (
                            isinstance(last_error, Throttled) and all(self.limiter.check(x, tokens)[0] == "exhausted"
                                                                      for x in models)):
                        log.warning("fail fast: %s: every model is exhausted (%s)", task,
                                    "; ".join(f"{x} {s}" for x, s, _ in seen) or "all tried")
                        timing.count("route_fail_fast")
                        raise DailyLimitReached(daily_message(task, models, self.clock())) from last_error
                    if last_error is not None:
                        raise LLMError(f"{task}: no model could answer ({last_error})") from last_error
                    raise LLMError(f"{task}: the request is too long for every model in the route ({', '.join(models)})")
                pause = min(min(w for _, w in busy), config.ROUTE_MAX_WAIT_S) + random.uniform(0, 0.25)
                if waited + pause > config.ROUTE_MAX_TOTAL_WAIT_S:
                    raise LLMError(f"{task}: every model stayed busy for {waited:.0f} s; try again in a minute")
                log.info("all busy: %s waits %.1f s (%s)", task, pause, ", ".join(x for x, _ in busy))
                timing.count("route_waits")
                self.sleep(pause)
                waited += pause
                continue
            attempts += 1
            try:
                out = self._attempt(task, m, system, user, output_type, tokens)
                _set_last(m)
                return out
            except Throttled as exc:
                last_error = exc
                overload_only = False
                self.limiter.exhaust(m, (exc.reset_after or 0) if exc.daily else (exc.retry_after or 5.0),
                                     str(exc), daily=exc.daily)
                why = "daily limit" if exc.daily else f"limit, retry after {exc.retry_after or 5:.0f} s"
            except Overloaded as exc:
                last_error = exc
                self.limiter.refund(m, tokens)
                self.limiter.exhaust(m, config.OVERLOAD_COOLDOWN_S, str(exc))
                why = f"overloaded ({exc})"
            except Unusable as exc:                            # e.g. unknown model id, rejected request format
                last_error = exc
                overload_only = False
                self.limiter.refund(m, tokens)
                self.limiter.exhaust(m, config.UNUSABLE_REST_S, str(exc)[:200])
                why = f"unusable ({str(exc)[:160]})"
            except DailyLimitReached:
                raise
            except (LLMError, ValidationError) as exc:         # invalid or cut-off answer, refused
                overload_only = False
                last_error, bad = exc, bad + 1
                why = f"no valid answer ({str(exc)[:160]})"
                if bad >= 2:                                    # once on the next model, then missing with the reason
                    self.limiter.release(m)
                    tried.add(m)
                    log.warning("failover: %s %s → none (no valid answer from two models: %s)", task, m, why)
                    raise LLMError(f"{task}: no valid answer from two models ({str(exc)[:200]})") from exc
            finally:
                self.limiter.release(m)
            tried.add(m)
            nxt = next((x for x in models if x not in tried), None)
            timing.count("failover")
            log.warning("failover: %s %s → %s (%s)", task, m, nxt or "none left", why)
            if attempts >= config.ROUTE_MAX_ATTEMPTS or nxt is None:
                if overload_only and rounds < config.ROUTE_OVERLOAD_RETRY_ROUNDS:
                    rounds, attempts = rounds + 1, 0               # a 503 storm usually passes in seconds:
                    tried.clear()                                  # once more, after the models have rested
                    log.warning("retry: %s: every model was overloaded; one more round once they have rested", task)
                    timing.count("route_overload_retry")
                    continue
                if isinstance(last_error, Throttled) and all(self.limiter.check(x, tokens)[0] == "exhausted" for x in models):
                    log.warning("fail fast: %s: every model is exhausted", task)
                    timing.count("route_fail_fast")
                    raise DailyLimitReached(daily_message(task, models, self.clock())) from last_error
                raise LLMError(f"{task}: no model could answer after {attempts} attempt(s): {last_error}") from last_error

    def _attempt(self, task: str, model: str, system: str, user: str, output_type, tokens: int):
        client = self.client_for(model)
        token = _routed.set(True)
        ttoken = _timeout.set(config.TASK_TIMEOUT_S.get(task))
        try:
            with timing.span(timing.MODEL, provider_of(model), model=model, method=task):
                return client.parse(model=model, system=system, user=user, output_type=output_type)
        finally:
            _timeout.reset(ttoken)
            _routed.reset(token)
