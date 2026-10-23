"""Which names are the same thing, for merging duplicates (specs/19 Phase C: "one claim per name").

Claims, certifications and projects merge only on an exact match of the key below, or when the reviewed alias file
(config/claim_aliases.json) puts both names in one group. config/market_synonyms.json maps names to market
*concepts* (MySQL → SQL, AWS → Cloud platform): those are related, not the same, so only its pairs that are the same
name spelled differently or its initials (python3 → Python, HF → Hugging Face, RAG → Retrieval-augmented generation)
are used. Nothing is merged on similarity alone: close names are reported as suggestions for the alias file.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from core.text import normalize_name

ROOT = Path(__file__).resolve().parent.parent
ALIASES = ROOT / "config" / "claim_aliases.json"
MARKET = ROOT / "config" / "market_synonyms.json"

# "(MLA-C01)", "(SAA-C03)", "(AZ-900)", "(DP-100)": letters, an optional dash, then a code with a digit.
_EXAM = re.compile(r"\(\s*[A-Za-z]{1,6}-?[A-Za-z0-9]*\d[A-Za-z0-9-]*\s*\)")
_BRACKET = re.compile(r"\(\s*([A-Za-z][A-Za-z0-9&.]{1,9})\s*\)")
_SMALL = {"of", "and", "for", "the", "a", "an", "in", "on", "to", "&"}
_KEEP = {"kubernetes", "pandas", "redis", "aws", "js", "analytics", "statistics", "ios", "macos", "sas", "glass",
         "express", "devops", "mlops", "llmops", "dataops", "gitops", "ops", "news", "series", "species", "less"}


def exam_code(name: str) -> str | None:
    m = _EXAM.search(name or "")
    return m.group(0).strip("() ").upper() if m else None


def _initials(words: list[str]) -> str:
    return "".join(w[0] for w in words if w and w.casefold() not in _SMALL)


def _strip_acronym(name: str) -> str:
    """"Model Context Protocol (MCP)" → "Model Context Protocol": only when the bracket is the name's initials."""
    def repl(m: re.Match) -> str:
        acr = re.sub(r"[^A-Za-z0-9]", "", m.group(1)).casefold()
        words = re.findall(r"[A-Za-z0-9]+", name[:m.start()])
        for a in {acr, acr[:-1] if acr.endswith("s") else acr}:     # "(LLMs)"
            for n in range(1, len(words) + 1):                     # the last n words, small words ignored
                if _initials(words[-n:]).casefold() == a:
                    return " "
        return m.group(0)
    return _BRACKET.sub(repl, name)


def _singular(word: str) -> str:
    w = word
    if w in _KEEP or len(w) <= 3 or not w.endswith("s") or w.endswith(("ss", "us", "is")):
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("xes", "ches", "shes", "sses")):
        return w[:-2]
    return w[:-1]


def _split_slug(name: str) -> str:
    """"lora-ticket-router" / "lora_ticket_router" / "LoraTicketRouter" → words."""
    name = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name) if " " not in name.strip() else name
    return re.sub(r"[-_]+", " ", name)


def key(name: str | None) -> str:
    """The matching key: lowercase, punctuation stripped, spaces collapsed, exam codes and an initials bracket
    removed, the last word singular. For matching only: the name shown keeps all of it."""
    if not name:
        return ""
    n = _strip_acronym(_EXAM.sub(" ", name))
    words = normalize_name(n).split()
    if words:
        words[-1] = _singular(words[-1])
    return " ".join(words)


def project_key(name: str | None) -> str:
    """Projects also match a repository slug to its title ("lora-ticket-router" = "LoRA Ticket Router")."""
    return key(_split_slug(name or ""))


def url_key(url: str | None) -> str:
    if not url:
        return ""
    u = re.sub(r"^[a-z]+://", "", url.strip().casefold())
    u = re.sub(r"^www\.", "", u).split("#")[0].split("?")[0].rstrip("/")
    return u[:-4] if u.endswith(".git") else u


def _compact(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.casefold())


def _same_spelling(variant: str, canonical: str) -> bool:
    """A market synonym pair that is one name: same letters (python3 / Python), or the variant is the initials."""
    v, c = _compact(variant), _compact(canonical)
    words = re.findall(r"[A-Za-z0-9]+", canonical)
    return bool(v) and (v == c or (len(v) >= 2 and len(words) >= 2 and v == _initials(words).casefold()))


@lru_cache(maxsize=1)
def alias_map() -> dict[str, str]:
    """Matching key → the group's key (the first name's). Reviewed groups first, then the usable market pairs."""
    out: dict[str, str] = {}
    if ALIASES.exists():
        for group in json.loads(ALIASES.read_text()).get("groups", []):
            head = key(group[0])
            for name in group:
                out.setdefault(key(name), head)
    if MARKET.exists():
        for variant, canonicals in json.loads(MARKET.read_text()).get("names", {}).items():
            for canonical in canonicals:
                if key(variant) != key(canonical) and _same_spelling(variant, canonical):
                    head = out.get(key(canonical), key(canonical))
                    out.setdefault(key(variant), head)
                    out.setdefault(key(canonical), head)
    return out


def canonical(name: str | None, *, project: bool = False) -> str:
    k = project_key(name) if project else key(name)
    return alias_map().get(k, k)


def suggestions(names: list[str]) -> list[tuple[str, str, str]]:
    """Pairs that look alike but were NOT merged (no exact key, no alias): (a, b, why), for a person to review.
    Same letters without spaces ("HuggingFace" / "Hugging Face"), one name inside the other ("SageMaker" /
    "AWS SageMaker"), or one or two letters apart."""
    out, seen = [], set()
    uniq = list(dict.fromkeys(n for n in names if n))
    for i, a in enumerate(uniq):
        for b in uniq[i + 1:]:
            ka, kb = canonical(a), canonical(b)
            if ka == kb or (ka, kb) in seen or not ka or not kb:
                continue
            why = None
            if ka.replace(" ", "") == kb.replace(" ", ""):
                why = "same letters, different spacing"
            elif f" {ka} " in f" {kb} " or f" {kb} " in f" {ka} ":
                why = "one name inside the other"
            elif min(len(ka), len(kb)) >= 5 and _distance(ka, kb) <= 2:
                why = "one or two letters apart"
            if why:
                seen.add((ka, kb))
                out.append((a, b, why))
    return out


def _distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# --- Projects: descriptor brackets, "Name — subtitle", reviewed groups (audit round, item 2b) -------------------

PROJECT_ALIASES = ROOT / "config" / "project_aliases.json"
_SUBTITLE = re.compile(r"\s+[—–]\s+|:\s+|\s+-\s+")


@lru_cache(maxsize=1)
def _project_file() -> dict:
    return json.loads(PROJECT_ALIASES.read_text()) if PROJECT_ALIASES.exists() else {}


def _drop_descriptor(name: str) -> str:
    """"Campus dining app redesign (case study)" → "Campus dining app redesign": only a reviewed descriptor."""
    descriptors = {normalize_name(d) for d in _project_file().get("descriptors", [])}
    m = re.search(r"\s*\(([^()]*)\)\s*\.?\s*$", name)
    if m and normalize_name(m.group(1)) in descriptors:
        return name[:m.start()]
    return name


@lru_cache(maxsize=1)
def project_alias_map() -> dict[str, str]:
    out: dict[str, str] = {}
    for group in _project_file().get("groups", []):
        head = project_key(_drop_descriptor(group[0]))
        for name in group:
            out.setdefault(project_key(_drop_descriptor(name)), head)
    return out


def _whole_key(name: str | None) -> str:
    k = project_key(_drop_descriptor(name or ""))
    return project_alias_map().get(k, alias_map().get(k, k))


def project_keys(name: str | None) -> set[str]:
    """Every key a project name matches on: the whole name (descriptor dropped, reviewed group applied) and, for
    "StudySpot — finding open study rooms" or "Name: subtitle", the name before the subtitle."""
    if not name:
        return set()
    base = _drop_descriptor(name)
    keys = {_whole_key(name)}
    head = _SUBTITLE.split(base, maxsplit=1)[0]
    if head != base:
        keys.add(_whole_key(head))
    return {k for k in keys if k}


def same_project_name(a: str | None, b: str | None) -> bool:
    """Same project: the whole names match, or one whole name is exactly the other's name before its subtitle.
    Two subtitles alone never match ("Churn: a study" / "Churn: v2" share only "Churn")."""
    wa, wb = _whole_key(a), _whole_key(b)
    return bool(wa and wb) and (wa == wb or wa in project_keys(b) or wb in project_keys(a))


# --- Awards (audit round, item 2d) --------------------------------------------------------------------------------

AWARD_ALIASES = ROOT / "config" / "award_aliases.json"
_AMOUNT = re.compile(r"\(\s*(?:[$€£₹]|usd|inr|eur)\s*[\d.,]+\s*[kKmM]?\s*\)|\(\s*[\d.,]+\s*(?:usd|inr|eur)\s*\)", re.I)


@lru_cache(maxsize=1)
def _award_file() -> dict:
    return json.loads(AWARD_ALIASES.read_text()) if AWARD_ALIASES.exists() else {}


def has_amount(name: str | None) -> bool:
    return bool(_AMOUNT.search(name or ""))


def _words(name: str | None, drop_words: bool) -> list[str]:
    n = _AMOUNT.sub(" ", (name or "").replace("&", " and "))
    drop = {w.casefold() for w in _award_file().get("drop_words", [])} if drop_words else set()
    return [w for w in normalize_name(n).split() if w not in drop]


def _clauses_dropped(words: list[str]) -> list[str]:
    """A leading 'Recognized with the …' and a trailing 'for …' clause dropped."""
    text = " ".join(words)
    for lead in sorted(_award_file().get("lead_phrases", []), key=len, reverse=True):
        lead = normalize_name(lead)
        if text.startswith(lead + " "):
            text = text[len(lead) + 1:]
            break
    return re.sub(r"\s+for\s+.*$", "", text).split()


def award_text(name: str | None) -> str:
    """An award's words for matching: '&' = 'and', a bracketed amount dropped, reviewed drop words dropped."""
    return " ".join(_words(name, True))


def _award_key(name: str | None) -> str:
    return " ".join(_clauses_dropped(_words(name, True)))


@lru_cache(maxsize=1)
def _award_groups() -> dict[str, str]:
    out: dict[str, str] = {}
    for group in _award_file().get("groups", []):
        for n in group:
            out.setdefault(_award_key(n), _award_key(group[0]))
    return out


def award_key(name: str | None) -> str:
    """The matching key of an award: award_text with the leading and trailing clauses dropped, then a reviewed
    group applied."""
    k = _award_key(name)
    return _award_groups().get(k, k)


def contained(needle_key: str, haystack: str | None) -> bool:
    """needle_key appears as whole words in the award text of haystack."""
    return bool(needle_key) and f" {needle_key} " in f" {award_text(haystack)} "


def award_completeness(name: str | None) -> tuple[int, int]:
    """Which name to show for merged awards: one with an amount first, then the one with more words once the
    leading and trailing clauses are dropped (drop words count: "Merit Fellowship" is more complete)."""
    return int(has_amount(name)), len(_clauses_dropped(_words(name, False)))


# --- Education (audit round, item 2c) ---------------------------------------------------------------------------

DEGREE_ALIASES = ROOT / "config" / "degree_aliases.json"


@lru_cache(maxsize=1)
def _degree_aliases() -> list[tuple[str, str]]:
    """(normalized alias, group), longest first."""
    groups = json.loads(DEGREE_ALIASES.read_text()).get("groups", {}) if DEGREE_ALIASES.exists() else {}
    pairs = [(normalize_name(a), g) for g, names in groups.items() for a in names]
    return sorted({p for p in pairs if p[0]}, key=lambda p: -len(p[0]))


def degree_group(degree: str | None) -> str | None:
    """The reviewed group a degree belongs to ("M.S. in Data Science" → masters), from the degree's first words."""
    d = normalize_name(degree)
    if not d:
        return None
    for alias, group in _degree_aliases():
        if d == alias or d.startswith(alias + " "):
            return group
    return None


def school_key(school: str | None) -> str:
    words = normalize_name((school or "").replace("&", " and ")).split()
    return " ".join(w for w in words if w != "the")
