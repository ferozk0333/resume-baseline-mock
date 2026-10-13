"""Thin wrapper over the Anthropic SDK for structured (Pydantic) outputs.

Shared by extraction and, later, the Claude judge and the gap report.
"""
from __future__ import annotations

from typing import Protocol, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from core import config

T = TypeVar("T", bound=BaseModel)

# Models that still accept sampling parameters. Newer models (Opus 4.7+, Sonnet 5+,
# Fable) reject a non-default temperature with a 400, so it is omitted for them.
_TEMPERATURE_MODELS = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-sonnet-4-6",
                       "claude-opus-4-5", "claude-opus-4-6", "claude-opus-4-1")


class LLMError(Exception):
    """A model call failed after retries, was refused, or never produced valid output."""


def sampling_kwargs(model: str) -> dict:
    """temperature 0 wherever the API allows it (CLAUDE.md rule 7).

    anthropic 1.x removed `temperature` from the method signatures (not from the
    API), so it is sent through `extra_body` for models that still accept it.
    """
    return {"extra_body": {"temperature": 0.0}} if model.startswith(_TEMPERATURE_MODELS) else {}


class StructuredLLM(Protocol):
    def parse(self, *, model: str, system: str, user: str, output_type: type[T]) -> T: ...



class ClaudeLLM:
    """messages.parse with SDK retries (2 on network/5xx) and one retry on validation failure (05 §D)."""

    def __init__(self, client: anthropic.Anthropic | None = None, max_tokens: int | None = None):
        self.client = client or anthropic.Anthropic(max_retries=2)
        self.max_tokens = max_tokens or config.EXTRACT_MAX_TOKENS

    def _call(self, model: str, system: str, user: str, output_type: type[T]) -> T:
        from core.limits import claude_slots
        try:
            with claude_slots:
                response = self.client.messages.parse(
                    model=model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    output_format=output_type,
                    **sampling_kwargs(model),
                )
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
        except ValidationError as exc:
            retry_user = (
                f"{user}\n\nYour previous answer failed schema validation:\n{exc}\n"
                "Return JSON that matches the schema exactly."
            )
            try:
                return self._call(model, system, retry_user, output_type)
            except ValidationError as exc2:
                raise LLMError(f"{model} output failed validation twice: {exc2}") from exc2
