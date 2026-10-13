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
.venv/bin/streamlit run app/Home.py
```
`GITHUB_TOKEN` can be a fine-grained, read-only token for public repositories. Without it GitHub
allows 60 requests an hour, which one profile with ~20 repos nearly uses up. GitHub responses are
cached in the database for 24 hours either way.

## Tests
```bash
.venv/bin/python -m pytest -q                       # offline: FakeJudge and fixtures, no API calls
RIGHTSTAFF_LIVE=1 .venv/bin/python -m pytest -q     # adds the live model checks (costs API calls)
.venv/bin/python scripts/render_test_profiles.py    # renders tests/fixtures/profiles/rendered/
```
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
