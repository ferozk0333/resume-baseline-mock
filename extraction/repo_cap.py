"""REPO_CAP (specs/18 item 11): read and judge at most MAX_REPOS repositories, ranked by relevance to the role (how
many of the role's skill and tool names they mention), then latest push, then stars. The others stay listed on the
profile but are not read or judged. Forks with no own commits are excluded as before."""
from __future__ import annotations

from core import config
from core.text import contains_phrase


def _terms(baseline) -> list[str]:
    return [t for c in baseline.criteria if c.kind in ("skill", "tool") for t in (c.name, *c.aliases)]


def relevance(text: str, baseline) -> int:
    return sum(1 for t in _terms(baseline) if contains_phrase(text, t))


def keep(items: list[tuple[str, str, str | None, int]], baseline) -> set[str]:
    """items: (key, text, last push ISO or None, stars). The keys of the MAX_REPOS repositories to read and judge."""
    ranked = sorted(items, key=lambda x: (-relevance(x[1], baseline), -(int((x[2] or "0").replace("-", "")[:8] or 0)),
                                          -x[3], x[0]))
    return {k for k, *_ in ranked[:config.MAX_REPOS]}
