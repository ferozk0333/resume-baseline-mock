"""Thin wrapper over the Anthropic SDK for structured (Pydantic) outputs.

Shared by extraction and, later, the Claude judge and the gap report.
"""
from __future__ import annotations

import re
from typing import Protocol, TypeVar

import anthropic
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



class ClaudeLLM:
    """Structured call with SDK retries (2 on network/5xx), one retry on validation failure (05 §D)
    and one retry on runaway output.

    The output is streamed so a runaway (the same characters repeated) is stopped within a
    few hundred characters instead of running to max_tokens. Clients without `messages.stream`
    (test fakes) use `messages.parse`.
    """

    def __init__(self, client: anthropic.Anthropic | None = None, max_tokens: int | None = None):
        self.client = client or anthropic.Anthropic(max_retries=2)
        self.max_tokens = max_tokens or config.EXTRACT_MAX_TOKENS

    def _request(self, model: str, system: str, user: str, output_type: type[T]) -> dict:
        return dict(model=model, max_tokens=self.max_tokens, system=system,
                    messages=[{"role": "user", "content": user}], output_format=output_type,
                    **sampling_kwargs(model))

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
