"""Environment settings, scoring constants and default weights (specs/04)."""
from __future__ import annotations

import copy
import os
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


def _role_model(env: str, claude_default: str, role: str) -> str:
    """The model for one role. With LLM_PROVIDER=gemini, Claude IDs in .env are replaced by the Gemini model
    for the role; an explicit "gemini/…" or "groq/…" setting always wins."""
    explicit = os.getenv(env, "").split("#", 1)[0].strip()
    if LLM_PROVIDER == "gemini":
        return explicit if explicit.startswith(("gemini/", "groq/")) else f"gemini/{GEMINI_MODELS[role]}"
    return explicit or claude_default


EXTRACT_MODEL = _role_model("EXTRACT_MODEL", "claude-haiku-4-5-20251001", "extract")
JUDGE_FALLBACK_MODEL = _role_model("JUDGE_FALLBACK_MODEL", "claude-haiku-4-5-20251001", "judge")
REASONING_MODEL = _role_model("REASONING_MODEL", "claude-sonnet-5-5", "plan")
# Complexity judgments; can be switched to Haiku if labels flip between fresh runs (07 §5).
COMPLEXITY_MODEL = _role_model("COMPLEXITY_MODEL", REASONING_MODEL, "complexity")
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
MAX_PORTFOLIO_CHARS = 50_000
GITHUB_MAX_REPOS = 30           # most recently pushed, non-archived
GITHUB_README_CHARS = 4_000
GITHUB_README_EXTRACTION = True  # one LLM call per counted repo README (05 §A)
GITHUB_CACHE_HOURS = 24          # GitHub API responses reused for this long (ETag revalidation after)
GITHUB_CONCURRENCY = 8           # repos whose details are fetched at once
EXTRACT_MAX_TOKENS = 12_000     # a dense one-page resume needs ~7.3k (phase 9); runaway output is cut earlier
PLAN_MAX_TOKENS = 16_000
EXTRACT_CONCURRENCY = 16        # resume, LinkedIn, portfolio and READMEs at once (specs/09 §2.5); Claude cap applies
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
