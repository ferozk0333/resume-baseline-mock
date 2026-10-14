"""Process-wide concurrency limits per model provider (see config)."""
import threading

from core import config

jev_slots = threading.BoundedSemaphore(config.JEV_MAX_CONCURRENCY)
claude_slots = threading.BoundedSemaphore(config.CLAUDE_MAX_CONCURRENCY)
groq_slots = threading.BoundedSemaphore(config.GROQ_CONCURRENCY)
