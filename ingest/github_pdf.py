"""A GitHub profile saved as PDF, read in code (no model, no API call). For mock candidates without a live account.

Two formats:
- The profile page printed from a browser. The username comes from the github.com address in the page footer. The
  repository cards sit under "Pinned" or "Popular repositories", two side by side, so text is grouped by position:
  a card starts only at a repo name followed by its visibility badge (Public, Private, Public template, Public
  archive); then an optional "Forked from owner/repo" line, a description (may wrap) and the footer row: the primary
  language, the star count and the fork count (a count, not fork status), printed as separate runs on one row. The profile README,
  the contribution graph and the activity sections are never read.
- The fixed block format of Repo.as_text() ("Repository: owner/name", "URL: …", …).

Nothing is invented: README and last push stay empty unless a block states the push, own commits are 0 (unknown),
and the README text of a block is dropped (it would go to the extraction model). Profile repos are skipped as on the
API path. Anything that does not read cleanly is skipped with a warning.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import pymupdf

from core import config
from ingest.github import Repo, is_profile_repo, parse_username

_FOOTER_URL = re.compile(r"github\.com/([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/?(?=[\s?#]|$)")
_START = re.compile(r"^(pinned|popular repositories)\b", re.I)
_END = re.compile(r"contributions? in (the last year|\d{4})|^contribution activity|^activity overview|"
                  r"^(learn how we count contributions|show more activity)", re.I)
# The visibility badge after a repo name: the only thing that starts a card (longest first).
_VISIBILITY = ("public template", "public archive", "public", "private")
_IGNORE = {"customize your pins"}
_REPO_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_FULL_NAME = re.compile(r"^([A-Za-z0-9-]{1,39})/([A-Za-z0-9._-]{1,100})$")
_FORKED = re.compile(r"^forked from\s+(\S+/\S+)", re.I)
_COUNT = r"\d[\d,]*(?:\.\d+)?[kK]?"
# A card's footer row: "<language> | <stars> | <forks>", printed as separate runs on one row; counts are optional.
_FOOTER = re.compile(rf"^(?P<lang>[A-Za-z][A-Za-z0-9+#.' -]{{0,30}}?)?(?:\s*(?P<a>{_COUNT}))?(?:\s+(?P<b>{_COUNT}))?$")
# A footer without counts (no stars or forks) is only taken as a language when it is one of these.
LANGUAGES = {
    "assembly", "c", "c#", "c++", "clojure", "css", "cuda", "dart", "dockerfile", "elixir", "elm", "erlang", "f#",
    "fortran", "go", "groovy", "haskell", "hcl", "html", "java", "javascript", "julia", "jupyter notebook", "kotlin",
    "lua", "makefile", "matlab", "nix", "objective-c", "ocaml", "perl", "php", "powershell", "python", "r", "ruby",
    "rust", "scala", "scss", "shell", "solidity", "sql", "svelte", "swift", "tex", "typescript", "vue", "zig",
}
_BLOCK_FIELDS = ("Repository", "URL", "Description", "Primary language", "Languages", "Fork", "Commits by user",
                 "Last push")


@dataclass
class Piece:
    """One run of text on one line of one page (a line split where two cards sit side by side)."""
    text: str
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool


def _pieces(doc) -> list[Piece]:
    out: list[Piece] = []
    for pno, page in enumerate(doc):
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                cur: list[dict] = []
                for s in ln["spans"]:
                    if not s["text"].strip():
                        continue
                    if cur and s["bbox"][0] - cur[-1]["bbox"][2] > 3 * max(s["size"], 1):
                        out.append(_piece(cur, pno))
                        cur = []
                    cur.append(s)
                if cur:
                    out.append(_piece(cur, pno))
    return out


def _piece(spans: list[dict], pno: int) -> Piece:
    text = " ".join(" ".join(s["text"].split()) for s in spans)
    chars = sum(len(s["text"].strip()) for s in spans)
    bold = sum(len(s["text"].strip()) for s in spans if s["flags"] & 16 or "bold" in s["font"].lower())
    return Piece(text, pno, min(s["bbox"][0] for s in spans), min(s["bbox"][1] for s in spans),
                 max(s["bbox"][2] for s in spans), max(s["bbox"][3] for s in spans),
                 max(s["size"] for s in spans), bold >= 0.6 * chars)


def _count(text: str | None) -> int:
    if not text:
        return 0
    t = text.replace(",", "")
    return int(float(t[:-1]) * 1000) if t[-1] in "kK" else int(float(t))


def _username(pieces: list[Piece], heights: dict[int, float]) -> str | None:
    """The profile address printed in the page footer (the bottom tenth of a page); any page address as a fallback."""
    def found(ps):
        return Counter(m.group(1) for p in ps for m in _FOOTER_URL.finditer(p.text) if parse_username(m.group(1)))
    footer = found(p for p in pieces if p.y0 >= 0.9 * heights[p.page])
    hits = footer or found(pieces)
    return hits.most_common(1)[0][0] if hits else None


def _repo(user: str, name: str, owner: str, description: str | None, language: str | None, is_fork: bool,
          stars: int = 0, own_commits: int = 0, pushed_at: str | None = None,
          languages: list[str] | None = None) -> Repo:
    langs = languages if languages is not None else ([language] if language else [])
    return Repo(name=name, full_name=f"{owner}/{name}", description=description or None, language=language or None,
                html_url=f"https://github.com/{owner}/{name}", is_fork=is_fork, created_at=None, pushed_at=pushed_at,
                languages={x: 0 for x in langs}, own_commits=own_commits, readme=None, stars=stars)


def _keep(repos: list[Repo], user: str, warnings: list[str]) -> list[Repo]:
    """Same choice as the API path: the user's own repos, not the profile repos, at most GITHUB_MAX_REPOS. A name seen
    twice is never merged: the first is kept and the other is reported."""
    out, seen = [], set()
    for r in repos:
        owner = r.full_name.split("/")[0]
        if owner.casefold() != user.casefold():
            warnings.append(f"GitHub PDF: skipped {r.full_name} (owned by {owner}, not {user}).")
        elif is_profile_repo(r.name, user):
            continue
        elif r.full_name.casefold() in seen:
            warnings.append(f"GitHub PDF: {r.full_name} appears more than once; kept the first.")
        else:
            seen.add(r.full_name.casefold())
            out.append(r)
    if len(out) > config.GITHUB_MAX_REPOS:
        warnings.append(f"GitHub PDF: kept the first {config.GITHUB_MAX_REPOS} of {len(out)} repositories.")
    return out[: config.GITHUB_MAX_REPOS]


# --- Repo.as_text() blocks ------------------------------------------------------------------------------------------

def _from_blocks(text: str, warnings: list[str]) -> tuple[str | None, list[Repo]]:
    chunks = re.split(r"(?m)^(?=Repository:)", text)
    found: list[tuple[str, str, dict]] = []
    for chunk in chunks:
        if not chunk.startswith("Repository:"):
            continue
        fields: dict[str, str] = {}
        for line in chunk.splitlines():
            if line.strip() == "README:":
                break                                   # dropped: it would go to the extraction model
            key, sep, value = line.partition(":")
            if sep and key.strip() in _BLOCK_FIELDS and key.strip() not in fields:
                fields[key.strip()] = value.strip()
        m = _FULL_NAME.match(fields.get("Repository", ""))
        if not m:
            warnings.append(f"GitHub PDF: could not read the block for '{fields.get('Repository', '')[:60]}'.")
            continue
        found.append((m.group(1), m.group(2), fields))
    if not found:
        return None, []
    user = Counter(owner for owner, _, _ in found).most_common(1)[0][0]
    repos = []
    for owner, name, f in found:
        commits = f.get("Commits by user", "")
        push = f.get("Last push", "")
        repos.append(_repo(user, name, owner, f.get("Description"), f.get("Primary language"),
                           f.get("Fork", "").lower() == "yes",
                           own_commits=int(commits) if commits.isdigit() else 0,
                           pushed_at=push if re.match(r"^\d{4}-\d{2}", push) else None,
                           languages=[x.strip() for x in f.get("Languages", "").split(",") if x.strip()]))
    return user, repos


# --- The printed profile page ---------------------------------------------------------------------------------------

@dataclass
class Row:
    """Pieces at one height in one column, left to right ("Python", "143", "11" is one row)."""
    pieces: list[Piece]

    @property
    def text(self) -> str:
        return " ".join(p.text for p in self.pieces)

    @property
    def top(self) -> Piece:
        return self.pieces[0]


def _rows(column: list[Piece]) -> list[Row]:
    rows: list[Row] = []
    for p in sorted(column, key=lambda p: (p.page, p.y0, p.x0)):
        last = rows[-1].top if rows else None
        if last is not None and last.page == p.page and abs(p.y0 - last.y0) < 0.5 * max(p.size, last.size):
            rows[-1].pieces.append(p)
        else:
            rows.append(Row([p]))
    for r in rows:
        r.pieces.sort(key=lambda p: p.x0)
    return rows


def _card_name(row: Row) -> str | None:
    """The name when the row is "<name> <visibility badge>", the only start of a card; else None."""
    text = row.text.strip()
    for badge in _VISIBILITY:
        name = text[: -len(badge)].strip()
        if text.casefold().endswith(" " + badge) and name and " " not in name:
            return name
    return None


def _footer(text: str) -> tuple[str | None, int, int] | None:
    """(language, stars, forks) when the row is a card's footer, else None."""
    m = _FOOTER.match(text.strip())
    if not m or not (m.group("lang") or m.group("a")):
        return None
    lang = (m.group("lang") or "").strip() or None
    if lang and len(lang.split()) > 3:
        return None
    if not m.group("a") and (lang or "").casefold() not in LANGUAGES:
        return None                                     # a short description line, not a language
    return lang, _count(m.group("a")), _count(m.group("b"))


@dataclass
class Card:
    start: Row
    name: str
    rows: list[Row]
    footer: tuple[str | None, int, int] | None = None


def _cards(rows: list[Row], warnings: list[str]) -> list[Card]:
    """A card starts only at a "<name> <badge>" row. Its footer row ends it and is never a card of its own. Text
    outside any card is reported, never read."""
    cards: list[Card] = []
    open_card: Card | None = None
    outside = False
    for row in rows:
        if (name := _card_name(row)) is not None:
            open_card = Card(row, name, [])
            cards.append(open_card)
            outside = False
        elif open_card is not None and (ft := _footer(row.text)) is not None:
            open_card.footer, open_card = ft, None
        elif open_card is not None:
            open_card.rows.append(row)
        elif not outside:
            warnings.append(f"GitHub PDF: skipped text outside any repository card: '{row.text[:60]}'.")
            outside = True
    return cards


def _card(card: Card, user: str, warnings: list[str]) -> Repo | None:
    name, owner = card.name, user
    if m := _FULL_NAME.match(name):                     # a pinned repo of another owner shows "owner/name"
        owner, name = m.group(1), m.group(2)
    if not _REPO_NAME.match(name):
        warnings.append(f"GitHub PDF: could not read a repository card starting '{card.start.text[:60]}'.")
        return None
    rest = [r.text for r in card.rows]
    is_fork = any(_FORKED.match(t) for t in rest)
    description = " ".join(t for t in rest if not _FORKED.match(t)) or None
    lang, stars, _forks = card.footer or (None, 0, 0)  # the fork count is not stored (Repo has no field for it)
    return _repo(user, name, owner, description, lang, is_fork, stars=stars)


def _from_page(pieces: list[Piece], heights: dict[int, float], warnings: list[str]) -> tuple[str | None, list[Repo]]:
    user = _username(pieces, heights)
    if user is None:
        warnings.append("GitHub PDF: no github.com profile address found (it is printed in the page footer).")
        return None, []
    pieces = [p for p in pieces if not (p.y0 >= 0.9 * heights[p.page] and _FOOTER_URL.search(p.text))]
    order = sorted(pieces, key=lambda p: (p.page, p.y0, p.x0))
    starts = [i for i, p in enumerate(order) if _START.match(p.text)]
    if not starts:
        warnings.append("GitHub PDF: no \"Pinned\" or \"Popular repositories\" section found.")
        return user, []
    head = order[starts[0]]
    after = order[starts[0] + 1:]
    end = next((p for p in after if _END.search(p.text) or _START.match(p.text)), None)

    def before_end(p: Piece) -> bool:                   # the whole row of the end heading is out ("… settings ▾")
        return end is None or (p.page, p.y0) < (end.page, end.y0 - 0.5 * end.size)
    area = [p for p in after if before_end(p) and p.x1 > head.x0 - 5      # left of the cards: the profile sidebar
            and p.text.casefold() not in _IGNORE]
    if not area:
        warnings.append("GitHub PDF: the repository section is empty.")
        return user, []
    mid = (head.x0 + max(p.x1 for p in area)) / 2
    found: list[tuple[Piece, Repo]] = []
    for col in ([p for p in area if p.x0 < mid], [p for p in area if p.x0 >= mid]):
        for card in _cards(_rows(col), warnings):
            if (repo := _card(card, user, warnings)) is not None:
                found.append((card.start.top, repo))
    found.sort(key=lambda x: (x[0].page, round(x[0].y0 / 4), x[0].x0))    # row by row, left card first
    return user, [r for _, r in found]


def parse_github_pdf(data: bytes) -> tuple[str | None, list[Repo], list[str]]:
    """(username, repos, warnings). Repos follow the API path from here (evidence type github_repo)."""
    warnings: list[str] = []
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:                                   # noqa: BLE001 - any unreadable file is one warning
        return None, [], ["GitHub PDF: the file could not be read as a PDF."]
    text = "\n".join(page.get_text() for page in doc)
    if re.search(r"(?m)^Repository:\s*\S+/\S+", text):
        user, repos = _from_blocks(text, warnings)
    else:
        user, repos = _from_page(_pieces(doc), {i: page.rect.height for i, page in enumerate(doc)}, warnings)
    if user is None:
        return None, [], warnings
    return user, _keep(repos, user, warnings), warnings
