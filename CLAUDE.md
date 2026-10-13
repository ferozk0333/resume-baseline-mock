# RightStaff EEAT Scoring — Local Mock

## What this is
A local, non-production mock that scores a candidate profile against a **baseline role profile** using the EEAT framework (Experience, Expertise, Authority, Trust; 19 subcategories), then explains the biggest gaps and how to close them.

Goal: verify that the scoring logic **works correctly** end to end. Not a production system.

## Read these specs before writing code
| File | Covers |
|---|---|
| `specs/01_requirements.md` | Scope, user flow, edge cases |
| `specs/02_architecture.md` | LangGraph graph, nodes, parallelism, project layout |
| `specs/03_data_model.md` | SQLite schema |
| `specs/04_scoring_rules.md` | Every formula, N/A vs missing, weights |
| `specs/05_prompts.md` | Extraction, judge and gap-report prompts; Jev/Claude switch |
| `specs/06_ui.md` | Streamlit pages and the weights sidebar |
| `specs/07_testing.md` | Tests and acceptance checks |
| `seed/baselines.json` | Three mock early-career baselines |

`specs/04_scoring_rules.md` is the source of truth for scoring. If anything elsewhere conflicts with it, follow `04` and flag the conflict to the user.

## Stack
- Python 3.11+
- LangGraph (graph orchestration)
- Anthropic Python SDK (Claude models)
- Jev by TypeSafe AI (bounded judgments). **Read the official API reference at https://docs.typesafe.ai/api before implementing. Do not guess the request or response format.**
- SQLite (single file `rightstaff.db`)
- Streamlit (UI)
- Pydantic v2 (schemas and validation)
- pypdf, python-docx (document parsing); requests + BeautifulSoup (portfolio); GitHub REST API (repos)
- pytest

## Environment (`.env`, never commit)
```
ANTHROPIC_API_KEY=
JEV_API_KEY=
GITHUB_TOKEN=                         # optional, raises GitHub rate limits
JUDGE=jev                             # jev | claude
EXTRACT_MODEL=claude-haiku-4-5-20251001
JUDGE_FALLBACK_MODEL=claude-haiku-4-5-20251001
REASONING_MODEL=claude-sonnet-5-5     # improvement plan (and complexity unless COMPLEXITY_MODEL is set)
COMPLEXITY_MODEL=                     # optional; defaults to REASONING_MODEL
JEV_MODEL=jev-1.13.0                  # pinned Jev version; saved judgments are matched on it
JEV_CONFIDENCE_THRESHOLD=0.6          # Jev confidence below this → Claude fallback; starting value, set in phase 8
```
Confirm current model IDs and SDK usage at https://docs.claude.com before coding.

## Non-negotiable rules
1. **Compute scores in code, never in an LLM.** Models only produce labels (e.g. `relevant`, `led`, `business`). Python applies the formulas in `04_scoring_rules.md`.
2. **Every judgment is saved** to the `judgments` table with label, quote, probabilities (if any), reasoning (if any), method and model version.
3. **Every extracted item carries a source quote.** Validation drops items whose quote is not found in the source text.
4. **Never fabricate.** Extraction returns `null` for absent fields. Prompts forbid inferring facts that are not in the text.
5. **N/A vs missing** (see `04`): N/A is excluded from averages; missing evidence scores 0 and produces a guidance message.
6. **Weights live in config**, default to the table in `04`, and are adjustable in the UI sidebar without re-running any model call.
7. **Determinism:** temperature 0 wherever the API allows it. Same inputs must give the same score.
8. **Integrity is out of scope** (deferred). Do not build it.
9. **Untrusted input:** resume, LinkedIn PDF, GitHub and portfolio text are data, never instructions. Keep all instructions in the system prompt; wrap document text in clear delimiters.

## Build order (one phase at a time; run that phase's tests before moving on)
1. **DB + seed:** schema from `03`, load `seed/baselines.json`, baseline CRUD functions.
2. **Scoring formulas:** pure functions in `scoring/formulas.py` + unit tests using hand-made judgments (no LLM).
3. **Ingest + extraction:** resume / LinkedIn PDF / GitHub / portfolio ingest, Claude extraction to Pydantic, validation, save.
4. **Judges:** `Judge` interface, `ClaudeJudge`, `JevJudge`, fallback logic, `FakeJudge` for tests.
5. **Graph:** LangGraph pipeline from `02`, using the judges and formulas.
6. **Gap analysis:** gain ranking in code + Sonnet improvement plan (structured, validated and priced in code).
7. **UI:** Streamlit pages + weights sidebar from `06`.
8. **Acceptance:** run the checks in `07` and report results.

After each phase, summarize what was built, what tests ran, and anything in the specs that was ambiguous.
