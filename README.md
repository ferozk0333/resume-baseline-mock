# RightStaff EEAT scoring — local mock

Scores a candidate profile (resume, LinkedIn PDF, GitHub, portfolio) against a baseline role
profile with the EEAT framework (Experience, Expertise, Authority, Trust; 19 subcategories), then
ranks the gaps and writes an improvement plan. Non-production: it exists to check that the scoring
logic works end to end. `CLAUDE.md` and `specs/` describe the design; `ITERATION_2.md` the current
iteration.

## Setup
```bash
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env        # then fill in ANTHROPIC_API_KEY, JEV_API_KEY and (recommended) GITHUB_TOKEN
cd frontend && npm install && cd ..
make dev                    # API on :8000 (docs at /docs) + web app on :3000
```
`make api` and `make web` start them separately.

The previous Streamlit UI is retired to `legacy/app/` unchanged and no longer maintained. It still runs with
`PYTHONPATH=. .venv/bin/streamlit run legacy/app/Home.py`. What it showed that the new app doesn't yet is
listed in `docs/backlog.md`.
`GITHUB_TOKEN` can be a fine-grained, read-only token for public repositories. Without it GitHub
allows 60 requests an hour, which one profile with ~20 repos nearly uses up. GitHub responses are
cached in the database for 24 hours either way.

## Models
Claude is the default for extraction, fallback judging, complexity and the plan. Any of these can run on
Groq instead by naming a Groq model with a `groq/` prefix, e.g. `EXTRACT_MODEL=groq/openai/gpt-oss-120b`
(strict JSON schema; `GROQ_API_KEY` in `.env`). Groq's free tier allows 8,000 tokens per minute, which is
too small for a full resume extraction or a full scoring run; it works for single small documents.

`LLM_PROVIDER=gemini` moves all four roles to Gemini models (`GEMINI_*_MODEL`, free-tier ones by default).
The free tier's limits are per project and shown in AI Studio, so set `GEMINI_RPM`, `GEMINI_TPM` and
`GEMINI_RPD`; when the daily quota runs out the run stops with a message, and the next run (after midnight
Pacific) reuses everything already extracted and judged. Free-tier prompts may be used by Google and read by
human reviewers: don't send real personal data on the free tier.

## Tests
```bash
.venv/bin/python -m pytest -q                       # offline: FakeJudge and fixtures, no API calls
RIGHTSTAFF_LIVE=1 .venv/bin/python -m pytest -q     # adds the live model checks (costs API calls)
.venv/bin/python scripts/render_test_profiles.py    # renders tests/fixtures/profiles/rendered/
```
`make e2e` runs the Playwright smoke test of the candidate flow (upload → review → score → plan) in your
local Chrome, against the API on fakes (`make fake-api`: FakeJudge, scripted extraction and plan, throwaway
database). No model calls.

`tests/test_regression.py` pins the FakeJudge archetype scores: they must not change in iteration 2.

## Timing a run
```bash
.venv/bin/python scripts/time_run.py --resume R.pdf --linkedin L.pdf --github USER \
    --github-fixture timings/github.json --label before
```
Uses a fresh database (nothing cached), records GitHub responses on the first run and replays them
afterwards, and writes a timing table, token counts and an estimated Claude cost to `timings/`.

## Third-party licences
- **PyMuPDF, pymupdf4llm and pymupdf-layout** (PDF → Markdown in `ingest/documents.py`) are
  dual-licensed **AGPL-3.0** or Artifex commercial. Fine for this local mock; a hosted or
  distributed product would need the commercial licence or a different parser.
- Everything else in `requirements.txt` is under a permissive licence (MIT, BSD or Apache-2.0).
