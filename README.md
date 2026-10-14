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

## Recruiter demo
```bash
.venv/bin/python scripts/render_test_profiles.py          # resume files for the viewer
.venv/bin/python scripts/seed_recruiter_demo.py --reset   # 3 jobs, 16 parsed profiles + 7 archetypes; no model calls
.venv/bin/python scripts/score_test_profiles.py           # optional, live: real judging of the 16 profiles
make demo                                                 # API on port 8000; then `make web`
```
The demo lives in `data/demo.db` (gitignored, kept between sessions). The 16 test profiles are parsed from
their structured files (exact quotes, no model) and labelled "Demo data: parsed from structured profile";
they are scored by `score_test_profiles.py` (Jev + Gemini). The 7 archetypes are scored with their scripted
labels, shown with a "Test archetype" badge and left out of benchmarks. The test variant DS4_padded is scored for
robustness checks only: it is never an applicant.

### Recruiter workflows (specs/13)
- **Workspace:** ranked list on the left, the candidate on the right with tabs (Overview, Requirements, EEAT,
  Resume, Work, LinkedIn, Activity); ↑/↓ move through the list; the tab and selection are in the URL.
- **Not moving forward:** a drawer with code-chosen reasons and an email draft (EMAIL_MODEL, checked in code; a
  "Template draft" when the model is unavailable). Nothing is sent until the recruiter clicks Send.
- **Interview loop:** shortlist, select, "Start interview loop": one review screen of scheduling drafts. The
  interview plan, duration and availability live in Job settings; the Pipeline board moves cards between stages.
- **Email:** `EMAIL_PROVIDER=outbox` (default) stores emails and shows them on the Outbox page; nothing leaves the
  machine. `smtp` sends with `SMTP_*` and `EMAIL_FROM`. While `DEMO_EMAIL_TO` is set (default), every email goes
  to that address only, with the intended recipient named in the subject and body.
- `make demo` drafts emails with templates (no model). For model drafts on the free tier:
  `EMAIL_LIVE=1 EMAIL_MODEL=gemini/gemini-3.5-flash-lite make demo`. "Why" points and phone-screen questions are code templates ("Demo text").

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
