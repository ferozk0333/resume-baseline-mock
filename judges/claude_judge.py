"""Claude as judge: one structured call per judgment (specs/05 §B)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from core import config
from core.llm import ClaudeLLM, LLMError, StructuredLLM
from extraction.extract import load_prompt
from extraction.validate import QuoteIndex
from judges.base import Judgment, JudgeError, question, render_context
from judges.questions import Question

JUDGE_MAX_TOKENS = 4_096  # room for adaptive thinking on the reasoning model


@lru_cache(maxsize=None)
def output_model(key: str) -> type[BaseModel]:
    """Structured output with `label` constrained to the question's options."""
    q = question(key)
    label = (Literal[tuple(q.options)], Field(description="Exactly one of the options."))
    quote = (str, Field(description="Exact text copied from the input that supports the label; "
                                    "empty string for a none/missing option."))
    reasoning = (str, Field(description="One to three sentences."))
    fields = {"reasoning": reasoning, "label": label, "quote": quote} if q.reason_first \
        else {"label": label, "quote": quote, "reasoning": reasoning}
    return create_model(f"Judged_{key}", **fields)


def system_prompt(q: Question) -> str:
    options = "\n".join(f"- {name}: {desc}" for name, desc in q.options.items())
    order = ("\nWrite your reasoning first (2-3 sentences), then the label, then the quote."
             if q.reason_first else "")
    return f"{load_prompt('judge_system')}\n\nQuestion: {q.instructions}\nOptions:\n{options}{order}"


class ClaudeJudge:
    def __init__(self, llm: StructuredLLM | None = None):
        self.llm = llm or ClaudeLLM(max_tokens=JUDGE_MAX_TOKENS)

    @staticmethod
    def model_for(key: str) -> str:
        return config.COMPLEXITY_MODEL if key == "complexity" else config.JUDGE_FALLBACK_MODEL

    def models_for(self, key: str) -> list[str]:
        return [self.model_for(key)]

    def _judge(self, key: str, context: dict) -> Judgment:
        q = question(key)
        user = render_context(context)
        model = self.model_for(key)
        try:
            out = self.llm.parse(model=model, system=system_prompt(q), user=user, output_type=output_model(key))
        except LLMError as exc:
            raise JudgeError(str(exc)) from exc
        quote = out.quote.strip() or None
        if quote and (out.label == q.none_option or not QuoteIndex(user).contains(quote)):
            quote = None  # never store a quote that is not in the input
        return Judgment(label=out.label, quote=quote, reasoning=out.reasoning.strip() or None,
                        method="claude", model_version=model)

    def choice(self, question_key: str, options: list[str], context: dict) -> Judgment:
        if list(options) != list(question(question_key).options):
            raise ValueError(f"options for {question_key} must match the catalogue")
        return self._judge(question_key, context)

    def yes_no(self, question_key: str, context: dict) -> Judgment:
        return self._judge(question_key, context)
