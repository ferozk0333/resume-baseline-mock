"""Claude as judge: one structured call per judgment (specs/05 §B)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, create_model

from core import config
from providers.routing import answer_budget, clear_last, last_model
from providers.llm import LLMError, RoutedLLM, StructuredLLM, is_gemini, is_groq
from extraction.extract import load_prompt
from extraction.validate import QuoteIndex
from core.text import wrap_tag
from judges.base import Judgment, JudgeError, _as_text, question, render_context
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


@lru_cache(maxsize=None)
def batch_output_model(key: str) -> type[BaseModel]:
    """A list of per-criterion answers, each with `label` constrained to the question's options."""
    q = question(key)
    criterion = (str, Field(description="The criterion id exactly as given, e.g. C1."))
    label = (Literal[tuple(q.options)], Field(description="Exactly one of the options."))
    quote = (str, Field(description="Exact text copied from the input that supports the label; "
                                    "empty string for a none/missing option."))
    if q.reason_first:            # complexity reasons first, in a batch as alone
        fields = {"criterion": criterion, "reasoning": (str, Field(description="Two or three sentences.")),
                  "label": label, "quote": quote}
    else:
        fields = {"criterion": criterion, "label": label, "quote": quote,
                  "reasoning": (str, Field(description="One sentence."))}
    item = create_model(f"BatchItem_{key}", **fields)
    return create_model(f"Batched_{key}", answers=(list[item], Field(description="One entry per criterion.")))


def batch_system_prompt(q: Question) -> str:
    return (f"{system_prompt(q)}\n\nThe input lists several criteria in <criteria>, each with an id (C1, C2, ...). "
            "Answer the question separately for each criterion, using the same candidate text for all of them. "
            "Return one entry per criterion, in the order given.")


def split_shared(contexts: list[dict]) -> tuple[dict, list[dict]]:
    """Context entries equal in every item (the candidate text) vs. the per-item rest (the criterion)."""
    keys = [k for k in contexts[0] if not k.startswith("_")]
    shared = {k: contexts[0][k] for k in keys if all(c.get(k) == contexts[0][k] for c in contexts)}
    return shared, [{k: v for k, v in c.items() if k not in shared and not k.startswith("_")} for c in contexts]


def method_of(model: str) -> str:
    """Stored judgment method: the provider that answered."""
    return "groq" if is_groq(model) else "gemini" if is_gemini(model) else "claude"


class ClaudeJudge:
    """Claude as judge, or Gemini / Groq when the configured model is "gemini/…" / "groq/…"."""

    def __init__(self, llm: StructuredLLM | None = None):
        self.llm = llm or RoutedLLM(max_tokens=JUDGE_MAX_TOKENS, effort=config.JUDGE_EFFORT)
        # Complexity reasons first on the reasoning model; its effort is set separately.
        self.complexity_llm = llm or RoutedLLM(max_tokens=JUDGE_MAX_TOKENS, effort=config.COMPLEXITY_EFFORT)

    @staticmethod
    def model_for(key: str) -> str:
        """The model or route ("route:<task>") for single questions of this family."""
        return config.COMPLEXITY_MODEL if key == "complexity" else config.JUDGE_FALLBACK_MODEL

    @staticmethod
    def batch_model_for(key: str) -> str:
        return config.JUDGE_BATCH_MODEL or config.JUDGE_FALLBACK_MODEL

    def models_for(self, key: str) -> list[str]:
        """Every model that may answer this family (saved judgments from any of them are reused): the route's
        members, and the batch route's too (complexity is never batched)."""
        from providers.routing import models_of
        out = []
        batched = key != "complexity"
        for m in (self.model_for(key), self.batch_model_for(key)) if batched else (self.model_for(key),):
            for x in models_of(m):
                if x not in out:
                    out.append(x)
        return out

    def _judge(self, key: str, context: dict) -> Judgment:
        q = question(key)
        user = render_context(context)
        model = self.model_for(key)
        llm = self.complexity_llm if key == "complexity" else self.llm
        clear_last()
        budget = config.TASK_MAX_TOKENS["complexity" if key == "complexity" else "judge_single"]
        try:
            with answer_budget(budget):
                out = llm.parse(model=model, system=system_prompt(q), user=user, output_type=output_model(key))
        except LLMError as exc:
            raise JudgeError(str(exc)) from exc
        quote = out.quote.strip() or None
        if quote and (out.label == q.none_option or not QuoteIndex(user).contains(quote)):
            quote = None  # never store a quote that is not in the input
        used = last_model(model)                       # the model that answered (failover may change it)
        return Judgment(label=out.label, quote=quote, reasoning=out.reasoning.strip() or None,
                        method=method_of(used), model_version=used)

    def batch(self, key: str, contexts: list[dict]) -> list[Judgment | None]:
        """One call for several criteria that share the same candidate text (specs/09 §2.2).

        Returns one Judgment per context, in order; None where the model gave no answer for it.
        A quote not found in that criterion's own input is dropped (the caller retries it alone).
        """
        q = question(key)
        shared, own = split_shared(contexts)
        ids = [f"C{i}" for i in range(1, len(contexts) + 1)]
        criteria = "\n".join(f"{cid}: {_as_text(next(iter(o.values())) if len(o) == 1 else o)}"
                             for cid, o in zip(ids, own))
        user = f"{render_context(shared)}\n\n{wrap_tag('criteria', criteria)}"
        model = self.model_for(key) if key == "complexity" else self.batch_model_for(key)
        clear_last()
        llm = self.complexity_llm if key == "complexity" else self.llm
        try:
            with answer_budget(config.grouped_budget(len(contexts)) * (2 if q.reason_first else 1)):
                out = llm.parse(model=model, system=batch_system_prompt(q), user=user,
                                output_type=batch_output_model(key))
        except LLMError as exc:
            raise JudgeError(str(exc)) from exc
        model = last_model(model)
        by_id = {a.criterion.strip(): a for a in out.answers}
        results: list[Judgment | None] = []
        for cid, ctx in zip(ids, contexts):
            a = by_id.get(cid)
            if a is None:
                results.append(None)
                continue
            quote = a.quote.strip() or None
            if quote and (a.label == q.none_option or not QuoteIndex(render_context(ctx)).contains(quote)):
                quote = None
            results.append(Judgment(label=a.label, quote=quote, reasoning=a.reasoning.strip() or None,
                                    method=method_of(model), model_version=model))
        return results

    def choice(self, question_key: str, options: list[str], context: dict) -> Judgment:
        if list(options) != list(question(question_key).options):
            raise ValueError(f"options for {question_key} must match the catalogue")
        return self._judge(question_key, context)

    def yes_no(self, question_key: str, context: dict) -> Judgment:
        return self._judge(question_key, context)
