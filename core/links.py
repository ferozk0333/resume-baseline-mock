"""Links, exactly as written (audit round, item 4): one place that finds URLs in text, never re-encodes them.

- A URL keeps every character it had: ( ) are not percent-encoded and an existing %XX is not encoded again.
- Parentheses inside a URL are kept when balanced ("…/PIIS9999-0000(26)04217-3/abstract"); a closing bracket
  that closes the text around the link ("(see https://x.org/a)") is not part of it.
- Trailing sentence punctuation (a final "." "," ";" ":" "!" "?" or a closing quote) is not part of the URL.
- Markdown links "[text](url)" are read with balanced parentheses in the URL.

The frontend has the same rules in frontend/lib/links.ts; tests/test_links.py checks both on one list of cases.
"""
from __future__ import annotations

import re

# pymupdf4llm writes "(" ")" and a newline inside a link target as "%0x28" "%0x29" "%0xa" (hex() of the
# character), which is not a valid escape and breaks the link. Turned back into the characters they stand for.
_PDF_ESCAPE = re.compile(r"%0x(28|29|a|0a)", re.I)

_START = re.compile(r"(?i)\b(?:https?://|www\.)")
_STOP = set(" \t\r\n<>\"`{}|\\^")
_TRAILING = ".,;:!?'\"”’»"


def decode_pdf_escapes(url: str) -> str:
    return _PDF_ESCAPE.sub(lambda m: {"28": "(", "29": ")"}.get(m.group(1).lower(), ""), url)


def _end_of_url(text: str, start: int) -> int:
    """Index just past the URL starting at `start`: balanced ( ) and [ ] are kept, trailing punctuation is not."""
    i, depth = start, {"(": 0, "[": 0}
    close = {")": "(", "]": "["}
    while i < len(text) and text[i] not in _STOP:
        c = text[i]
        if c in depth:
            depth[c] += 1
        elif c in close:
            if depth[close[c]] == 0:
                break                                   # closes the text around the link
            depth[close[c]] -= 1
        i += 1
    while i > start and text[i - 1] in _TRAILING:
        i -= 1
    return i


def find_urls(text: str | None) -> list[tuple[int, int, str]]:
    """(start, end, url) of every plain-text URL in `text`, in order."""
    out, pos = [], 0
    text = text or ""
    while m := _START.search(text, pos):
        end = _end_of_url(text, m.start())
        if end > m.end():
            out.append((m.start(), end, text[m.start():end]))
        pos = max(end, m.end())
    return out


def first_url(text: str | None) -> str | None:
    found = find_urls(text)
    return found[0][2] if found else None


def markdown_links(text: str | None) -> list[tuple[int, int, str, str]]:
    """(start, end, label, url) of every "[label](url)" in `text`, the URL read with balanced parentheses."""
    out = []
    text = text or ""
    for m in re.finditer(r"\[([^\]]*)\]\(", text):
        i, depth = m.end(), 1
        while i < len(text) and depth and text[i] not in " \t\r\n":
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            i += 1
        if depth == 0:
            out.append((m.start(), i, m.group(1), text[m.end():i - 1]))
    return out


def href_for(url: str) -> str:
    """The href for a stored URL: the URL itself, character for character; only a URL written without a scheme
    ("github.com/x", "www.x.org") gets "https://" in front (frontend/lib/links.ts hrefFor)."""
    u = url.strip()
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*:", u) or u.startswith(("/", "#")):
        return u
    return f"https://{u}"
