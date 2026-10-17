"""Skill and tool claims in code (specs/18 §4.2 items 18–19).

(a) A skills list ("Languages: Python, SQL", "AWS (SageMaker, Bedrock)") is split with the same rules the
    extraction prompt gives the LLM: one claim per named skill or tool, slashes stay whole, a parenthetical list
    is split only when it lists tools (a platform label is kept as its own claim, a generic category label is
    dropped), and a method with its variants stays one claim.
(b) Inside sentences, code finds names from our vocabulary (baseline criteria names and aliases, the Market
    synonym table, and the demo tool list); Jev confirms each one ("the candidate states they use or know this").
Every name is returned exactly as written in the text, so its quote is the name itself.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from core import config
from core.text import normalize_name

# Category words: "Vector databases (Chroma, Qdrant)" drops the label and keeps the products.
_CATEGORY = {"tools", "tool", "databases", "database", "dbs", "frameworks", "framework", "libraries", "library",
             "platforms", "platform", "services", "languages", "apis", "stores", "store", "packages", "systems",
             "technologies", "stack", "infrastructure", "cloud", "clouds", "bi", "visualization", "orchestration",
             "devops", "mlops", "ides", "editors", "methods", "models", "techniques"}
# Labels that introduce something other than skills: never claims.
_NOT_SKILLS = {"open to", "interests", "hobbies", "coursework", "relevant coursework", "awards", "honors", "honours",
               "conferences", "certifications", "certification", "education", "references", "availability",
               "location", "contact", "gpa", "technical writing", "spoken languages",
               "industries", "domains", "roles", "seeking", "looking for", "visa", "work authorization",
               "achievements", "publications", "volunteering", "email", "phone", "linkedin", "github", "portfolio",
               "website"}
_ONE_THING = {"research and development", "r&d", "p&l", "profit and loss", "q&a", "at&t", "s&p", "trial and error",
              "black and white", "mergers and acquisitions", "m&a", "ci/cd", "a/b testing"}
# Common English words that are also tool names: matched in sentences only when capitalized as a name.
_COMMON = {"excel", "spark", "go", "notion", "react", "rest", "hive", "airflow", "keras", "chroma", "lambda", "glue",
           "flask", "redis", "kafka", "looker", "git", "linux", "docker", "jira", "figma", "dash", "streamlit", "r",
           "c", "make", "pig", "storm", "beam", "presto", "ray", "polars", "dagster", "prefect", "terraform"}
LABEL = re.compile(r"(?:(?<=^)|(?<=[\s,;.)]))(?P<label>[A-Z][\w&/+'’ -]{0,40}?):\s")
_CAMEL_WORDS = {"linkedin", "github", "gitlab", "youtube", "pytorch", "numpy", "fastapi", "javascript", "typescript",
                "postgresql", "mongodb", "mysql", "openai", "langchain", "langgraph", "huggingface", "powerpoint",
                "tensorflow", "devops", "mlops", "llmops", "iphone", "macos", "ios", "graphql", "nodejs", "dynamodb",
                "bigquery", "sagemaker", "opensearch", "eventbridge", "cloudformation", "cloudwatch", "mlflow"}
# Platforms whose products are listed in parentheses: "AWS (SageMaker, Bedrock)" → AWS, SageMaker, Bedrock.
_PLATFORMS = {"aws", "amazon web services", "gcp", "google cloud", "google cloud platform", "azure", "microsoft azure",
              "databricks", "snowflake", "hugging face", "huggingface", "openai", "anthropic", "oracle", "ibm", "sap",
              "salesforce", "atlassian", "microsoft", "google", "meta", "apache", "hashicorp"}


_SKILL_LABELS = {"skills", "skill", "expertise", "tech", "toolkit", "toolset", "toolchain", "proficient", "proficiencies"}


def unglue(text: str, vocab: "Vocabulary | None" = None) -> str:
    """A label glued to the previous list item when the PDF lost a line break ("…, BashFrameworks: LangGraph",
    "NumPyCertifications: …"): break before the label, at the last lower-to-upper case change of the run between
    the previous comma and the colon. Only after a list comma, so "GitHub: …" at the start of a line stays whole."""
    cuts = []
    for colon in (m.start() for m in re.finditer(r":\s", text)):
        start = max(text.rfind(",", 0, colon), text.rfind(";", 0, colon))
        if start < 0 or any(ch in text[start + 1:colon] for ch in ".()\n"):
            continue
        run = text[start + 1:colon]
        if normalize_name(run) in _CAMEL_WORDS or (vocab is not None and normalize_name(run) in vocab.terms):
            continue                                       # "LinkedIn: …", "GitHub: …" are labels as written
        camel = [i for i in range(1, len(run)) if run[i - 1].islower() and run[i].isupper()]
        if camel:
            cuts.append(start + 1 + camel[-1])
    out, last = [], 0
    for cut in cuts:
        out.append(text[last:cut])
        last = cut
    out.append(text[last:])
    return "\n".join(out)


@dataclass(frozen=True)
class Term:
    norm: str
    kind: str                 # skill | tool


@dataclass(frozen=True)
class Vocabulary:
    terms: dict[str, Term]    # normalized name → term
    longest: int              # words in the longest name

    def kind(self, name: str) -> str | None:
        t = self.terms.get(normalize_name(re.sub(r"\s*\(.*?\)\s*", " ", name)))
        return t.kind if t else None


def _demo_tools() -> set[str]:
    from extraction.structured import TOOLS
    return set(TOOLS)


@lru_cache(maxsize=8)
def _vocab_from(criteria: tuple[tuple[str, str, tuple[str, ...]], ...]) -> Vocabulary:
    tools = {normalize_name(t) for t in _demo_tools()}
    terms: dict[str, Term] = {}

    def add(name: str, kind: str):
        n = normalize_name(name)
        if n and len(n) >= 1 and n not in terms:
            terms[n] = Term(n, "tool" if n in tools or kind == "tool" else "skill")
    for t in sorted(tools):
        add(t, "tool")
    for kind, name, aliases in criteria:
        if kind in ("skill", "tool"):
            add(name, kind)
            for a in aliases:
                add(a, kind)
    syn = json.loads((config.ROOT / "config" / "market_synonyms.json").read_text())
    for name, targets in syn.get("names", {}).items():
        kinds = syn.get("kinds", {})
        add(name, "tool" if any(kinds.get(t) == "tool" for t in targets) else "skill")
    return Vocabulary(terms, max(len(n.split()) for n in terms))


def vocabulary(conn=None) -> Vocabulary:
    """From the baselines in the database (recruiter jobs included), or from the seed file without one."""
    rows: list[tuple[str, str, tuple[str, ...]]] = []
    if conn is not None:
        for r in conn.execute("SELECT kind, name, aliases FROM baseline_criteria ORDER BY id"):
            rows.append((r[0], r[1], tuple(json.loads(r[2] or "[]"))))
    else:
        seed = json.loads(config.SEED_PATH.read_text())
        for b in seed["baselines"]:
            rows += [(c["kind"], c["name"], tuple(c.get("aliases", []))) for c in b["criteria"]]
    return _vocab_from(tuple(sorted(set(rows))))


# --- (a) skills lists -----------------------------------------------------------------------------------------

def _split_top(text: str, seps: str = ",;|•·") -> list[str]:
    """Split at separators outside parentheses."""
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += ch in "([" and 1 or 0
        depth -= ch in ")]" and 1 or 0
        if ch in seps and depth <= 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return [p.strip() for p in out if p.strip()]


def _is_category(label: str, vocab: Vocabulary) -> bool:
    words = normalize_name(label).split()
    return bool(words) and (words[-1] in _CATEGORY or (vocab.kind(label) is None and words[-1].endswith("s")))


def _item_names(item: str, vocab: Vocabulary) -> list[str]:
    item = re.sub(r"^(?:and|&|or)\s+", "", item.strip(), flags=re.I).strip(" .:;")
    item = re.sub(r"\s+(?:etc\.?|and more)$", "", item, flags=re.I).strip(" .")
    if not item:
        return []
    m = re.fullmatch(r"(?P<label>[^()]+?)\s*\((?P<inner>[^()]*)\)", item)
    if m and "," in m["inner"]:
        inner = [x for x in _split_top(m["inner"]) if x]
        label = m["label"].strip()
        if normalize_name(label) in _PLATFORMS:
            return [label, *inner]                           # "AWS (SageMaker, Bedrock)" → AWS too
        if _is_category(label, vocab):
            return inner                                     # "Vector Databases (Chroma, Qdrant)" → the products
        return [item]                                        # a method with its variants: one claim
    if m := re.fullmatch(r"(?P<what>.+?)\s+(?:with|using|via|on|in)\s+(?P<tool>[A-Z][\w.+#/ -]{1,40})", item):
        return [m["tool"].strip()]                           # "CI/CD with GitHub Actions" → the tool named
    if normalize_name(item) in {normalize_name(x) for x in _ONE_THING} or "/" in item:
        return [item]
    sides = [s.strip() for s in re.split(r"\s+(?:and|&)\s+", item) if s.strip()]
    if len(sides) > 1 and all(len(s.split()) <= 2 for s in sides):
        return sides                                         # "Docker & Kubernetes"
    if len(item.split()) <= 5:
        return [item]
    found = find_names(item, vocab, confirmed_only=True)     # a phrase, not a name: keep the names inside it
    return [f.text for f in found]


def split_skills_text(text: str, vocab: Vocabulary, labelled_only: bool = False) -> list[str]:
    """Names in a skills line or block. With labelled_only (a summary paragraph), only "Label: a, b, c" lists
    count, a list ends at the end of its sentence, and labels that introduce non-skills are skipped."""
    text = unglue(text, vocab)
    names: list[str] = []
    for chunk in text.split("\n"):
        labels = list(LABEL.finditer(chunk))
        if labelled_only and not labels:
            continue
        segments = []
        if not labels:
            segments.append(("", chunk))
        else:
            if not labelled_only and labels[0].start() > 0:
                segments.append(("", chunk[:labels[0].start()]))
            for i, m in enumerate(labels):
                end = labels[i + 1].start() if i + 1 < len(labels) else len(chunk)
                segments.append((m["label"], chunk[m.end():end]))
        for label, seg in segments:
            if label and normalize_name(label) in _NOT_SKILLS:
                continue
            if labelled_only:
                # In prose only a label that names a kind of skill introduces a skills list ("Core stack:",
                # "Frameworks:", "MLOps and infrastructure:"), not "Open to:" or "Experience spans:".
                if not label or not set(normalize_name(label).split()) & (_CATEGORY | _SKILL_LABELS):
                    continue
                seg = re.split(r"(?<=[a-z0-9)])\.\s+(?=[A-Z])|\.$", seg.strip())[0]   # the list ends with its sentence
            for item in _split_top(seg):
                for n in _item_names(item, vocab):
                    n = n.strip(" .")
                    if n and len(n) <= 60 and n not in names:
                        names.append(n)
    return names


# --- (b) names inside sentences --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Found:
    text: str                 # exactly as written
    norm: str
    kind: str
    start: int


_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#.\-/]*[A-Za-z0-9+#]|[A-Za-z0-9]")


def find_names(text: str, vocab: Vocabulary, confirmed_only: bool = False) -> list[Found]:
    """Vocabulary names in a sentence, longest match first, as written. Short or common words must look like names
    (capitalized; one or two letters exactly as listed, e.g. "R")."""
    words = [(m.group(0), m.start(), m.end()) for m in _WORD.finditer(text)]
    out: list[Found] = []
    i = 0
    while i < len(words):
        hit = None
        for n in range(min(vocab.longest, len(words) - i), 0, -1):
            span = text[words[i][1]:words[i + n - 1][2]]
            norm = normalize_name(span)
            term = vocab.terms.get(norm)
            if term is None:
                continue
            if n == 1 and (norm in _COMMON or len(norm) <= 2):
                w = words[i][0]
                if len(norm) <= 2 and w not in ("R", "C", "Go", "C++", "C#", "AI", "ML", "BI", "QA", "UX", "UI"):
                    continue
                if norm in _COMMON and not w[:1].isupper():
                    continue
            hit = Found(span, norm, term.kind, words[i][1])
            i += n
            break
        if hit is None:
            i += 1
            continue
        if not any(f.norm == hit.norm for f in out):
            out.append(hit)
    return out


def kind_of(name: str, vocab: Vocabulary) -> str | None:
    """skill or tool from the vocabulary (display only: kind changes no score); None when unknown."""
    k = vocab.kind(name)
    if k:
        return k
    base = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
    parts = [vocab.kind(p) for p in re.split(r"\s+", base)]
    return "tool" if parts and parts[-1] == "tool" else None


def guess_kind(name: str, vocab: Vocabulary) -> str:
    """Vocabulary first; else a one-word product-like name (inner capitals or digits: "HuggingFace", "EC2") is a
    tool, anything else a skill. Display only: kind changes no score."""
    k = kind_of(name, vocab)
    if k:
        return k
    base = re.sub(r"\s*\(.*?\)\s*", " ", name).strip()
    if " " not in base and (re.search(r"[a-z][A-Z]|\d", base) or base.isupper()):
        return "tool"
    return "skill"


_AWARD_SENTENCE = re.compile(r"\b(recogni[sz]ed with|awarded|received the|winner of|won the|recipient of)\b.*"
                             r"\b(award|fellowship|scholarship|prize|medal|honou?r)\b", re.I)


def award_sentences(text: str) -> list[str]:
    """Awards stated in a summary ("Recognized with the X Fellowship for …"): the sentence, verbatim (validation
    trims an award to its clause)."""
    text = re.sub(r"(?<=[a-z])\.(?=[A-Z])", ". ", text)       # "cluster.Recognized" (a lost space in the PDF)
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if _AWARD_SENTENCE.search(sentence):
            out.append(sentence.strip().rstrip("."))
    return out
