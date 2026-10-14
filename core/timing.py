"""Per-run timing (specs/09 §1): every ingest source, extraction call, graph node, judge call,
model call and the plan, with latency and token counts.

A Recorder is made active for a pipeline run through a context variable, so any code on the
run's threads can add spans without being passed the recorder. Thread pools must run their
work through `bind()` to carry the context into worker threads (LangGraph does this itself
for its nodes). With no active recorder, `span()` costs nothing and records nothing.
"""
from __future__ import annotations

import contextvars
import threading
import time
from collections import defaultdict
from contextlib import contextmanager

_recorder: contextvars.ContextVar["Recorder | None"] = contextvars.ContextVar("timing_recorder", default=None)
_span: contextvars.ContextVar[dict | None] = contextvars.ContextVar("timing_span", default=None)

TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")

# Span kinds, in report order.
STEP = "step"          # top-level graph node (wall clock)
NODE = "node"          # node inside a dimension/subcategory subgraph (wall clock)
INGEST = "ingest"      # one source read (document parse, GitHub API, portfolio fetch)
EXTRACT = "extract"    # one extraction call
JUDGE = "judge"        # one judgment asked of a judge (Jev and/or Claude, retries included)
MODEL = "model"        # one HTTP model call (claude | jev)
PLAN = "plan"          # improvement plan generation
EARLY = "early"        # job-independent judgments started during review (specs/09 §2.6)


class Recorder:
    def __init__(self):
        self.t0 = time.perf_counter()
        self.events: list[dict] = []
        self.counters: dict[str, int] = defaultdict(int)   # e.g. claude_429, jev_retries
        self._lock = threading.Lock()

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def add(self, kind: str, name: str, start: float, seconds: float, **attrs) -> None:
        with self._lock:
            self.events.append({"kind": kind, "name": name, "start": round(start, 4),
                                "seconds": round(seconds, 4), **attrs})

    def now(self) -> float:
        return time.perf_counter() - self.t0

    # --- summaries --------------------------------------------------------------------------
    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["kind"] == kind]

    def window(self, first: str, last: str) -> float | None:
        """Wall seconds from the start of step `first` to the end of step `last` (latest occurrences)."""
        steps = {e["name"]: e for e in self.of(STEP)}
        if first not in steps or last not in steps:
            return None
        return steps[last]["start"] + steps[last]["seconds"] - steps[first]["start"]

    def _score_to_plan(self) -> float | None:
        """Wall seconds from 'Confirm & score' (prepare_match starts) to the plan being saved."""
        steps = {e["name"]: e for e in self.of(STEP)}
        plans = self.of(PLAN)
        if "prepare_match" not in steps:
            return None
        end = max([e["start"] + e["seconds"] for e in plans] +
                  [steps[s]["start"] + steps[s]["seconds"] for s in ("aggregate", "gap_analysis") if s in steps])
        return round(end - steps["prepare_match"]["start"], 3)

    def summary(self) -> dict:
        """Plain-data summary stored in runs.timings_json."""
        steps = [{"step": e["name"], "seconds": e["seconds"], "start": e["start"]} for e in self.of(STEP)]
        groups: dict[tuple, dict] = defaultdict(lambda: {"calls": 0, "seconds": 0.0, "max": 0.0,
                                                          **{k: 0 for k in TOKEN_KEYS}})
        for e in self.events:
            if e["kind"] not in (INGEST, EXTRACT, JUDGE, MODEL, PLAN, EARLY):
                continue
            key = (e["kind"], e["name"], e.get("model") or "", e.get("method") or "")
            g = groups[key]
            g["calls"] += 1
            g["seconds"] += e["seconds"]
            g["max"] = max(g["max"], e["seconds"])
            for k in TOKEN_KEYS:
                g[k] += e.get(k) or 0
        calls = [{"kind": k[0], "name": k[1], "model": k[2], "method": k[3], **{x: round(v, 3) if isinstance(v, float)
                  else v for x, v in g.items()}} for k, g in sorted(groups.items())]
        model_calls = defaultdict(int)
        tokens: dict[str, dict] = defaultdict(lambda: {"calls": 0, **{k: 0 for k in TOKEN_KEYS}})
        for e in self.of(MODEL):
            model_calls[e["name"]] += 1
            t = tokens[e.get("model") or e["name"]]
            t["calls"] += 1
            for k in TOKEN_KEYS:
                t[k] += e.get(k) or 0
        return {
            "steps": steps,
            "calls": calls,
            "model_calls": dict(model_calls),
            "rate_limits": dict(sorted(self.counters.items())),
            "tokens_by_model": {m: {**t, "usd": cost(m, t)} for m, t in sorted(tokens.items())},
            "estimated_usd": round(sum(cost(m, t) or 0 for m, t in tokens.items()), 4),
            "judge_calls": len(self.of(JUDGE)),
            "onboarding_seconds": self.window("ingest", "validate"),
            "score_seconds": self.window("prepare_match", "aggregate"),
            "early_seconds": round(sum(e["seconds"] for e in self.of(EARLY)), 3) or None,
            "plan_seconds": round(sum(e["seconds"] for e in self.of(PLAN)), 3) or None,
            "scoring_and_plan_seconds": self._score_to_plan(),
            "subcategory_judge_seconds": {e["path"][-1]: e["seconds"] for e in self.of(NODE)
                                          if e["name"] == "judge" and e.get("path")},
        }


def cost(model: str, t: dict) -> float | None:
    """Estimated Claude cost from config.MODEL_PRICES (None for Jev or an unknown model)."""
    from core import config
    price = next((p for prefix, p in config.MODEL_PRICES.items() if model.startswith(prefix)), None)
    if price is None:
        return None
    usd = (t["input_tokens"] * price["input"] + t["output_tokens"] * price["output"]
           + t["cache_read_tokens"] * price["input"] * 0.1 + t["cache_write_tokens"] * price["input"] * 1.25)
    return round(usd / 1e6, 4)


def active() -> Recorder | None:
    return _recorder.get()


def count(name: str, n: int = 1) -> None:
    """Add to a run counter (no-op outside a recorded run)."""
    rec = _recorder.get()
    if rec is not None:
        rec.count(name, n)


@contextmanager
def recording(recorder: Recorder):
    token = _recorder.set(recorder)
    try:
        yield recorder
    finally:
        _recorder.reset(token)


@contextmanager
def span(kind: str, name: str, **attrs):
    """Time a block. Yields a dict the block may add attributes to (model, method, tokens…).

    Token counts added inside a span are also added to the enclosing span (a judge call
    shows the tokens of the model calls it made).
    """
    rec = _recorder.get()
    if rec is None:
        yield attrs
        return
    parent = _span.get()
    token = _span.set(attrs)
    start = rec.now()
    try:
        yield attrs
    except BaseException as exc:
        attrs.setdefault("error", exc.__class__.__name__)
        raise
    finally:
        _span.reset(token)
        rec.add(kind, name, start, rec.now() - start, **attrs)
        if parent is not None:
            for k in TOKEN_KEYS:
                if attrs.get(k):
                    parent[k] = (parent.get(k) or 0) + attrs[k]


def note(**attrs) -> None:
    """Add attributes (e.g. token counts) to the innermost open span."""
    current = _span.get()
    if current is not None:
        for k, v in attrs.items():
            current[k] = (current.get(k) or 0) + v if k in TOKEN_KEYS else v


def bind(fn):
    """Wrap `fn` so a worker thread runs it inside the caller's context (recorder and open span)."""
    ctx = contextvars.copy_context()

    def run(*args, **kwargs):
        return ctx.copy().run(fn, *args, **kwargs)
    return run


# --- Report -----------------------------------------------------------------------------------

def table(summary: dict) -> str:
    """Text table printed at the end of a run: steps by wall clock, then model calls grouped."""
    lines = []
    # Share of end-to-end wall time, review wait excluded (dimension subgraphs overlap, so they add to >100%).
    total = sum(summary.get(k) or 0 for k in ("onboarding_seconds", "scoring_and_plan_seconds")) or 1.0
    lines.append(f"{'step (wall clock)':38s} {'seconds':>8s} {'share':>6s}")
    for s in summary["steps"]:
        if s["step"] == "review":
            continue
        lines.append(f"{s['step']:38s} {s['seconds']:8.2f} {100 * s['seconds'] / total:5.0f}%")
    for label, key in (("upload → review", "onboarding_seconds"), ("early judging (during review)", "early_seconds"),
                       ("confirm → score", "score_seconds"), ("plan (after the score)", "plan_seconds"),
                       ("confirm → plan ready", "scoring_and_plan_seconds")):
        if summary.get(key) is not None:
            lines.append(f"{'= ' + label:38s} {summary[key]:8.2f}")
    lines.append("")
    lines.append(f"{'calls (summed latency; they overlap)':38s} {'calls':>5s} {'sum s':>8s} {'max s':>6s} "
                 f"{'in tok':>8s} {'cached':>7s}")
    for c in summary["calls"]:
        if c["kind"] == MODEL:
            continue
        name = f"{c['kind']}:{c['name']}" + (f" [{c['method'] or c['model']}]" if c["method"] or c["model"] else "")
        lines.append(f"{name[:38]:38s} {c['calls']:5d} {c['seconds']:8.2f} {c['max']:6.2f} "
                     f"{c['input_tokens']:8d} {c['cache_read_tokens']:7d}")
    mc = summary.get("model_calls", {})
    lines.append("")
    for m, t in summary.get("tokens_by_model", {}).items():
        usd = f"  ≈ ${t['usd']:.3f}" if t.get("usd") is not None else ""
        lines.append(f"{m}: {t['calls']} calls, in {t['input_tokens']} (cache read {t['cache_read_tokens']}, "
                     f"write {t['cache_write_tokens']}), out {t['output_tokens']}{usd}")
    lines.append(f"estimated Claude cost this run: ${summary.get('estimated_usd', 0):.3f}")
    rl = summary.get("rate_limits") or {}
    lines.append("rate limits / overload: " + (", ".join(f"{k} {v}" for k, v in rl.items()) or "none"))
    lines.append("model HTTP calls: " + ", ".join(f"{k} {v}" for k, v in sorted(mc.items())) +
                 f"  ·  judgments asked: {summary.get('judge_calls', 0)}")
    return "\n".join(lines)
