# 13 — Recruiter workspace, decision workflows, candidate median

Scoring logic does not change. Model calls only where marked; every model role is configurable (model-neutral) with a code-template fallback when the model or its quota is unavailable.

## 0. Fixes found in review (do first)
1. **Ranked list:** a large blank area sits above the first row; the top candidate (Maya Torres, 88.2) is not visible in the list. Fix the virtualized list's offset.
2. **Duplicate applicant:** `DS4_padded` appears as a second "Sofia Lindqvist". Test variants must not appear as applicants. Keep it for robustness tests only.
3. **Chips and table disagree:** Maya's must-have chip says "Data wrangling · met, backed" but the Requirements table row says "— · not shown". Chips, table and score must read from one source (the saved skill/tool evidence judgment). Add a test that they agree for every criterion of every demo applicant.
4. **Skills matched only in the skills list never see the bullets:** "Experimentation / A/B testing" shows "claimed only", although Maya's bullet says "Designed and analyzed 6 A/B tests". When a criterion's alias matches **only** the skills list, also send it to the all-bullets judge (the unmatched route), so a bullet with a near variant can earn credit. Model-neutral fix in matching; add a fixed-input test. Then re-score the 16 test profiles (Flash-Lite and Jev only; report calls) and report which labels and scores changed.
5. **Backing and source must refer to the same evidence:** "Python · artifact · skills list" is contradictory. Show the evidence item that earned the backing level (e.g. the repo) as the source.
6. **"p80" label:** show "Top 20%" (with a tooltip "Higher than 80% of N other candidates for this role").

## 1. Recruiter workspace: full width + tabs
**Layout**
- The recruiter job page uses the full window width (side padding only), not a centered column.
- Left: ranked list, fixed width (~380 px), its own scroll, sticky filters.
- Right: candidate panel filling the rest.
  - **Sticky header:** name, current title, demo badge, Match, Evidence confidence, Must-haves (x of y), percentile, actions (Shortlist, Not moving forward, stage dropdown once in the loop).
  - **Tabs below the header**, each scrolling on its own:

| Tab | Content |
|---|---|
| **Overview** (default) | Why this candidate (3 points) · Key differentiators (strength tags + the 3 strongest evidence items with source) · Must-have chips · Ask on the phone screen |
| **Requirements** | Requirements vs. evidence table |
| **EEAT** | Four dimensions and subcategories, expandable to evidence |
| **Resume** | Inline viewer |
| **Work** | Demo videos (embedded), GitHub repo cards, portfolio cards. Count badge, e.g. "Work (4)" |
| **LinkedIn** | Extracted data, "From the uploaded LinkedIn PDF" |
| **Activity** | Stage history, emails sent (with full text), decisions |

- A tab with no content is shown disabled with "Not provided" on hover.
- The selected tab persists when switching candidates and is reflected in the URL.
- ↑/↓ keys move through the list.
- **Overview must fit on one screen without scrolling at 1440×900.** Trim text (one line per point, expand on click) to meet this.

## 2. "Not moving forward" workflow (agent, human-approved)
**Flow** (small LangGraph graph): `build_reasons` (code) → `draft_email` (model) → `validate` (code) → **recruiter review (interrupt)** → `send` → `log`.

- Clicking **Not moving forward** opens a side drawer:
  - **Reasons**, pre-selected by code from: unmet or on-paper must-haves, the top 3 gaps (from the existing gap ranking), and missing key evidence. The recruiter can tick or untick them and add a free-text note.
  - **Draft email**, generated from the selected reasons only; editable.
  - Buttons: **Send**, **Save draft**, **Cancel**. **Nothing is ever sent without the recruiter clicking Send.**
- **Email rules** (prompt + code checks):
  - At most 150 words. Thank them, state the decision clearly, give 2–3 specific, job-related areas to strengthen, and one concrete next step (taken from the improvement-plan action types, e.g. "a project showing X with a measured result").
  - Each area must map to a selected reason (code check; drop or redraft otherwise).
  - Never include scores, percentiles, rankings, other candidates, or anything about personal characteristics; no promises about future roles.
  - Respectful, plain tone.
- On send: application status → `not_moving_forward`; the email is logged in Activity.

## 3. Shortlist → interview loop
**Interview plan per job** (Job settings → Interview plan, editable like a typical ATS):
- Default stages: Shortlisted → Scheduling → Phone screen → Interview → Hiring manager review → Offer → Hired; plus the terminal stage Not moving forward.
- Add, rename, reorder (drag) and delete stages. Each stage has optional focus areas, pre-filled from the job's must-haves.
- Job settings also hold: recruiter name, interview duration, availability windows (free text, e.g. "Mon–Wed 10:00–14:00 ET") or a scheduling link.

**Pipeline view:** a "Ranked | Pipeline" toggle on the job page. Pipeline is a board with one column per stage; cards can be moved between stages; moving a card logs it in Activity.

**Start interview loop** (agent, human-approved):
- Select shortlisted candidates (bulk) → **Start interview loop**.
- For each, a scheduling email is drafted (model): role and company, next stage and duration, the availability windows or scheduling link, and one personal line referencing their most relevant work (cited evidence ID, code-checked). At most 120 words.
- The recruiter reviews all drafts in one screen (edit any, approve all or one by one) → Send → each candidate moves to **Scheduling**; emails are logged.
- Later stages can draft a stage email on demand with the same review step.
- Out of scope: calendar integration, reading replies.

## 4. Sending email
- `EMAIL_PROVIDER=outbox|smtp`, default `outbox`: emails are stored and shown on an **Outbox** page; nothing leaves the machine.
- `smtp`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM` in `.env`. (For Gmail this needs an app password.)
- **Demo redirect:** `DEMO_EMAIL_TO=you@example.com`. While set, every outgoing email goes to this address regardless of the candidate; the subject is prefixed `[RightStaff demo → <candidate name>]`, and the intended recipient is shown at the top of the body. Sending to any other address is refused while it is set. Test this.
- Store every email: intended recipient, actual recipient, subject, body, model or template, status, sent_at, application ID.
- Email drafting uses the configured model role (`EMAIL_MODEL`); if unavailable or over quota, use a code template built from the same reasons and label it "Template draft".

## 5. Candidate side: compare with the median
On the candidate Score page, for the final score and each dimension:
- your score, the **median for this role**, and the percentile ("Median for Data Scientist: 68 · you're higher than 72% of N other candidates");
- a small 0–100 bar with a marker for you and a tick for the median.
- Same pool rules as phase 12 (same baseline, real-judge runs only, minimum pool, demo-pool badge).
- A dimension below the median shows "Below the median for this role — see what higher scorers have", linking to the matching gap on the Plan page.
- Do **not** show a ratio (score ÷ median).

## 6. Tests and report
- Tests: section 0 fixes; demo redirect and refusal; nothing sends without the Send action; email grounding check (every area maps to a selected reason, no scores or other candidates in text); stage moves logged; median and percentile values.
- Screenshots at 1440×900: each recruiter tab, the Not-moving-forward drawer, the interview-loop review screen, the Pipeline board, the Outbox, and the candidate Score page with medians.
- Report model calls used per role.
