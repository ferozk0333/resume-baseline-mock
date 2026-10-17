"""PARSE_README (specs/18 §4.3 item 22): a repository's README without an LLM call.

The README is split into blocks. Code decides the clear ones (code, badges and images, and setup / install /
license / contributing sections are dropped; overview, features, architecture and results are kept); Jev decides
the rest ("describes what was built" / "a result or outcome" are kept; setup and other are dropped). Kept "what
was built" blocks, verbatim and in order, become the project description (after the repository's own description,
capped at README_MAX_CHARS); kept "result or outcome" blocks become the project's bullets, one per sentence, list
item, verbatim (a results table is one bullet: its header and up to README_TABLE_MAX_ROWS rows), so the outcome and
impact judges see them as they see LLM bullets. Claims: the repository languages from the GitHub API, and vocabulary names inside kept
blocks that Jev confirms the candidate used. Too many blocks still unclear after Jev → the README goes to the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core import config
from core.text import normalize_name
from extraction import schemas
from extraction.parse.claims import Vocabulary, find_names, guess_kind
from extraction.parse.common import ParseResult, Report, Timer, gate, src, states_use
from extraction.parse.labeller import Ask

DROP = {"install", "installation", "installing", "setup", "set up", "getting started", "get started", "quick start",
        "quickstart", "requirements", "prerequisites", "dependencies", "usage", "how to run", "how to use", "running",
        "run", "run locally", "running locally", "local development", "development", "configuration", "config",
        "environment", "environment variables", "env", "docker", "build", "building", "commands", "scripts",
        "project structure", "folder structure", "directory structure", "repository structure", "repo structure",
        "file structure", "structure", "files", "contributing", "contribution", "contributions", "license", "licence",
        "acknowledgements", "acknowledgments", "credits", "contact", "author", "authors", "references", "citation",
        "cite", "support", "faq", "table of contents", "contents", "toc", "todo", "to do", "roadmap", "changelog",
        "tests", "testing", "running tests", "api reference", "troubleshooting", "notes", "disclaimer", "links",
        "resources", "next steps", "future work", "future improvements", "deploy", "deployment steps", "steps",
        "clone", "cloning", "installation and setup", "setup and installation", "how to install"}
BUILT = {"overview", "about", "introduction", "intro", "description", "features", "key features", "what it does",
         "how it works", "architecture", "system architecture", "design", "system design", "approach", "methodology",
         "method", "methods", "pipeline", "implementation", "how it was built", "how i built it", "solution",
         "components", "highlights", "project overview", "summary", "what i built", "workflow", "the project",
         "project description", "about the project", "technical details", "tech stack", "technologies", "stack",
         "built with", "model", "models", "agents", "tools", "capabilities", "key components", "core features"}
RESULT = {"results", "result", "performance", "evaluation", "metrics", "benchmarks", "benchmark", "outcomes",
          "outcome", "impact", "findings", "accuracy", "experiments", "experimental results", "key results",
          "model performance", "evaluation results", "key findings", "achievements"}
BLOCK_KINDS = {
    "built": "Describes what was built: the project's purpose, features, architecture or how it works.",
    "result": "Describes a result or outcome: a metric, an evaluation, a benchmark, users or impact.",
    "setup": "Setup, installation, configuration, usage commands, requirements or deployment steps.",
    "other": "Anything else: license, badges, contributing, contact, links or acknowledgements.",
}


@dataclass
class Block:
    text: str
    heading: str
    kind: str                     # prose | list | table | code
    verdict: str | None = None    # built | result | setup | other (None = unclear)
    by: str = "parser"            # parser | jev
    raw: str = ""                 # the block's lines as written (Markdown marks kept): bullet quotes come from here


_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b[^>]*>|\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)")
_HTML = re.compile(r"</?(?:p|div|br|a|span|center|h\d|b|i|em|strong|sup|sub|details|summary|picture|source)\b[^>]*>",
                   re.I)


def _clean(line: str) -> str:
    line = _IMG.sub("", line)
    line = _HTML.sub("", line)
    line = re.sub(r"\*\*|__|`", "", line)                     # emphasis and code marks (the words stay)
    if re.fullmatch(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?", line.strip()):
        return ""                                          # a table's separator row
    return line.strip()


def blocks(readme: str) -> list[Block]:
    """Paragraphs, lists, tables and code, each under its nearest heading. Images, badges and HTML wrappers are
    removed; a line left empty by that is dropped."""
    out: list[Block] = []
    heading, cur, kind, fence = "", [], None, False
    raw_lines: list[str] = []
    readme = re.sub(r"<!--.*?-->", "", readme, flags=re.S)

    def flush():
        nonlocal cur, kind, raw_lines
        text = "\n".join(cur).strip()
        if text:
            out.append(Block(text, heading, kind or "prose", raw="\n".join(raw_lines)))
        cur, kind, raw_lines = [], None, []

    lines = readme.splitlines()
    for i, raw in enumerate(lines):
        if raw.strip().startswith(("```", "~~~")):
            if fence:
                cur.append(raw)
                flush()
                fence = False
            else:
                flush()
                fence, kind, cur = True, "code", [raw]
            continue
        if fence:
            cur.append(raw)
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if m := re.match(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$", raw):
            flush()
            heading = _clean(m.group(1)).strip("*_ :")
            continue
        if raw.strip() and re.fullmatch(r"=+|-{3,}", nxt) and not raw.strip().startswith(("-", "*", "|")):
            flush()
            heading = _clean(raw).strip("*_ :")
            continue
        if re.fullmatch(r"\s*(=+|-{3,}|\*{3,}|_{3,})\s*", raw):
            continue
        if re.fullmatch(r"\s*\*\*[^*]{2,60}\*\*:?\s*", raw) and not cur:     # **Bold line** used as a heading
            flush()
            heading = raw.strip().strip("*: ")
            continue
        line = _clean(raw)
        if not line and kind == "table" and raw.strip().startswith("|"):
            raw_lines.append(raw.rstrip())                # a table's separator row: part of the verbatim quote
            continue
        if not line:
            if raw.strip() == "" or not cur:
                flush()
            continue
        this = "table" if line.startswith("|") else "list" if re.match(r"^\s*(?:[-*+]|\d+[.)])\s+", raw) else "prose"
        if cur and kind and kind != this and not (kind == "list" and this == "prose" and raw.startswith(" ")):
            flush()
        kind = kind or this
        cur.append(line)
        raw_lines.append(raw.rstrip())
    if fence:
        flush()
    flush()
    return out


_SETUP_WORDS = {"install", "installation", "installing", "setup", "configure", "configuration", "commands", "command",
                "prerequisites", "requirements", "dependencies", "usage", "clone", "run", "running", "executed",
                "execute", "env", "environment", "build", "deploying", "steps"}
_OTHER_WORDS = {"license", "licence", "contributing", "contributors", "contact", "credits", "acknowledgements",
                "acknowledgments", "citation", "references", "links", "disclaimer", "author", "authors"}
_RESULT_WORDS = {"results", "result", "eval", "evaluation", "evaluations", "performance", "metrics", "benchmark",
                 "benchmarks", "accuracy", "impact", "outcomes", "outcome", "findings"}
_BUILT_WORDS = {"architecture", "overview", "design", "capabilities", "features", "feature", "components", "workflow",
                "technologies", "tools", "frameworks", "libraries", "stack", "objective", "objectives", "approach",
                "pipeline", "processing", "agents", "agent", "flow", "implementation", "methodology", "decisions",
                "concepts", "how", "works", "model", "models", "about", "introduction", "summary", "highlights"}


def _by_words(heading: str) -> str | None:
    words = set(normalize_name(heading).split())
    if words & _SETUP_WORDS:
        return "setup"
    if words & _OTHER_WORDS:
        return "other"
    if words & _RESULT_WORDS:
        return "result"
    if words & _BUILT_WORDS:
        return "built"
    return None


def _rule(b: Block) -> str | None:
    """built | result | setup | other by code, or None when it needs Jev."""
    h = normalize_name(b.heading)
    if b.kind == "code":
        return "setup"
    if re.fullmatch(r"[\s|:-]+", b.text):
        return "other"
    if h in DROP or any(h.startswith(d + " ") for d in ("install", "setup", "license", "contributing", "how to run")):
        return "setup" if h not in {"license", "licence", "contributing", "contact", "credits", "acknowledgements",
                                    "acknowledgments", "authors", "author", "citation", "references", "links"} \
            else "other"
    if h in RESULT:
        return "result"
    if h in BUILT:
        return "built" if b.kind != "table" else None
    if not b.heading:
        return "built" if len(b.text.split()) >= 6 and b.kind != "table" else "other"   # the opening paragraph
    by_words = _by_words(b.heading)
    if b.kind == "table":
        return by_words if by_words in ("result", "setup", "other") else None
    return by_words


def _title(readme: str) -> str:
    m = re.search(r"^\s{0,3}#\s+(.*)$", readme, re.M)
    return _clean(m.group(1)) if m else ""


def parse_readme(repo, vocab: Vocabulary, labeller) -> ParseResult:
    """One project from the repository plus claims, or a fallback report."""
    report = Report("github_repo")
    with Timer(report, labeller):
        result = _parse(repo, vocab, labeller, report)
    return result


def _parse(repo, vocab, labeller, report: Report) -> ParseResult:
    readme = repo.readme or ""
    title = normalize_name(_title(readme))
    bs = blocks(readme)
    for b in bs:
        if normalize_name(b.heading) == title and title:     # the H1 is the repository's name: its text is the intro
            b.heading = ""
        b.verdict = _rule(b)
    unclear = [b for b in bs if b.verdict is None]
    asks = [Ask(f"block_{i}", "choice", f"Classify the README block in `block_{i}`, under the heading in "
                f"`block_{i}_heading`.", tuple(BLOCK_KINDS.items()),
                ((f"block_{i}", b.text[:1500]), (f"block_{i}_heading", b.heading or "(none)")))
            for i, b in enumerate(bs) if b.verdict is None]
    answers = labeller.ask(asks) if asks else {}
    for i, b in enumerate(bs):
        a = answers.get(f"block_{i}")
        if b.verdict is None and a is not None and a.trusted:
            b.verdict, b.by = a.label, "jev"
    gate(report, answers, [a.key for a in asks])
    report.notes.append(f"{len(bs)} block(s): {sum(b.by == 'parser' and b.verdict is not None for b in bs)} by rule, "
                        f"{sum(b.by == 'jev' for b in bs)} by Jev, {len(unclear)} sent to Jev")
    if not report.ok:
        return ParseResult(None, report)

    kept = [b for b in bs if b.verdict in ("built", "result")]
    # Build steps (a list, or paragraphs that open with a bold step label: "**1. Data processing:** …") become bullets
    # like results, so the outcome and ownership judges see them as they see LLM bullets; other "what was built"
    # text is the description.
    steps = [b for b in kept if b.verdict == "built" and is_steps(b)]
    built = [b for b in kept if b.verdict == "built" and not is_steps(b)]
    results = [b for b in kept if b.verdict == "result" or b in steps]
    parts, used = [], 0
    if repo.description:
        parts.append(repo.description.strip())
        used = len(parts[0])
    for b in built:
        room = config.README_MAX_CHARS - used
        if room <= 40:
            break
        text = b.text if len(b.text) <= room else _cut(b.text, room)
        if text:
            parts.append(text)
            used += len(text) + 2
    description = "\n\n".join(parts) or None
    by_jev = any(b.by == "jev" for b in built)

    methods: dict[int, str] = {}
    bullets = []
    for b in results:
        pairs = units(b)
        if b in steps and b.kind != "list" and pairs:          # a step paragraph is one bullet, as an LLM writes it
            pairs = [(" ".join(t for t, _ in pairs), b.raw.replace("\n", " ").strip())]
        for text, quote in pairs:
            bullet = schemas.Bullet(text=text, source=src(quote, f"repository, bullet {len(bullets) + 1}"))
            bullets.append(bullet)
            methods[id(bullet)] = b.by
    project = schemas.Project(kind="project", name=repo.name, description=description, url=repo.html_url, start=None,
                              end=None, role_index=None, bullets=bullets, source=src(f"Repository: {repo.full_name}",
                                                                                     "repository"))
    methods[id(project)] = "jev" if by_jev else "parser"
    claims: list[schemas.Claim] = []
    for lang in top_languages(repo):
        c = schemas.Claim(kind="tool", name=lang, bullet_ref=None, source=src(lang, "languages"))
        claims.append(c)
        methods[id(c)] = "parser"
    seen = {normalize_name(x) for x in top_languages(repo)}
    found = []
    for b in kept:
        for f in find_names(b.text, vocab):
            if f.norm not in seen:
                seen.add(f.norm)
                found.append((f, b))
    asks = [states_use(f"uses_{i}", f.text, b.text[:1500]) for i, (f, b) in enumerate(found)]
    answers = labeller.ask(asks) if asks else {}
    for i, (f, b) in enumerate(found):
        a = answers.get(f"uses_{i}")
        if a is not None and a.trusted and a.label == "yes":
            c = schemas.Claim(kind=guess_kind(f.text, vocab), name=f.text, bullet_ref=None,
                              source=src(f.text, "readme"))
            claims.append(c)
            methods[id(c)] = "jev"
    unconfirmed = sum(1 for i in range(len(found)) if (answers.get(f"uses_{i}") is None
                                                        or not answers[f"uses_{i}"].trusted))
    if unconfirmed:
        report.notes.append(f"{unconfirmed} vocabulary name(s) not confirmed by Jev: left out")
    report.items = {"projects": 1, "bullets": len(bullets), "claims": len(claims), "kept_blocks": len(kept),
                    "blocks": len(bs)}
    return ParseResult(schemas.PortfolioExtract(projects=[project], claims=claims), report, methods)


_MARKER = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


_STEP_LABEL = re.compile(r"^\s*\*\*\s*\d+[.)]\s*[^*]{2,60}\*\*")          # "**1. Data processing:** …"
_DONE = {"built", "ran", "wrote", "led", "made", "set", "cut", "won", "drove", "grew", "taught", "rebuilt", "began",
         "brought", "chose", "found", "held", "kept", "met", "ran", "sold", "spent", "took", "won", "fed", "fit"}


def _accomplishment(item: str) -> bool:
    """A list item that says what was done ("Built parameterized pipelines…", "Used lookup activities…"), not a
    feature or component ("Weather Forecast: Fetches real-time weather.")."""
    m = re.match(r"\W*([A-Za-z]+)", _clean(item))
    word = m.group(1).casefold() if m else ""
    return word in _DONE or (word.endswith("ed") and len(word) > 4)


def is_steps(b: Block) -> bool:
    """Build steps: numbered bold step paragraphs, or a list whose every item is an accomplishment."""
    if b.kind == "list":
        items = [x for x in b.raw.splitlines() if _MARKER.match(x)]
        return bool(items) and all(_accomplishment(_MARKER.sub("", x)) for x in items)
    return bool(_STEP_LABEL.match(b.raw))


def units(b: Block) -> list[tuple[str, str]]:
    """A result block as (bullet text, quote) pairs: one per list item or sentence; a table is one bullet. The quote
    is the source as written; the text drops Markdown marks only."""
    out: list[tuple[str, str]] = []
    lines = [x for x in b.raw.splitlines() if x.strip()]
    if b.kind == "table":
        # One bullet for the whole table (Phase 3 review): the header row plus up to README_TABLE_MAX_ROWS data rows,
        # verbatim. The quote is the source lines as written (the separator row included, so it is one span).
        sep = re.compile(r"\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?")
        keep, data = [], 0
        for x in lines:
            if not sep.fullmatch(x.strip()) and keep and data >= config.README_TABLE_MAX_ROWS:
                break
            keep.append(x.strip())
            if not sep.fullmatch(x.strip()) and len(keep) > 1:
                data += 1
        rows = [r for r in keep if not sep.fullmatch(r)]
        text = "\n".join(" | ".join(c for c in (_clean(c) for c in r.strip("|").split("|")) if c) for r in rows)
        if rows:
            out.append((text, "\n".join(keep)))
    elif b.kind == "list":
        items: list[str] = []
        for x in lines:
            if _MARKER.match(x) or not items:
                items.append(_MARKER.sub("", x).strip())
            else:
                items[-1] += " " + x.strip()                  # an item's wrapped line
        out = [(_clean(i), i) for i in items]
    else:
        joined = " ".join(x.strip() for x in lines)
        parts: list[str] = []
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“(*])", joined):
            if parts and re.fullmatch(r"\**\d+[.)]", parts[-1].strip()):
                parts[-1] = f"{parts[-1]} {sentence}"      # "**1. Data processing:** …": the number opens the step
            else:
                parts.append(sentence)
        out += [(_clean(x), x.strip()) for x in parts]
    # A lead-in that only introduces what follows ("Compared to an unregularized baseline:") is not a bullet.
    return [(t, q) for t, q in out if len(t.split()) >= 3 and not t.rstrip().endswith(":")]


def top_languages(repo, n: int = 3) -> list[str]:
    """The repository's main languages: up to n with at least 10% of its code (GitHub lists them by size), or the
    first n listed when sizes are unknown. Vendored files can add a long tail of languages nobody wrote."""
    langs = list(repo.languages)
    total = sum(repo.languages.values())
    if total:
        langs = [x for x in sorted(langs, key=lambda k: -repo.languages[k]) if repo.languages[x] / total >= 0.10]
    if repo.language and repo.language not in langs:
        langs.insert(0, repo.language)
    return langs[:n]


def _cut(text: str, room: int) -> str:
    """Whole sentences that fit, else whole words."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > room:
            break
        out = f"{out} {s}".strip()
    if out:
        return out
    cut = text[:room]
    return cut[:cut.rfind(" ")] if " " in cut else cut


def repo_from_text(text: str):
    """A Repo rebuilt from its saved source text (Repo.as_text): offline comparisons on recorded runs."""
    from ingest.github import Repo
    head, _, readme = text.partition("\n\nREADME:\n")
    f = dict(line.split(": ", 1) for line in head.splitlines() if ": " in line)
    full = f.get("Repository", "")
    langs = [x.strip() for x in f.get("Languages", "").split(",") if x.strip()]
    return Repo(name=full.split("/")[-1], full_name=full, description=f.get("Description") or None,
                language=f.get("Primary language") or None, html_url=f.get("URL", ""), is_fork=f.get("Fork") == "yes",
                created_at=None, pushed_at=f.get("Last push") or None, languages={x: 0 for x in langs},
                own_commits=int(f.get("Commits by user") or 0), readme=readme or None)
