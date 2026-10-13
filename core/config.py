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
JUDGE = _env("JUDGE", "jev")  # jev | claude
EXTRACT_MODEL = _env("EXTRACT_MODEL", "claude-haiku-4-5-20251001")
JUDGE_FALLBACK_MODEL = _env("JUDGE_FALLBACK_MODEL", "claude-haiku-4-5-20251001")
REASONING_MODEL = _env("REASONING_MODEL", "claude-sonnet-5-5")
# Complexity judgments; can be switched to Haiku if labels flip between fresh runs (07 §5).
COMPLEXITY_MODEL = _env("COMPLEXITY_MODEL", REASONING_MODEL)
JEV_MODEL = _env("JEV_MODEL", "jev-1.13.0")   # pinned; saved judgments are matched on this version
JEV_URL = "https://api.typesafe.ai/v1/systemone"
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
EXTRACT_CONCURRENCY = 4
# Concurrency. All 19 subcategories run at once; these caps set how many model calls are in flight.
# JUDGE_CONCURRENCY (specs/09 §2.1): judgments in flight across all four dimension subgraphs, Jev and
# Claude together. Beneath it, per-provider pools keep fast Jev calls from queueing slow Claude calls.
JUDGE_CONCURRENCY = int(_env("JUDGE_CONCURRENCY", _env("MAX_PARALLEL_JUDGE_CALLS", "8")))
JEV_MAX_CONCURRENCY = int(_env("JEV_MAX_CONCURRENCY", "32"))             # Jev allows 80 requests/s
CLAUDE_MAX_CONCURRENCY = int(_env("CLAUDE_MAX_CONCURRENCY", "12"))       # lower it if Anthropic returns 429s
# Estimated USD per million tokens, for the run cost line only (check https://claude.com/pricing).
# Cache reads cost 0.1× input, 5-minute cache writes 1.25× input.
MODEL_PRICES = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-sonnet": {"input": 3.0, "output": 15.0},     # assumption for Sonnet 5.5; confirm
    "claude-opus": {"input": 5.0, "output": 25.0},
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
