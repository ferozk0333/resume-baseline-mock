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
- **Interview plan:** stages, duration and availability windows live in Job settings; the Pipeline board moves
  cards between stages.
- **Email:** `EMAIL_PROVIDER=outbox` (default) stores emails and shows them on the Outbox page; nothing leaves the
  machine. `smtp` sends with `SMTP_*` and `EMAIL_FROM`. While `DEMO_EMAIL_TO` is set (default), every email goes
  to that address only, with the intended recipient named in the subject and body.
- `make demo` runs on fakes: candidate uploads are not read and get preset scores (a banner says so). For real
  processing of an upload (Jev + Gemini, never Claude), stop the demo and run `make live` (same database, so the recruiter jobs stay).
- Paused "Not moving forward" drafts and hiring workflows are checkpointed to `<db>.graphs.db` (LangGraph SQLite)
  and survive a restart.
- `make demo` drafts emails with templates (no model). For model drafts on the free tier:
  `EMAIL_LIVE=1 EMAIL_MODEL=gemini/gemini-3.5-flash-lite make demo`. "Why" points and phone-screen questions are code templates ("Demo text").

### Create a job (specs/14)
Jobs page: tabs Open · Drafts · Closed and **Create job** in 4 steps: start (upload or paste a job description,
copy a job or role template, or from scratch) → role & requirements → hiring setup → review & publish. Every change
is autosaved, so a draft resumes at its step. Reading a JD is one `JOB_EXTRACT_MODEL` call (default
`gemini/gemini-3.8-flash`); every field is quote-checked and marked "From JD"; salary, benefits, EEO, team size and
"how to apply" are never requirements. If the model is unavailable the form opens empty next to the JD text.
A published job owns its baseline (never listed as a candidate role); candidates apply from Home with their
confirmed profile. Editing requirements or "What good looks like" asks first: "N applicants will be re-scored.
About M model calls." Demo: `JOB_EXTRACT_LIVE=1 make demo` reads JDs with the real model.

### Market baselines (specs/15)
The **Market** page (recruiter navigation) builds a baseline per role from real job postings:
fetch (official Greenhouse / Lever / Ashby job-board APIs only; `config/market_sources.yaml`, placeholders until the
companies are chosen) or add postings by hand → dedupe → level (code first) → one cached extraction per posting
(`MARKET_EXTRACT_MODEL`, the spec 14 reader) → names through the reviewed map `config/market_synonyms.json` (new names
wait for a person) → aggregation in code (company → tier → overall shares, thresholds with hysteresis) → a draft
version → a person publishes it. **Baseline source** per role switches candidate scoring between Manual (default)
and the active Market version, after a "re-score N candidates" warning. Test mode: "Load test fixtures" and
"Recompute without a model" (`tests/fixtures/market/`). Costs before running at scale:
`.venv/bin/python scripts/market_dry_run.py [--fetch]` (never calls a model).

### Hiring workflow (specs/16)
One workflow per application, from shortlist to decision: a LangGraph graph that waits for the recruiter at every
email and every decision. **Nothing is sent automatically.**
- **Start:** "Start interview loop" on the job page (the ticked candidates, or the one open in the panel; anyone
  not shortlisted is moved there after a confirmation), or from the workflow page.
- **Workflow page** (`/recruiter/applications/{id}/workflow`, from a board card, the panel's Workflow button or
  Tasks): stage timeline · the current step · the email thread and history. Steps: approve the invite → the
  candidate's reply (paste it, pick a canned one, or IMAP) is read by `REPLY_MODEL` (code rules if unavailable) →
  pick a slot (proposed from the availability windows, skipping booked slots) → approve the confirmation with its
  `.ics` invite → interview → scorecards (1–4 per focus area) → summary by `FEEDBACK_MODEL` (code summary if
  unavailable; never a recommendation) → the recruiter decides: advance, not moving forward, or hold.
- **Tasks** (top navigation, with a count): everything waiting for the recruiter, oldest first.
- **Scheduled checks** every `WORKFLOW_TICK_SECONDS` (60): a nudge is *drafted* after `NUDGE_DAYS` (3) without a
  reply; feedback is asked for when the interview time passes. `POST /workflows/tick` runs them now.
- **Board moves** update the workflow and never send anything; when an email would normally follow, the workflow
  asks "Send stage email?".
- **Replies by email (optional):** `REPLY_INBOX=imap` polls the demo inbox read-only every `IMAP_POLL_MINUTES`
  (Gmail: same app password as SMTP). Replies match their workflow by the `[RS-xxxxxx]` token in the subject, or by
  In-Reply-To; our own sent copies are skipped. Every send is idempotent (one send per approved draft).

### Candidate experience (specs/17, phase A)
The candidate side is now one person's view: the demo has no sign-in, so the profile being viewed is remembered in
the browser ("Demo: viewing as … · Switch profile" in the header). With no profile yet, `/` opens the first run.
- **Onboarding** (`/onboarding`, 5 steps, every step resumable; progress is in the database, "Save and exit" any
  time): Add your proof → Target roles (1–3, one primary, picked *while* the documents are read) → Review
  ("Confirm profile") → Your baseline scores (one card per role: score, percentile or why not, 4 dimensions, top 3
  actions) → Finish your profile (optional application details, "Skip for now") → "You're set up".
- **Plans:** only the primary role's plan is written during onboarding. Other roles' plans are written the first
  time they are opened ("Preparing your plan…"), one plan call each (`POST /runs/{id}/plan`).
- **My scores** (`/scores`): a tab per role with the Score and Plan pages; add a role (scored at once, reusing the
  job-independent answers), remove a role, change the primary. **Profile** (`/profile`): sources, update documents
  or review again (both re-score), application details.
- **Application details** (work authorization, frequent questions, voluntary self-identification) are never sent
  to a model, never in a recruiter response and never read by scoring, pools or percentiles
  (`tests/test_candidate.py` checks all three).
- The old "Score a profile against a role" page is now **Admin → Demo tools** (`/admin/demo`); role baselines are
  under Admin too.

### Candidate Home and job detail (specs/17, phase B)
- **Home** (`/`, and **Jobs** at `/jobs`): the baseline score strip, then *Jobs for you*: open recruiter jobs on
  RightStaff (never market postings), list on the left and the job on the right (a phone opens the job as its own
  page, `/jobs/{id}`). Jobs for your target roles come first, grouped by JobMatch: **Strong** ≥ `JOBMATCH_STRONG` (75),
  **Close** ≥ `JOBMATCH_CLOSE` (55), **Stretch** below; other roles' jobs sit under "Other jobs" (collapsed). Filters
  (role, work mode, location, salary listed, saved; work mode pre-filled from application details), Save, and
  Not interested (optional reason; hides the job, changes no score; "Show again" undoes it).
- **JobMatch is a dummy preview** (`JOBMATCH_PREVIEW=1`): your baseline score for the job's role plus a stable
  offset in [−12, +8] (55 ± 15 without one), clamped to 5–98. Always shown with a "Preview" badge; never stored,
  never shown to recruiters, never in pools or percentiles.
- **Job detail:** JobMatch (Preview) with "Your baseline score for <role>" right below it; requirements with "Your
  closest evidence" only when the name or an alias exactly matches a criterion of your same-role baseline score
  (else "Checked when JobMatch launches"); "What would raise your match" from your plan; the posting; and Apply,
  which opens "What the recruiter will see" and then runs the existing apply.
- **Recruiter activity:** the last recruiter action on the job (applicant panel opened, stage moves, approved
  emails, workflow decisions, posting or editing the job): "Recruiter active today / N days ago"; "Reviewing
  applications" when a stage moved within `REVIEWING_DAYS` (14); "Low recent activity" after `LOW_ACTIVITY_DAYS` (30).
- **Demo jobs:** `seed/demo_jobs.json` (9 fictional jobs, 3 per role; job baselines written from the role baselines
  with small differences, no model calls). `scripts/seed_demo_jobs.py --db data/demo.db` adds them (idempotent);
  `scripts/seed_recruiter_demo.py` includes them. All demo jobs are marked as demo data.

Home, job detail and the apply sheet make no model calls.

**Wait screens.** Reading the documents shows each source added (Resume, LinkedIn, GitHub, Portfolio) with its
status and what was found in it; scoring shows one row per role with four segments (Experience, Expertise,
Authority, Trust) that fill as checks finish, "Ready" per role, Retry for a role that couldn't finish (the others
carry on), then "Your scores are ready". Both read saved data only (`/candidates/{id}/reading-progress`,
`/scoring-progress`). A "Good to know" card rotates tips from `config/wait_tips.yaml` (editable); it pauses on hover
or focus, stops once you use the arrows, and stays still with reduced motion.

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
