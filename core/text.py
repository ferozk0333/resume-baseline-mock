"""Name normalization and phrase matching shared by validation and scoring."""
from __future__ import annotations

import re

_SEP = re.compile(r"[^a-z0-9+#]+")


def normalize_name(text: str | None) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed ("Scikit-Learn" → "scikit learn").

    Keeps + and # so "C++" and "C#" stay distinct.
    """
    if not text:
        return ""
    return " ".join(_SEP.sub(" ", text.casefold()).split())


def contains_phrase(haystack: str | None, needle: str | None) -> bool:
    """True if `needle` appears in `haystack` as whole words, after normalization."""
    n, h = normalize_name(needle), normalize_name(haystack)
    return bool(n) and f" {n} " in f" {h} "


def wrap_tag(tag: str, text: str) -> str:
    """Put untrusted text inside <tag>…</tag>, neutralizing any copy of that tag inside the text."""
    escaped = re.sub(rf"<\s*(/?)\s*{re.escape(tag)}\s*>", rf"&lt;\1{tag}&gt;", text, flags=re.I)
    return f"<{tag}>\n{escaped}\n</{tag}>"
