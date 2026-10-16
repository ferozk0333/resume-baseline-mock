"""Process-wide concurrency limits per provider (see config). Gemini and Groq calls are limited per model by the
router (core/routing.py, specs/18 §2)."""
import threading

from core import config

jev_slots = threading.BoundedSemaphore(config.JEV_MAX_CONCURRENCY)
claude_slots = threading.BoundedSemaphore(config.CLAUDE_MAX_CONCURRENCY)
