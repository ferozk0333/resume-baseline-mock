"""Environment settings, scoring constants and default weights (specs/04)."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str) -> str:
    # Strip inline "# comment" tails copied from .env.example.
    value = os.getenv(name, default).split("#", 1)[0].strip()
    return value or default


# --- Paths -----------------------------------------------------------------
DB_PATH = Path(_env("RIGHTSTAFF_DB", str(ROOT / "rightstaff.db")))
SEED_PATH = ROOT / "seed" / "baselines.json"
PROMPTS_DIR = ROOT / "prompts"
UPLOAD_DIR = Path(_env("RIGHTSTAFF_UPLOADS", str(ROOT / "uploads")))

# --- Models / judges -------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
JEV_API_KEY = os.getenv("JEV_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
# Groq (OpenAI-compatible, hosted open models). Use a model as "groq/<model id>", e.g. EXTRACT_MODEL=groq/openai/gpt-oss-120b.
GROQ_API_KEY = _env("GROQ_API_KEY", "")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_CONCURRENCY = int(_env("GROQ_CONCURRENCY", "2"))
GROQ_TPM = int(_env("GROQ_TPM", "8000"))           # tokens per minute of your Groq tier (free: 8000); caps each request
GROQ_REASONING_EFFORT = _env("GROQ_REASONING_EFFORT", "low")   # gpt-oss reasoning tokens count toward the limit
JUDGE = _env("JUDGE", "jev")  # jev | claude
# Provider for the roles Claude had (extraction, fallback judging, complexity, plan): claude | gemini.
# Jev stays the main judge. Groq stays available per model ("groq/<id>").
LLM_PROVIDER = _env("LLM_PROVIDER", "claude")
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Free-tier models (pricing page, 2026-10-13). Set LLM_PROVIDER=gemini to use them.
# Model per role. Fallback judging, unmatched-skill batches and complexity run often: Flash-Lite has the daily
# limit for them. Extraction and the plan are few calls: Flash.
GEMINI_MODELS = {
    "extract": _env("GEMINI_EXTRACT_MODEL", "gemini-3.8-flash"),
    "judge": _env("GEMINI_JUDGE_MODEL", "gemini-3.5-flash-lite"),
    "complexity": _env("GEMINI_COMPLEXITY_MODEL", "gemini-3.5-flash-lite"),
    "plan": _env("GEMINI_PLAN_MODEL", "gemini-3.8-flash"),
    "email": _env("GEMINI_EMAIL_MODEL", "gemini-3.5-flash-lite"),
    "reply": _env("GEMINI_REPLY_MODEL", "gemini-3.5-flash-lite"),
    "feedback": _env("GEMINI_FEEDBACK_MODEL", "gemini-3.5-flash-lite"),
}


def _limits(spec: str) -> dict[str, tuple[int, int, int]]:
    """"model=RPM/TPM/RPD,model=…" → {model: (rpm, tpm, rpd)}."""
    out = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        model, _, nums = part.partition("=")
        rpm, tpm, rpd = (int(x) for x in nums.split("/"))
        out[model.strip()] = (rpm, tpm, rpd)
    return out


# Free-tier limits per model, from the project's AI Studio rate-limit page (2026-10-13). Override with
# GEMINI_LIMITS="model=RPM/TPM/RPD,…". RPD resets at midnight Pacific. A model not listed uses GEMINI_RPM/TPM/RPD.
GEMINI_LIMITS = {**_limits("gemini-3.8-flash=5/250000/20,gemini-3.5-flash-lite=15/250000/500"),
                 **_limits(_env("GEMINI_LIMITS", ""))}
GEMINI_RPM = int(_env("GEMINI_RPM", "5"))
GEMINI_TPM = int(_env("GEMINI_TPM", "250000"))
GEMINI_RPD = int(_env("GEMINI_RPD", "20"))


def gemini_limits(model: str) -> tuple[int, int, int]:
    return GEMINI_LIMITS.get(model, (GEMINI_RPM, GEMINI_TPM, GEMINI_RPD))
GEMINI_CONCURRENCY = int(_env("GEMINI_CONCURRENCY", "4"))
GEMINI_THINKING_LEVEL = _env("GEMINI_THINKING_LEVEL", "low")

# --- Routing (specs/18 §2) ---------------------------------------------------------------------------------------------
# Real model calls (Gemini, Groq, Claude, Jev) only with LIVE=1 (`make live` sets it). Dev servers, scripts and tests
# without it fail at once with a clear message instead of calling a provider.
LIVE = _env("LIVE", "0") == "1"
# Ordered provider/model lists per task. In phase 1 the first model of each list is the one used today; the others
# take over only when it is exhausted, busy or overloaded. Override one list with ROUTE_<TASK>="model,model,…"
# (a "claude-…" id at the top works with no code change).
_ROUTE_DEFAULTS = {
    # gemini-2.5-flash is not offered to new users any more (HTTP 404 in the first live run); gemma-4-26b-it is not
    # found on the v1beta API (every call in the README gate, 2026-10-17).
    "extract_document": "gemini/gemini-3.8-flash,gemini/gemini-3.7-flash,gemini/gemini-3.6-flash,"
                        "gemini/gemini-3-flash-preview",
    "extract_web": "gemini/gemini-3.5-flash-lite,gemini/gemini-3.1-flash-lite,gemini/gemma-4-31b-it,"
                   "groq/openai/gpt-oss-120b",
    "judge_single": "gemini/gemini-3.5-flash-lite,gemini/gemini-3.1-flash-lite,groq/openai/gpt-oss-120b,"
                    "groq/qwen/qwen3.8-27b,groq/openai/gpt-oss-20b,gemini/gemma-4-31b-it",
    "judge_grouped": "gemini/gemini-3.5-flash-lite,gemini/gemini-3.1-flash-lite,gemini/gemma-4-31b-it",
    "plan": "gemini/gemini-3.8-flash,gemini/gemini-3.7-flash,gemini/gemini-3.6-flash",
}
ROUTES: dict[str, list[str]] = {
    task: [m.strip() for m in _env(f"ROUTE_{task.upper()}", default).split(",") if m.strip()]
    for task, default in _ROUTE_DEFAULTS.items()}


@dataclass(frozen=True)
class ModelLimits:
    rpm: int = 0          # 0 = no limit known
    tpm: int = 0
    rpd: int = 0
    tpd: int = 0


def _model_limits(text: str) -> dict[str, ModelLimits]:
    """"provider/model=RPM/TPM/RPD[/TPD],…" → {model: ModelLimits}."""
    out = {}
    for part in filter(None, (x.strip() for x in text.split(","))):
        model, _, nums = part.partition("=")
        n = [int(x) for x in nums.split("/")]
        out[model.strip()] = ModelLimits(*n)
    return out


# Free-tier limits (specs/18 §1, 2026-10-16; Groq: console.groq.com/docs/rate-limits). Override with MODEL_LIMITS.
_FLASH = "5/250000/20"
MODEL_LIMITS = {
    **_model_limits(",".join(f"gemini/{m}={_FLASH}" for m in ("gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash",
                                                              "gemini-3-flash-preview", "gemini-2.5-flash"))),
    **_model_limits("gemini/gemini-3.5-flash-lite=15/250000/500,gemini/gemini-3.1-flash-lite=15/250000/500,"
                    "gemini/gemma-4-31b-it=30/16000/14400,gemini/gemma-4-26b-it=30/16000/14400,"
                    "groq/openai/gpt-oss-120b=30/8000/1000/200000,groq/openai/gpt-oss-20b=30/8000/1000/200000,"
                    "groq/qwen/qwen3.8-27b=30/8000/1000/200000,"
                    "groq/meta-llama/llama-prompt-guard-2-86m=30/15000/14400/500000,"
                    "groq/meta-llama/llama-prompt-guard-2-22m=30/15000/14400/500000"),
    **{f"gemini/{m}": ModelLimits(*lim) for m, lim in _limits(_env("GEMINI_LIMITS", "")).items()},
    **_model_limits(_env("MODEL_LIMITS", "")),
}


def model_limits(model: str) -> ModelLimits:
    """Known limits of a model (a Gemini model not listed uses GEMINI_RPM/TPM/RPD; Claude: none)."""
    if model in MODEL_LIMITS:
        return MODEL_LIMITS[model]
    if model.startswith("gemini/"):
        return ModelLimits(GEMINI_RPM, GEMINI_TPM, GEMINI_RPD)
    if model.startswith("groq/"):
        return ModelLimits(30, GROQ_TPM, 1000, 200000)
    return ModelLimits()


@dataclass(frozen=True)
class ModelCaps:
    """What a model's API accepts (Phase 3 review 2c). A request sends only these; prompts never change."""
    thinking: bool = True             # Gemini thinkingConfig
    temperature: bool = True          # temperature 0 (CLAUDE.md rule 7) where accepted
    system_instruction: bool = True   # instructions in a separate system part (CLAUDE.md rule 9)
    json_schema: bool = True          # structured output by schema


# Prefix → capabilities; the longest matching prefix wins. Gemma on the Gemini API rejects a thinking level ("Thinking
# level is not supported for this model", live run 1). Newer Claude models reject a non-default temperature.
_CAPS: dict[str, ModelCaps] = {
    "gemini/gemma-": ModelCaps(thinking=False),
    "claude-sonnet-5": ModelCaps(temperature=False), "claude-opus-5": ModelCaps(temperature=False),
    "claude-haiku-5": ModelCaps(temperature=False), "claude-fable": ModelCaps(temperature=False),
    "claude-opus-4-7": ModelCaps(temperature=False),
}


def _caps_env(text: str) -> dict[str, ModelCaps]:
    """MODEL_CAPS="provider/model=thinking:0,temperature:1,…;…" (overrides for one model)."""
    out = {}
    for part in filter(None, (x.strip() for x in text.split(";"))):
        model, _, flags = part.partition("=")
        kv = dict(f.split(":") for f in flags.split(",") if ":" in f)
        out[model.strip()] = ModelCaps(**{k.strip(): v.strip() == "1" for k, v in kv.items()})
    return out


_CAPS.update(_caps_env(_env("MODEL_CAPS", "")))


def model_caps(model: str) -> ModelCaps:
    hits = [k for k in _CAPS if model.startswith(k)]
    return _CAPS[max(hits, key=len)] if hits else ModelCaps()


_CONCURRENCY = {k.strip(): int(v) for k, _, v in (x.partition("=") for x in _env("MODEL_CONCURRENCY", "").split(",") if "=" in x)}


def model_concurrency(model: str) -> int:
    """Calls in flight per model: up to its requests per minute (MODEL_CONCURRENCY="model=n,…" overrides)."""
    if model in _CONCURRENCY:
        return _CONCURRENCY[model]
    rpm = model_limits(model).rpm
    return rpm if rpm else CLAUDE_MAX_CONCURRENCY


# Answer budget (max output tokens) per kind of call (specs/18 Phase 1 review): small for single questions, larger for
# complexity (it reasons first), per item for grouped questions. Thinking/reasoning tokens count toward it. Smaller
# budgets reserve less of Groq's 8K and Gemma's 16K tokens per minute. Override with TASK_MAX_TOKENS="key=n,…".
_TASK_TOKENS = {"judge_single": 1024, "complexity": 2048, "judge_grouped_base": 512, "judge_grouped_item": 192,
                "judge_grouped_max": 4096, "extract_web": 12000}   # 4096 cut portfolio pages off (live run 1)
TASK_MAX_TOKENS = {**_TASK_TOKENS, **{k.strip(): int(v) for k, _, v in (x.partition("=") for x in
                                                                      _env("TASK_MAX_TOKENS", "").split(",") if "=" in x)}}


def grouped_budget(items: int) -> int:
    t = TASK_MAX_TOKENS
    return min(t["judge_grouped_base"] + t["judge_grouped_item"] * items, t["judge_grouped_max"])


GEMINI_TIMEOUT_S = float(_env("GEMINI_TIMEOUT_S", "45"))
GROQ_TIMEOUT_S = float(_env("GROQ_TIMEOUT_S", "20"))
# Reading a whole résumé or LinkedIn export takes longer than one question: 45 s timed out on Gemini 3.8 and 3.7
# Flash in the first live run (earlier real extractions took 60-90 s). Per task, in seconds; TASK_TIMEOUT_S overrides.
TASK_TIMEOUT_S = {"extract_document": 180.0, "extract_web": 90.0, "plan": 120.0,
                  **{k.strip(): float(v) for k, _, v in (x.partition("=") for x in _env("TASK_TIMEOUT_S", "").split(",")
                                                         if "=" in x)}}
UNUSABLE_REST_S = float(_env("UNUSABLE_REST_S", "3600"))  # a model that rejects our request (400/404) rests this long
ROUTE_MAX_ATTEMPTS = int(_env("ROUTE_MAX_ATTEMPTS", "4"))      # model calls per request, across the list
ROUTE_MAX_WAIT_S = float(_env("ROUTE_MAX_WAIT_S", "5"))        # one wait when every model is busy
ROUTE_BUSY_BUDGET_S = float(_env("ROUTE_BUSY_BUDGET_S", "60"))  # longer than this is "exhausted", not "busy"
ROUTE_MAX_TOTAL_WAIT_S = float(_env("ROUTE_MAX_TOTAL_WAIT_S", "90"))   # all models busy this long: give up the call
OVERLOAD_COOLDOWN_S = float(_env("OVERLOAD_COOLDOWN_S", "20"))  # a model that answered 5xx/timeout rests this long
# Hedging (off by default): a single-item call with no answer after N s also goes to the next model; first answer
# wins. Each hedge costs one more request of quota.
HEDGE_AFTER_S = float(_env("HEDGE_AFTER_S", "0"))
HEDGE_TASKS = tuple(t.strip() for t in _env("HEDGE_TASKS", "judge_single").split(","))


# The route each role uses with LLM_PROVIDER=gemini (specs/18 §2 item 1).
ROLE_ROUTES = {"extract": "extract_document", "extract_web": "extract_web", "judge": "judge_single",
               "judge_batch": "judge_grouped", "complexity": "judge_single", "plan": "plan", "email": "judge_single",
               "reply": "judge_single", "feedback": "judge_single", "job_extract": "extract_document"}


def _role_model(env: str, claude_default: str, role: str) -> str:
    """The model (or route) for one role. With LLM_PROVIDER=gemini, Claude IDs in .env are replaced by the role's
    route ("route:<task>"); an explicit "gemini/…", "groq/…" or "route:…" setting always wins."""
    explicit = os.getenv(env, "").split("#", 1)[0].strip()
    if LLM_PROVIDER == "gemini":
        return explicit if explicit.startswith(("gemini/", "groq/", "route:")) else f"route:{ROLE_ROUTES[role]}"
    return explicit or claude_default


EXTRACT_MODEL = _role_model("EXTRACT_MODEL", "claude-haiku-4-5-20251001", "extract")
JUDGE_FALLBACK_MODEL = _role_model("JUDGE_FALLBACK_MODEL", "claude-haiku-4-5-20251001", "judge")
REASONING_MODEL = _role_model("REASONING_MODEL", "claude-sonnet-5-5", "plan")
# READMEs and portfolio pages (extract_web) and grouped judge questions (judge_grouped): same model as their role
# unless a route applies.
EXTRACT_WEB_MODEL = _role_model("EXTRACT_WEB_MODEL", EXTRACT_MODEL, "extract_web")
JUDGE_BATCH_MODEL = _role_model("JUDGE_BATCH_MODEL", "", "judge_batch")     # empty: same as JUDGE_FALLBACK_MODEL
# Complexity judgments; can be switched to Haiku if labels flip between fresh runs (07 §5).
COMPLEXITY_MODEL = _role_model("COMPLEXITY_MODEL", REASONING_MODEL, "complexity")
# Recruiter emails (specs/13 §2–§4): drafts only; a recruiter sends. Template draft when the model is unavailable.
EMAIL_MODEL = _role_model("EMAIL_MODEL", "claude-haiku-4-5-20251001", "email")
# Reading a job description into the create-job form (specs/14 §4): one call per JD; manual form if unavailable.
JOB_EXTRACT_MODEL = _env("JOB_EXTRACT_MODEL", "route:extract_document")
# Hiring workflow (specs/16): reading candidate replies and summarizing interview feedback. Code rules / a code
# summary when the model is unavailable; the recruiter always decides.
REPLY_MODEL = _role_model("REPLY_MODEL", "claude-haiku-4-5-20251001", "reply")
FEEDBACK_MODEL = _role_model("FEEDBACK_MODEL", "claude-haiku-4-5-20251001", "feedback")
JEV_MODEL = _env("JEV_MODEL", "jev-1.13.0")   # pinned; saved judgments are matched on this version
JEV_URL = "https://api.typesafe.ai/v1/systemone"
# Effort per purpose (low | medium | high; empty = the model's default: high on Sonnet 5.5, medium on
# Haiku 5.5). Sent only to models that support effort; Haiku 4.5 does not. Thinking tokens bill as output.
EXTRACT_EFFORT = _env("EXTRACT_EFFORT", "")
JUDGE_EFFORT = _env("JUDGE_EFFORT", "")
COMPLEXITY_EFFORT = _env("COMPLEXITY_EFFORT", "")
PLAN_EFFORT = _env("PLAN_EFFORT", "")
# Fallback to Claude when Jev's `confidence` is below this (yes/no: |2p - 1|). Starting value; set in phase 8.
JEV_CONFIDENCE_THRESHOLD = float(_env("JEV_CONFIDENCE_THRESHOLD", "0.6"))

# --- Ingest limits ------------------------------------------------------------
MIN_DOCUMENT_CHARS = 100        # fewer non-space characters → "unreadable" (no OCR)
MIN_PORTFOLIO_CHARS = 200       # fewer → probably JavaScript-rendered
UPLOAD_MAX_MB = float(_env("UPLOAD_MAX_MB", "10"))   # resume, LinkedIn PDF, JD and market uploads (specs/19 item 2)
# No space before . , ; : ! ? ) or after ( in document text (specs/19 item 6). Part of the normalizer key, so only
# documents read from now on change; saved text (and its judgments) stays as it is.
PUNCT_SPACING = _env("PUNCT_SPACING", "1") == "1"
# Duplicate claims, certifications and projects merge on the matching key or the reviewed alias file (specs/19
# Phase C, a bug fix under "one claim per name"). Off only to reproduce the old merge for the before/after report.
DEDUPE_NAMES = _env("DEDUPE_NAMES", "1") == "1"
MAX_PORTFOLIO_CHARS = 50_000
GITHUB_MAX_REPOS = 30           # most recently pushed, non-archived
GITHUB_README_CHARS = 4_000
GITHUB_README_EXTRACTION = True  # one LLM call per counted repo README (05 §A)
GITHUB_CACHE_HOURS = 24          # GitHub API responses reused for this long (ETag revalidation after)
GITHUB_CONCURRENCY = 8           # repos whose details are fetched at once
EXTRACT_MAX_TOKENS = 12_000     # a dense one-page resume needs ~7.3k (phase 9); runaway output is cut earlier
PLAN_MAX_TOKENS = 16_000
EXTRACT_CONCURRENCY = 16        # resume, LinkedIn, portfolio and READMEs at once (specs/09 §2.5); Claude cap applies

# Code-first extraction (specs/18 §4). A parsed document is used only when its confidence gate passes; otherwise the
# whole document goes to the LLM. PARSE_LINKEDIN is on (Phase 3 review); README and resume parsing are parked (off).
PARSE_README = _env("PARSE_README", "0") == "1"          # README blocks kept by rule or Jev; no LLM call
PARSE_LINKEDIN = _env("PARSE_LINKEDIN", "1") == "1"      # LinkedIn "Save to PDF" layout parser
PARSE_RESUME = _env("PARSE_RESUME", "off")               # off | shadow (runs after the LLM, logged, never used)
PARSE_SHADOW_CALLS = _env("PARSE_SHADOW_CALLS", "0") == "1"   # shadow parser may ask Jev / the small claims call
REMOVE_HIDDEN_TEXT = _env("REMOVE_HIDDEN_TEXT", "1") == "1"   # white-on-white, tiny or off-page text (spec item 20)
PARSER_MAX_UNCLEAR = int(_env("PARSER_MAX_UNCLEAR", "3"))     # more unclear lines after Jev → LLM fallback
README_MAX_CHARS = int(_env("README_MAX_CHARS", "1500"))       # kept README blocks, verbatim, as the description
README_TABLE_MAX_ROWS = int(_env("README_TABLE_MAX_ROWS", "10"))   # data rows in a kept results table (one bullet)
JEV_PARSE_BATCH = int(_env("JEV_PARSE_BATCH", "20"))           # questions per Jev request when parsing
# All-bullets judging per source document (Phase 3 review 2b): one ask per document (resume, LinkedIn, each repo,
# portfolio) instead of one over everything, so an unchanged document keeps its saved answer and a new source can only
# add credit. Final label = the best across sources.
PARTITIONED_BATCHES = _env("PARTITIONED_BATCHES", "0") == "1"
# specs/18 Phase 2 (all default off; the comparison script decides each).
GROUPED_ITEM_JUDGING = _env("GROUPED_ITEM_JUDGING", "0") == "1"   # complexity / work samples: one call per family
REPO_CAP = _env("REPO_CAP", "0") == "1"                            # judge and read at most MAX_REPOS repositories
MAX_REPOS = int(_env("MAX_REPOS", "8"))
GEMMA_JUDGE = _env("GEMMA_JUDGE", "0") == "1"                      # Gemma in routine judging, not only overflow
GROQ_JUDGE = _env("GROQ_JUDGE", "0") == "1"                        # Groq in routine single-item judging
# Prompt-injection screen (specs/18 Phase 4; default off). Llama Prompt Guard 2 on Groq: a 512-token context window
# (https://console.groq.com/docs/model/llama-prompt-guard-2-86m), so text is screened in chunks. Background only:
# never blocks reading, never changes a score, never shown to recruiters.
PROMPT_GUARD = _env("PROMPT_GUARD", "1") == "1"   # on (spec 18 wrap-up); needs LIVE=1 to call Groq
PROMPT_GUARD_MODELS = [m.strip() for m in _env("PROMPT_GUARD_MODELS", "groq/meta-llama/llama-prompt-guard-2-86m,"
                                                "groq/meta-llama/llama-prompt-guard-2-22m").split(",") if m.strip()]
PROMPT_GUARD_THRESHOLD = float(_env("PROMPT_GUARD_THRESHOLD", "0.5"))
PROMPT_GUARD_CHUNK_CHARS = 1200     # ≈ 300–400 tokens: inside the 512-token window with room for the template
PROMPT_GUARD_OVERLAP = 150          # an instruction split across two chunks is still seen whole in one
HIDDEN_MIN_FONT_PT = 4.0         # smaller text is hidden
HIDDEN_MIN_CONTRAST = 1.6        # text/background contrast ratio below this is hidden (white on white = 1.0)
# Concurrency. All 19 subcategories run at once; these caps set how many model calls are in flight.
# JUDGE_CONCURRENCY (specs/09 §2.1): judgments in flight across all four dimension subgraphs, Jev and
# Claude together. Beneath it, per-provider pools keep fast Jev calls from queueing slow Claude calls.
# Default 48, not the spec's 8: measured on a real profile, 8 made confirm → score 51.5 s vs 33.4 s (phase 9).
JUDGE_CONCURRENCY = int(_env("JUDGE_CONCURRENCY", _env("MAX_PARALLEL_JUDGE_CALLS", "48")))
JEV_MAX_CONCURRENCY = int(_env("JEV_MAX_CONCURRENCY", "32"))             # Jev allows 80 requests/s
CLAUDE_MAX_CONCURRENCY = int(_env("CLAUDE_MAX_CONCURRENCY", "12"))       # lower it if Anthropic returns 429s
# Estimated USD per million tokens, for the run cost line only (check https://claude.com/pricing).
# Checked 2026-10-13. Cache reads cost 0.1× input, 5-minute cache writes 1.25× input.
MODEL_PRICES = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},  # prompts up to 100k tokens
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0},
}
PRINT_TIMINGS = _env("PRINT_TIMINGS", "1") == "1"   # timing table on stdout at the end of each run (specs/09 §1)

# --- Benchmarks (specs/12) -----------------------------------------------------
# Percentiles need at least MIN_POOL candidates in the pool; any peer statistic needs at least MIN_GROUP
# people. Lower them in .env only for a local demo: the UI then shows a "demo pool" badge.
MIN_POOL = int(_env("MIN_POOL", "20"))
MIN_GROUP = int(_env("MIN_GROUP", "10"))

# Candidate Home (specs/17 §4, §6). JobMatch is a dummy preview for now: a deterministic number, always labelled
# "Preview", never stored as a score and never shown to recruiters. Groups: Strong >= STRONG, Close >= CLOSE.
JOBMATCH_PREVIEW = _env("JOBMATCH_PREVIEW", "1") == "1"
JOBMATCH_STRONG = int(_env("JOBMATCH_STRONG", "75"))
JOBMATCH_CLOSE = int(_env("JOBMATCH_CLOSE", "55"))
# Recruiter activity signals (specs/17 §4.1): "Reviewing applications" when a stage moved within REVIEWING_DAYS;
# "Low recent activity" after LOW_ACTIVITY_DAYS without any recruiter action on the job.
REVIEWING_DAYS = int(_env("REVIEWING_DAYS", "14"))
LOW_ACTIVITY_DAYS = int(_env("LOW_ACTIVITY_DAYS", "30"))
# Closure principle (specs/17 §7): an open application without a stage change for CLOSURE_DAYS becomes a recruiter
# Tasks item. Nothing is ever sent by itself.
CLOSURE_DAYS = int(_env("CLOSURE_DAYS", "14"))
PEER_MIN_RATE = int(_env("PEER_MIN_RATE", "50"))   # a peer signal shows when at least this % of the group has it

# --- Scoring constants (specs/04) -----------------------------------------
LOGIC_VERSION = "v0.1"
ENTRY_LEVEL_MAX_YEARS = 2          # baseline.required_years <= 2 → entry level
DECAY = 0.9                        # Recency decay per year
PROJECT_TARGET = 3                 # P in Depth Project_score
UNVERIFIED_CERT_TOOL_SCORE = 1.0   # [OPEN] tool evidence from a certification
SCOPE_OF_IMPACT_RELEVANCE_FILTER = True  # [OPEN] only bullets with relevance > 0 count toward reach
ROLE_MATCH_THRESHOLD = 0.6               # cross-source role dedupe: share of title+employer words that must match
NO_ROLES_RELEVANCE_SCOPE_NA = False      # [OPEN] no roles at all → Relevance and Scope are na instead of missing

RELEVANCE_VALUES = {"relevant": 1.0, "adjacent": 0.5, "unrelated": 0.0}

SUBCATEGORY_STATUSES = ("scored", "missing", "na", "error")


def today() -> date:
    """Scoring 'today'; set RIGHTSTAFF_TODAY=YYYY-MM-DD to pin it in tests."""
    override = _env("RIGHTSTAFF_TODAY", "")
    return date.fromisoformat(override) if override else date.today()


# --- Weights (specs/04 §6) -------------------------------------------------
# Integrity's 5% is redistributed across Trust in proportion: w / 95 × 100.
_DEFAULT_WEIGHTS: dict = {
    "dimensions": {
        "experience": 30.0,
        "expertise": 30.0,
        "authority": 20.0,
        "trust": 20.0,
    },
    "subcategories": {
        "experience": {
            "relevance": 35.0,
            "depth": 25.0,
            "scope": 20.0,
            "progression": 10.0,
            "recency": 10.0,
        },
        "expertise": {
            "role_skills": 30.0,
            "applied_knowledge": 30.0,
            "tools": 15.0,
            "complexity": 15.0,
            "work_samples": 10.0,
        },
        "authority": {
            "outcomes": 30.0,
            "impact": 30.0,
            "ownership": 20.0,
            "scope_of_impact": 10.0,
            "recognition": 10.0,
        },
        "trust": {
            "proof_density": 30 / 95 * 100,
            "validation_strength": 30 / 95 * 100,
            "consistency": 25 / 95 * 100,
            "evidence_quality": 10 / 95 * 100,
        },
    },
}

DIMENSIONS: tuple[str, ...] = tuple(_DEFAULT_WEIGHTS["dimensions"])
SUBCATEGORIES: dict[str, tuple[str, ...]] = {
    d: tuple(subs) for d, subs in _DEFAULT_WEIGHTS["subcategories"].items()
}
SUBCATEGORY_DIMENSION: dict[str, str] = {
    s: d for d, subs in SUBCATEGORIES.items() for s in subs
}

SUBCATEGORY_LABELS: dict[str, str] = {
    "relevance": "Relevance",
    "depth": "Depth",
    "scope": "Scope",
    "progression": "Progression",
    "recency": "Recency",
    "role_skills": "Role-specific skills",
    "applied_knowledge": "Applied knowledge",
    "tools": "Technical / tools proficiency",
    "complexity": "Complexity",
    "work_samples": "Work samples / artefacts",
    "outcomes": "Outcomes",
    "impact": "Impact",
    "ownership": "Ownership",
    "scope_of_impact": "Scope of impact",
    "recognition": "Recognition",
    "proof_density": "Proof density",
    "validation_strength": "Validation strength",
    "consistency": "Consistency",
    "evidence_quality": "Evidence quality",
}


def default_weights() -> dict:
    """A fresh, mutable copy of the default weight table."""
    return copy.deepcopy(_DEFAULT_WEIGHTS)


# --- Recruiter email (specs/13 §4) -------------------------------------------------------------
EMAIL_PROVIDER = _env("EMAIL_PROVIDER", "outbox")        # outbox: stored and shown, nothing leaves the machine | smtp
SMTP_HOST = _env("SMTP_HOST", "")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _env("SMTP_USER", "")
SMTP_PASSWORD = _env("SMTP_PASSWORD", "")
EMAIL_FROM = _env("EMAIL_FROM", "")
# Demo redirect: while set, every email goes to this address only (subject and body name the intended recipient).
# Set DEMO_EMAIL_TO= (empty) to turn the redirect off.
DEMO_EMAIL_TO = os.getenv("DEMO_EMAIL_TO", "you@example.com").split("#", 1)[0].strip()
# Prototype deployment (docs/deploy.md): the API refuses to start without the demo redirect.
REQUIRE_DEMO_EMAIL_TO = _env("REQUIRE_DEMO_EMAIL_TO", "0") == "1"

# --- Spending and abuse limits (deploy item 6) --------------------------------------------------------------------
# Daily request caps on the paid providers (core/spend.py); 0 = no paid calls. Free tiers keep their own limits.
JEV_DAILY_MAX_REQUESTS = int(_env("JEV_DAILY_MAX_REQUESTS", "300"))
CLAUDE_DAILY_MAX_REQUESTS = int(_env("CLAUDE_DAILY_MAX_REQUESTS", "20"))
# Uploads per client address per hour (resume/LinkedIn, job descriptions, market postings); 0 = no limit.
UPLOAD_RATE_PER_HOUR = int(_env("UPLOAD_RATE_PER_HOUR", "20"))
# Behind the reverse proxy, the client address is the first X-Forwarded-For entry (set TRUST_PROXY=1 only there).
TRUST_PROXY = _env("TRUST_PROXY", "0") == "1"
REJECTION_MAX_WORDS = 150
SCHEDULING_MAX_WORDS = 120

# --- Hiring workflow (specs/16) ------------------------------------------------------------------------
NUDGE_DAYS = float(_env("NUDGE_DAYS", "3"))             # no reply after this many days → draft a nudge (not sent)
REPLY_INBOX = _env("REPLY_INBOX", "none")                # none (paste replies) | imap (poll the demo inbox)
IMAP_HOST = _env("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(_env("IMAP_PORT", "993"))
IMAP_USER = _env("IMAP_USER", "") or SMTP_USER           # same Gmail app password as SMTP by default
IMAP_PASSWORD = _env("IMAP_PASSWORD", "") or SMTP_PASSWORD
IMAP_POLL_MINUTES = float(_env("IMAP_POLL_MINUTES", "3"))
WORKFLOW_TICK_SECONDS = int(_env("WORKFLOW_TICK_SECONDS", "60"))   # nudges due, interviews passed, inbox; 0 = off
MESSAGE_MAX_WORDS = 120                                  # nudge, answer and offer-note drafts

# --- Market baseline (specs/15) ---------------------------------------------------------------------------------
MARKET_SOURCES = ROOT / "config" / "market_sources.yaml"
MARKET_SYNONYMS = ROOT / "config" / "market_synonyms.json"     # reviewed synonym map (versioned, not in data/)
MARKET_FIXTURES = ROOT / "tests" / "fixtures" / "market"        # test mode: hand-labelled postings, no model
MARKET_EXTRACT_MODEL = _env("MARKET_EXTRACT_MODEL", "gemini/gemini-3.8-flash")   # once per new posting, cached
MARKET_SUMMARY_MODEL = _env("MARKET_SUMMARY_MODEL", "gemini/gemini-3.8-flash")   # scope/complexity/description per version
MARKET_MERGE_MODEL = _env("MARKET_MERGE_MODEL", "gemini/gemini-3.5-flash-lite")  # proposes merges for leftover names
MARKET_MERGE_BATCH = 40
WINDOW_DAYS = int(_env("WINDOW_DAYS", "90"))           # a posting counts while last_seen is within this window
RECOMPUTE_DAYS = int(_env("RECOMPUTE_DAYS", "30"))
FETCH_DAYS = 7                                          # weekly fetch; only updates last_seen for known postings
REQ_SHARE = float(_env("REQ_SHARE", "0.5"))             # required if listed as required by ≥ this share (proposal)
PREF_SHARE = float(_env("PREF_SHARE", "0.2"))           # preferred if listed at all by ≥ this share (proposal)
MARGIN = float(_env("MARGIN", "0.05"))                  # hysteresis: a category changes only past threshold ± margin
TIER_WEIGHTS = {"large": 1.0, "mid": 1.0, "startup": 1.0}   # equal by default
DEDUPE_SIMILARITY = 0.92                                # same company + this text similarity = one posting
MARKET_USER_AGENT = "RightStaff-market-baseline/0.1 (local research mock; weekly)"
