"""Thin wrapper over the Anthropic SDK for structured (Pydantic) outputs.

Shared by extraction and, later, the Claude judge and the gap report.
"""
from __future__ import annotations

import json
import re
from typing import Protocol, TypeVar

import anthropic
import requests
from pydantic import BaseModel, ValidationError

from core import config, timing

T = TypeVar("T", bound=BaseModel)

# Models that still accept sampling parameters. Newer models (Opus 4.7+, Sonnet 5+,
# Fable) reject a non-default temperature with a 400, so it is omitted for them.
_TEMPERATURE_MODELS = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-sonnet-4-6",
                       "claude-opus-4-5", "claude-opus-4-6", "claude-opus-4-1")


class LLMError(Exception):
    """A model call failed after retries, was refused, or never produced valid output."""


class RunawayOutput(LLMError):
    """The model started repeating the same few characters (e.g. tabs copied from a PDF layout)."""


RUNAWAY_MESSAGE = ("the model's output got stuck repeating the same characters, twice. "
                   "Add the missing items by hand in the review step.")
# A unit of 1-24 characters repeated to fill at least the last 160 characters of output.
_RUNAWAY = re.compile(r"(.{1,24}?)\1{9,}$", re.S)


def is_runaway(tail: str, min_chars: int = 160) -> bool:
    m = _RUNAWAY.search(tail)
    return bool(m) and len(m.group(0)) >= min_chars


# Models that accept output_config.effort (platform docs, 2026-10). Haiku 4.5 does not.
_EFFORT_MODELS = ("claude-haiku-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-opus-5", "claude-opus-4-5",
                  "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8", "claude-fable", "claude-mythos")


def effort_kwargs(model: str, effort: str | None) -> dict:
    return {"output_config": {"effort": effort}} if effort and model.startswith(_EFFORT_MODELS) else {}


def sampling_kwargs(model: str) -> dict:
    """temperature 0 wherever the API allows it (CLAUDE.md rule 7).

    anthropic 1.x removed `temperature` from the method signatures (not from the
    API), so it is sent through `extra_body` for models that still accept it.
    """
    return {"extra_body": {"temperature": 0.0}} if model.startswith(_TEMPERATURE_MODELS) else {}


def note_usage(response) -> None:
    """Token counts of a response, for the run's timing report."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    counts = {"input_tokens": getattr(usage, "input_tokens", None),
              "output_tokens": getattr(usage, "output_tokens", None),
              "cache_read_tokens": getattr(usage, "cache_read_input_tokens", None),
              "cache_write_tokens": getattr(usage, "cache_creation_input_tokens", None)}
    timing.note(**{k: v for k, v in counts.items() if isinstance(v, int)})


class StructuredLLM(Protocol):
    def parse(self, *, model: str, system: str, user: str, output_type: type[T]) -> T: ...



RETRIED_STATUSES = {408, 409, 429}   # plus every 5xx: the SDK retries these (max_retries)


def count_response(response) -> None:
    """httpx response hook: count 429/529 and the responses the SDK will retry, for the run's timings."""
    status = response.status_code
    if status in (429, 529):
        timing.count(f"claude_{status}")
    if status in RETRIED_STATUSES or status >= 500:
        timing.count("claude_retried_responses")


def default_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(max_retries=2,
                               http_client=anthropic.DefaultHttpxClient(event_hooks={"response": [count_response]}))


class ClaudeLLM:
    """Structured call with SDK retries (2 on network/5xx), one retry on validation failure (05 §D)
    and one retry on runaway output.

    The output is streamed so a runaway (the same characters repeated) is stopped within a
    few hundred characters instead of running to max_tokens. Clients without `messages.stream`
    (test fakes) use `messages.parse`.
    """

    def __init__(self, client: anthropic.Anthropic | None = None, max_tokens: int | None = None,
                 effort: str | None = None):
        self.client = client or default_client()
        self.max_tokens = max_tokens or config.EXTRACT_MAX_TOKENS
        self.effort = effort or None

    def _request(self, model: str, system: str, user: str, output_type: type[T]) -> dict:
        return dict(model=model, max_tokens=self.max_tokens, system=system,
                    messages=[{"role": "user", "content": user}], output_format=output_type,
                    **sampling_kwargs(model), **effort_kwargs(model, self.effort))

    def _streamed(self, request: dict):
        tail = ""
        with self.client.messages.stream(**request) as stream:
            for event in stream:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    tail = (tail + event.delta.text)[-400:]
                    if is_runaway(tail):
                        timing.note(runaway=True)
                        raise RunawayOutput(f"{request['model']} output started repeating {tail[-40:]!r}")
            return stream.get_final_message()

    def _call(self, model: str, system: str, user: str, output_type: type[T]) -> T:
        from core.limits import claude_slots
        request = self._request(model, system, user, output_type)
        try:
            with claude_slots, timing.span(timing.MODEL, "claude", model=model):
                if hasattr(self.client.messages, "stream"):
                    response = self._streamed(request)
                else:
                    response = self.client.messages.parse(**request)
                note_usage(response)
        except anthropic.APIError as exc:
            raise LLMError(f"{model} call failed: {exc.__class__.__name__}: {exc}") from exc
        if response.stop_reason == "refusal":
            raise LLMError(f"{model} declined the request")
        if response.stop_reason == "max_tokens":
            raise LLMError(f"{model} output was cut off at max_tokens")
        if response.parsed_output is None:
            raise LLMError(f"{model} returned no parsed output")
        return response.parsed_output

    def parse(self, *, model: str, system: str, user: str, output_type: type[T]) -> T:
        try:
            return self._call(model, system, user, output_type)
        except RunawayOutput:
            try:
                return self._call(model, system, user, output_type)
            except RunawayOutput as exc:
                raise RunawayOutput(RUNAWAY_MESSAGE) from exc
        except ValidationError as exc:
            retry_user = (
                f"{user}\n\nYour previous answer failed schema validation:\n{exc}\n"
                "Return JSON that matches the schema exactly."
            )
            try:
                return self._call(model, system, retry_user, output_type)
            except ValidationError as exc2:
                raise LLMError(f"{model} output failed validation twice: {exc2}") from exc2


# --- Groq (OpenAI-compatible) ---------------------------------------------------------------------

GROQ_PREFIX = "groq/"


def is_groq(model: str) -> bool:
    return model.startswith(GROQ_PREFIX)


def strict_schema(schema: dict) -> dict:
    """Pydantic JSON schema → Groq strict mode: every object closed, every property required, no defaults."""
    def fix(node):
        if isinstance(node, dict):
            node.pop("default", None)
            for key in ("minLength", "maxLength", "minItems", "maxItems", "pattern", "format"):
                node.pop(key, None)
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for v in node.values():
                fix(v)
        elif isinstance(node, list):
            for v in node:
                fix(v)
        return node
    return fix(json.loads(json.dumps(schema)))


class GroqLLM:
    """Structured call to Groq with strict JSON schema; same contract as ClaudeLLM.parse.

    Each request is capped to fit the tier's tokens-per-minute (prompt + completion), 429s wait for
    `retry-after`, and the same validation and runaway retries apply as for Claude.
    """

    def __init__(self, session=None, api_key: str | None = None, max_tokens: int | None = None,
                 effort: str | None = None, max_429_retries: int = 6):
        import requests
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else config.GROQ_API_KEY
        self.max_tokens = max_tokens or config.EXTRACT_MAX_TOKENS
        self.effort = effort or config.GROQ_REASONING_EFFORT
        self.max_429_retries = max_429_retries

    def _budget(self, system: str, user: str) -> int:
        est_input = (len(system) + len(user)) // 3 + 200            # conservative chars → tokens
        return min(self.max_tokens, config.GROQ_TPM - est_input)

    def _call(self, model: str, system: str, user: str, output_type: type[T]) -> T:
        import time as _time
        from core.limits import groq_slots
        if not self.api_key:
            raise LLMError("GROQ_API_KEY is not set in .env.")
        budget = self._budget(system, user)
        if budget < 500:
            raise LLMError(f"input too long for one {model} request within GROQ_TPM={config.GROQ_TPM}")
        body = {"model": model.removeprefix(GROQ_PREFIX), "max_completion_tokens": budget, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": output_type.__name__, "strict": True,
                    "schema": strict_schema(output_type.model_json_schema())}}}
        if "gpt-oss" in model and self.effort:
            body["reasoning_effort"] = self.effort
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(self.max_429_retries + 1):
            with groq_slots, timing.span(timing.MODEL, "groq", model=model):
                resp = self.session.post(config.GROQ_URL, json=body, headers=headers, timeout=120)
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage") or {}
                    timing.note(input_tokens=int(usage.get("prompt_tokens") or 0),
                                output_tokens=int(usage.get("completion_tokens") or 0))
                    break
            if resp.status_code == 429 and attempt < self.max_429_retries:
                timing.count("groq_429")
                _time.sleep(min(float(resp.headers.get("retry-after") or 5), 60))
                continue
            raise LLMError(f"{model} call failed: HTTP {resp.status_code}: {resp.text[:300]}")
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        if is_runaway(content[-400:]):
            raise RunawayOutput(f"{model} output started repeating {content[-40:]!r}")
        if choice.get("finish_reason") == "length":
            raise LLMError(f"{model} output was cut off at {budget} tokens (GROQ_TPM={config.GROQ_TPM})")
        return output_type.model_validate_json(content)

    parse = ClaudeLLM.parse      # same retry rules (validation once, runaway once)


class RoutedLLM:
    """Sends each call to Claude, Gemini ("gemini/…") or Groq ("groq/…") by its model name."""

    def __init__(self, max_tokens: int | None = None, effort: str | None = None):
        self.max_tokens, self.effort = max_tokens, effort
        self._claude = self._groq = None

    def _for(self, model: str):
        if is_gemini(model):
            self._gemini = getattr(self, "_gemini", None) or GeminiLLM(max_tokens=self.max_tokens)
            return self._gemini
        if is_groq(model):
            self._groq = self._groq or GroqLLM(max_tokens=self.max_tokens)
            return self._groq
        self._claude = self._claude or ClaudeLLM(max_tokens=self.max_tokens, effort=self.effort)
        return self._claude

    def parse(self, *, model: str, system: str, user: str, output_type: type[T]) -> T:
        return self._for(model).parse(model=model, system=system, user=user, output_type=output_type)


# --- Gemini (Google AI Studio) -------------------------------------------------------------------------

GEMINI_PREFIX = "gemini/"


def is_gemini(model: str) -> bool:
    return model.startswith(GEMINI_PREFIX)


class DailyLimitReached(LLMError):
    """A free-tier daily quota is used up. Saved extractions and judgments are kept: run again after the reset."""


def pacific_day():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/Los_Angeles")).date()


def daily_limit_message(model: str) -> str:
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/Los_Angeles"))
    reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = (reset - now).total_seconds() / 3600
    return (f"Daily free-tier limit for {model} reached. Everything extracted and judged so far is saved; "
            f"run again after midnight Pacific (in about {hours:.0f} h) and it continues where it stopped.")


class RateLimiter:
    """Requests and tokens per minute (in memory) and requests per day (SQLite, survives restarts), per model.

    acquire() blocks until the request fits the per-minute limits, or raises DailyLimitReached.
    """

    def __init__(self, provider: str, rpm: int, tpm: int, rpd: int, db_path: str | None = None, clock=None,
                 sleep=None):
        import threading
        import time as _time
        self.provider, self.rpm, self.tpm, self.rpd = provider, rpm, tpm, rpd
        self.db_path = db_path
        self.clock = clock or _time.monotonic
        self.sleep = sleep or _time.sleep
        self._lock = threading.Lock()
        self._recent: dict[str, list[tuple[float, int]]] = {}       # model → [(time, tokens)]

    def _conn(self):
        from core import db
        return db.connect(self.db_path or config.DB_PATH)

    def used_today(self, model: str) -> int:
        conn = self._conn()
        try:
            row = conn.execute("SELECT requests FROM provider_usage WHERE provider = ? AND model = ? AND day = ?",
                               (self.provider, model, pacific_day().isoformat())).fetchone()
            return row[0] if row else 0
        finally:
            conn.close()

    def _record(self, model: str, tokens: int) -> None:
        conn = self._conn()
        try:
            with conn:
                conn.execute("INSERT INTO provider_usage (provider, model, day, requests, tokens) VALUES (?, ?, ?, 1, ?) "
                             "ON CONFLICT(provider, model, day) DO UPDATE SET requests = requests + 1, "
                             "tokens = tokens + excluded.tokens",
                             (self.provider, model, pacific_day().isoformat(), tokens))
        finally:
            conn.close()

    def exhaust_today(self, model: str) -> None:
        """The API said the daily quota is gone: remember it so later calls stop at once."""
        conn = self._conn()
        try:
            with conn:
                conn.execute("INSERT INTO provider_usage (provider, model, day, requests, tokens) VALUES (?, ?, ?, ?, 0) "
                             "ON CONFLICT(provider, model, day) DO UPDATE SET requests = MAX(requests, excluded.requests)",
                             (self.provider, model, pacific_day().isoformat(), self.rpd))
        finally:
            conn.close()

    def acquire(self, model: str, tokens: int) -> None:
        if self.used_today(model) >= self.rpd:
            raise DailyLimitReached(daily_limit_message(model))
        while True:
            with self._lock:
                now = self.clock()
                window = [(t, n) for t, n in self._recent.get(model, []) if now - t < 60]
                self._recent[model] = window
                fits = len(window) < self.rpm and sum(n for _, n in window) + tokens <= self.tpm
                if fits or not window:
                    window.append((now, tokens))
                    break
                wait = 60 - (now - window[0][0]) + 0.05
            timing.count(f"{self.provider}_throttled")
            self.sleep(max(wait, 0.05))
        self._record(model, tokens)


_GEMINI_LIMITER: RateLimiter | None = None


def gemini_limiter() -> RateLimiter:
    global _GEMINI_LIMITER
    if _GEMINI_LIMITER is None:
        _GEMINI_LIMITER = RateLimiter("gemini", config.GEMINI_RPM, config.GEMINI_TPM, config.GEMINI_RPD)
    return _GEMINI_LIMITER


def gemini_schema(schema: dict) -> dict:
    """Pydantic JSON schema → the subset Gemini accepts: $defs inlined, no defaults."""
    defs = schema.get("$defs", {})

    def fix(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return fix(json.loads(json.dumps(defs[node["$ref"].split("/")[-1]])))
            out = {}
            for k, v in node.items():
                if k in ("$defs", "default", "title"):          # schema annotations, not field names
                    continue
                out[k] = {name: fix(sub) for name, sub in v.items()} if k == "properties" else fix(v)
            return out
        if isinstance(node, list):
            return [fix(v) for v in node]
        return node
    return fix(schema)


class GeminiLLM:
    """Structured call to Gemini (generateContent with a JSON schema); same contract as ClaudeLLM.parse.

    The system prompt goes in systemInstruction and the document only in the user turn (CLAUDE.md rule 9).
    """

    RETRY_STATUSES = {500, 502, 503, 504}

    def __init__(self, session=None, api_key: str | None = None, max_tokens: int | None = None,
                 limiter: RateLimiter | None = None, max_retries: int = 6, sleep=None):
        import requests
        import time as _time
        self.session = session or requests.Session()
        self.api_key = api_key if api_key is not None else config.GEMINI_API_KEY
        self.max_tokens = max_tokens or config.EXTRACT_MAX_TOKENS
        self.limiter = limiter
        self.max_retries = max_retries
        self.sleep = sleep or _time.sleep

    @staticmethod
    def _retry_delay(data: dict) -> float:
        for d in (data.get("error") or {}).get("details", []):
            if "retryDelay" in d:
                return float(str(d["retryDelay"]).rstrip("s") or 5)
        return 5.0

    @staticmethod
    def _is_daily(data: dict) -> bool:
        text = json.dumps(data.get("error") or {})
        return "PerDay" in text or "per day" in text.lower()

    def _call(self, model: str, system: str, user: str, output_type: type[T]) -> T:
        from core.limits import gemini_slots
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not set in .env.")
        name = model.removeprefix(GEMINI_PREFIX)
        limiter = self.limiter or gemini_limiter()
        gen = {"responseMimeType": "application/json",
               "responseJsonSchema": gemini_schema(output_type.model_json_schema()),
               "temperature": 0, "maxOutputTokens": self.max_tokens}
        if config.GEMINI_THINKING_LEVEL:
            gen["thinkingConfig"] = {"thinkingLevel": config.GEMINI_THINKING_LEVEL}
        body = {"systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": gen}
        estimate = (len(system) + len(user)) // 3 + self.max_tokens // 4
        for attempt in range(self.max_retries + 1):
            limiter.acquire(name, estimate)
            resp, data = None, {}
            with gemini_slots, timing.span(timing.MODEL, "gemini", model=model):
                try:
                    resp = self.session.post(config.GEMINI_URL.format(model=name), json=body,
                                             headers={"x-goog-api-key": self.api_key}, timeout=(10, 300))
                    data = resp.json() if resp.text else {}
                except requests.RequestException as exc:      # timeouts on a busy free tier: retry
                    problem = f"no answer ({exc.__class__.__name__})"
                if resp is not None and resp.status_code == 200:
                    usage = data.get("usageMetadata") or {}
                    timing.note(input_tokens=int(usage.get("promptTokenCount") or 0),
                                output_tokens=int(usage.get("candidatesTokenCount") or 0)
                                + int(usage.get("thoughtsTokenCount") or 0))
                    break
            if resp is not None:
                problem = f"HTTP {resp.status_code}"
                if resp.status_code == 429:
                    timing.count("gemini_429")
                    if self._is_daily(data):
                        limiter.exhaust_today(name)
                        raise DailyLimitReached(daily_limit_message(name))
                elif resp.status_code not in self.RETRY_STATUSES:
                    raise LLMError(f"{model} call failed: HTTP {resp.status_code}: {resp.text[:300]}")
            if attempt == self.max_retries:
                raise LLMError(f"{model} call failed after {attempt + 1} attempts: {problem}")
            timing.count("gemini_retries")
            rate_limited = resp is not None and resp.status_code == 429
            self.sleep(min(self._retry_delay(data) if rate_limited else 2 ** attempt * 2, 60))
        candidate = (data.get("candidates") or [{}])[0]
        if candidate.get("finishReason") not in (None, "STOP"):
            if candidate.get("finishReason") == "MAX_TOKENS":
                raise LLMError(f"{model} output was cut off at max_tokens")
            raise LLMError(f"{model} stopped: {candidate.get('finishReason')}")
        parts = (candidate.get("content") or {}).get("parts") or []
        content = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if is_runaway(content[-400:]):
            raise RunawayOutput(f"{model} output started repeating {content[-40:]!r}")
        return output_type.model_validate_json(content)

    parse = ClaudeLLM.parse      # same retry rules (validation once, runaway once)
