"""Daily spending caps on the paid providers (deploy item 6): Jev and Claude.

Each real request is counted per Pacific day in provider_usage (provider "jev" / "claude", model "*daily-cap*"), the
same table the free-tier limiter uses, so a restart does not reset the count. Past the cap the request is refused
before it is sent: Jev questions then go to the free judge route (the usual fallback), and a Claude call fails over
like any refused call. Caps live in config (JEV_DAILY_MAX_REQUESTS, CLAUDE_DAILY_MAX_REQUESTS; 0 = no paid calls).
"""
from __future__ import annotations

import threading

from core import config

_LOCK = threading.Lock()
MODEL = "*daily-cap*"


class SpendCapReached(Exception):
    """The provider's daily request cap is used up; nothing was sent."""


def cap(provider: str) -> int:
    return {"jev": config.JEV_DAILY_MAX_REQUESTS, "claude": config.CLAUDE_DAILY_MAX_REQUESTS}[provider]


def used_today(provider: str, db_path: str | None = None) -> int:
    from core import db
    from providers.routing import pacific_day
    conn = db.connect(db_path or config.DB_PATH)
    try:
        row = conn.execute("SELECT requests FROM provider_usage WHERE provider = ? AND model = ? AND day = ?",
                           (provider, MODEL, pacific_day())).fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def charge(provider: str, db_path: str | None = None) -> None:
    """Count one real request, or raise SpendCapReached when today's cap is used up (nothing is counted then)."""
    from core import db
    from providers.routing import pacific_day
    limit = cap(provider)
    with _LOCK:
        conn = db.connect(db_path or config.DB_PATH)
        try:
            day = pacific_day()
            row = conn.execute("SELECT requests FROM provider_usage WHERE provider = ? AND model = ? AND day = ?",
                               (provider, MODEL, day)).fetchone()
            used = row[0] if row else 0
            if used >= limit:
                raise SpendCapReached(f"{provider}: daily cap of {limit} requests reached")
            with conn:
                conn.execute("INSERT INTO provider_usage (provider, model, day, requests, tokens) VALUES (?, ?, ?, 1, 0) "
                             "ON CONFLICT(provider, model, day) DO UPDATE SET requests = requests + 1",
                             (provider, MODEL, day))
        finally:
            conn.close()
