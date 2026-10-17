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

### Candidate experience, phase C (specs/17)
- **My applications** (`/applications`): each application with a status chip and a timeline: Applied → Viewed by
  recruiter (first applicant panel opened) → Shortlisted → Interview ("Round N") → Offer → Hired / Declined, or Not
  moving forward with the reasons of the email that was sent, each linked to the matching gap in your plan. A board
  move to Not moving forward shows only once the email is approved and sent. Never scores, ranks, other candidates,
  notes, scorecards or internal tasks.
- **Closure principle:** an open application without a stage change for `CLOSURE_DAYS` (14) appears on the
  recruiter's Tasks as "<Name> has waited N days for an answer". Nothing is sent; the candidate sees "Waiting for
  the recruiter. RightStaff reminds recruiters to answer every application."
- **Mark done** on Home's next best action (stored per candidate, role and action; Undo). It changes no score: "Add
  the proof to your profile and check again to update your score."
- **Role family** on Create job step 2 (suggested from the title, editable; "None" = JobMatch starts from 55).
  Posting a job counts once as recruiter activity; edits don't, only actions on applicants afterwards.
- **Candidate wording:** no "judgment", internal keys, model or provider names, or raw counts on candidate screens.
  The Score page's technical detail (labels, methods, raw breakdown) shows only from Admin → Demo tools
  (`/runs/{id}?technical=1`).

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

### Speed and failover (specs/18, phase 1)
- **Routes per task** (`config.ROUTES`, override with `ROUTE_<TASK>`): ordered provider/model lists for
  extract_document, extract_web, judge_single, judge_grouped and plan. The first model is the one used today; the
  others take over only when it is busy, overloaded or out of quota. A `claude-…` id on top works with no code change.
- **Failover, never sleep:** a 429, 5xx or timeout sends the call to the next model at once; the provider's answer
  wins over our counters (Google's retryDelay or per-day quota, Groq's `retry-after` / `x-ratelimit-*`). Only when
  every model is busy is there a wait of at most `ROUTE_MAX_WAIT_S` (5 s). At most `ROUTE_MAX_ATTEMPTS` (4) calls per
  request. Every model out of quota for the day: fail fast ("Daily free-tier limit reached…"; saved work is kept).
- **Limits per model** (requests and tokens per minute and per day, from `MODEL_LIMITS`); calls in flight per model up
  to its requests per minute; timeouts Gemini 45 s, Groq 20 s. Optional hedging (`HEDGE_AFTER_S`, off).
- **Early start:** the roles picked during reading are judged as soon as the documents are read; Confirm only
  finishes what's left (an item edited in Review is judged again).
- **LIVE guard:** real provider calls only with `LIVE=1` (`make live` sets it). Scripts that call real models need it too.
- Logs (`rightstaff.routing`): `failover: <task> <model> → <next> (<why>)`, `all busy: …`, `fail fast: …`. Runs record
  calls per task per model (`calls_by_task`) and failovers in their timings.

### Code-first extraction (specs/18, phase 3; PARSE_LINKEDIN on; README and resume parsing parked)
- **Design:** code builds the structure, Jev decides what is unclear, the LLM is the fallback. Every field is a
  verbatim span of the source, so the usual quote check, claim, award and publication rules apply unchanged.
- **`PARSE_LINKEDIN`** (on by default): a parser for LinkedIn's "Save to PDF" layout (sidebar + main column, font
  tiers read from the document). A real export shows less than the profile: only the top 3 skills, certificate names
  without issuer, date or credential ID, and no projects; the parser reads what the export shows, as the LLM does. **`PARSE_README`:** README blocks kept by rule (overview, features, architecture, results) or by Jev;
  setup, install, licence, badges and code are dropped. Kept "what was built" text becomes the repository's
  description (after its GitHub description, ≤1,500 characters); kept results become the project's bullets, one per
  sentence, list item or table row, verbatim.
  **`PARSE_RESUME=shadow`:** the résumé is still read by the LLM; afterwards the parser reads it too and the
  comparison is logged (`parser_runs`); nothing from it is shown or scored.
- **Confidence gate:** a document goes to the LLM when it doesn't look like the expected layout, a role has no dates,
  more than `PARSER_MAX_UNCLEAR` lines stay unclear, reading order looks scrambled, or a Jev label is below
  `JEV_CONFIDENCE_THRESHOLD`. Every item records how it was read (`method`: parser | jev | llm).
- **`REMOVE_HIDDEN_TEXT`** (on): white or near-background text (against what is drawn under it), text under 4 pt and
  off-page text are removed before reading; what was removed is kept for Admin
  (`GET /admin/candidates/{id}/extraction`, which also lists the parser log).
- **Review opens early:** each source is saved into the profile as soon as it is read (in the usual order, so the
  final profile is the one a single merge gives); Review opens once the resume is read, shows "Still reading
  LinkedIn…" per pending source, and Confirm waits for every source. Early-start judging runs again per source.
  An item deleted in Review is never re-added by a later source: Review shows "LinkedIn also mentions X · Add back".
- **Review (specs/19):** one entry card for roles, projects, education, certifications and every "More proof" type:
  the same field grid in view and edit, one card in edit at a time, bullets edited one by one (drag or ↑/↓, paste
  splits lines), delete with Undo, grouped and searchable skills (`config/skill_groups.yaml`), a section menu,
  "Needs your attention" and a sticky Confirm bar. Duplicates merge on a normalized name or
  `config/claim_aliases.json` (never on similarity; near misses are listed by `scripts/dedupe_report.py`, which also
  writes the offline before/after report to `timings/dedupe_report.md`). Education, patents, volunteering, courses
  and languages are display only: shown to recruiters, never judged or scored.
- **Gates:** `scripts/parser_gates.py` (offline; replays recorded judgments, `judges/replay_judge.py`) writes
  `timings/parser_gates.md`. `scripts/render_test_profiles.py --stress` renders the layout stress set.
  `scripts/readme_live_gate.py --estimate` counts the calls of the README gate on the latest live profile; `--run`
  (LIVE=1, after approval) runs it.
- **Shadow calls** (`PARSE_SHADOW_CALLS=1`): Jev confirms vocabulary skills inside résumé bullets, and one small call
  per document (`prompts/bullet_skills_system.txt`, the judge fallback model) lists skill and tool names from bullets
  that matched no vocabulary; names not written exactly as in their bullet are dropped.

### Fewer calls, more model families (specs/18 phase 2; all flags off, parked — see docs/spec18_summary.md)
- **`GROUPED_ITEM_JUDGING`:** complexity and work-sample questions in one batched call per family (same schema, a
  quote required above the lowest label, each failed item retried alone; complexity reasons first).
- **`REPO_CAP`** (`MAX_REPOS` 8): read and judge only the repositories most relevant to the role (then latest push,
  then stars); the others stay listed. **`GEMMA_JUDGE` / `GROQ_JUDGE`:** judging calls take turns across Flash-Lite and
  the family (Groq: single items only, never grouped calls, extraction or plans).
- **`PARTITIONED_BATCHES`:** the all-bullets skill/tool checks per source document; an unchanged document keeps its
  saved answer and the label is the best across sources.
- **Capabilities per model** (`config.model_caps`, override `MODEL_CAPS`): a request sends only what the model accepts
  (Gemma: no thinking setting). `scripts/phase2_compare.py --estimate` states the calls per flag; `--run FLAG` (LIVE)
  compares on vs off on the test profiles.

### Prompt-injection screen (specs/18 phase 4; `PROMPT_GUARD` on)
Llama Prompt Guard 2 on Groq (86M, then 22M; 512-token window, so overlapping chunks), in the background: never
blocks reading, never changes a score, never shown to recruiters. Visible text above `PROMPT_GUARD_THRESHOLD` is logged
for Admin; hidden text stays removed and, when also flagged, the candidate sees "We ignored hidden text in your …".
Admin → Screening lists removed hidden text, the parser log and flagged chunks (candidates and market postings).

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
- The code-first parsers (specs/18 phase 3) add no dependency: they use PyMuPDF and python-docx already listed, and
  their own date parser. OpenResume (AGPL) informed the ideas only; no code was copied.
