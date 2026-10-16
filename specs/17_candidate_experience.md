# Spec 17 — Candidate experience: onboarding, Home, job detail, my applications

Status: approved direction (Feroz, 2026-10-16). Items marked **[proposal]** are Claude (chat) suggestions Feroz accepted in principle; mark anything you add beyond this spec as "my interpretation; please confirm".

## 0. Why (research summary)
Candidates' top pains in 2026:
1. Silence after applying.
2. Ghost jobs.
3. The volume trap (hundreds of applications, no interviews).
4. Not knowing why.
5. Re-typing the same answers into forms.
6. Missing salary.

Auto-apply tools (Tsenta, Jobright, Simplify) speed up the volume game. RightStaff owns both sides, so it can offer:
- **closure** (every application gets an answer);
- **real jobs** posted by recruiters, with activity signals;
- **honest fit before applying**;
- **proof-building advice, not resume rewriting**;
- **fill-once application details**.

Every screen below should express one of these.

## 1. Product rules (hard)
- Scoring formulas, weights and `logic_version` v0.1 are unchanged. The archetype regression stays identical.
- **Two different scores. Never mix them in the UI:**
  - **Baseline score:** EEAT against a role baseline (AI Engineer, Data Scientist, Product Manager). It tells a candidate where they stand on average for that role. This is the real, existing score.
  - **JobMatch:** a match against a specific recruiter job. **Dummy for now** (§6). Always shown with a "Preview" badge and the tooltip "Sample value. JobMatch scoring is coming soon."
- Recruiter-side job scoring on Apply stays exactly as built. The candidate UI must not show the recruiter's job score, rank, percentile within the job, or notes.
- No new model roles. Onboarding uses the existing extraction, judging and plan calls. Home, job detail and my applications make **no model calls**.
- Tests offline (FakeJudge/fixtures). DEMO_EMAIL_TO redirect enforced. HITL on all emails unchanged.

## 2. "Current candidate" (demo, no auth)
- Store the current candidate ID client-side (cookie or localStorage, wrapped in try/catch).
- No current candidate → first-run onboarding.
- Header: "Demo: viewing as <name> · Switch profile". Switch opens a small dialog listing saved candidates plus "New profile".
- Move today's "Score a profile against a role" page (target role tiles, new/saved candidates, apply list) to **Admin → Demo tools**. Candidates no longer see it.
- Candidate navigation: **Home · Jobs · My applications · My scores · Profile**. Keep "What the market asks for" reachable from My scores.

## 3. Onboarding (first run)
A stepper at the top with 5 steps, every step resumable. "Save and exit" is always available, and progress persists in the DB.

| # | Step | Content | Required |
|---|---|---|---|
| 1 | Add your proof | Resume (required). LinkedIn PDF, GitHub username, portfolio URL, certifications, references, demo video links (optional). Each optional source shows a one-line "why": e.g. LinkedIn → "Confirms your resume (Trust)"; GitHub → "Shows real work (Expertise, Trust)"; reference → "A manager reference is the strongest proof (Trust)". | resume |
| 2 | Target roles | Shown **while extraction runs** (progress bar with plain-language stages, no model names). Pick 1–3 of the available role baselines. Mark one as **primary** (default: the first picked) with a single control: a "Primary" pill on the primary card and a "Make primary" link on the other selected cards (no separate radio row). | ≥1 role |
| 3 | Review | Existing review screen (extracted roles, projects, claims, Add back, edits). "Confirm profile" ends this step. | confirm |
| 4 | Your baseline scores | Scoring runs for every selected role. Then results (§3.1). Recommendations are saved. | — |
| 5 | Finish your profile (optional) | Application details (§3.2). Buttons "Save" and "Skip for now". | no |

The end screen says: "You're set up." Primary button **Start applying** → Home. Secondary: "See my full plan".

### 3.1 Results screen
- One card per selected role, primary first. Each card shows:
  - baseline score;
  - percentile or the empty state "Percentile not available yet: fewer than 20 other candidates have been scored for <role>";
  - the 4 dimension bars;
  - the **top 3 actions** from the plan with "up to N points" each.
- Plan generation is a **[proposal]**: generate the plan for the primary role during onboarding only. For other roles, generate on first open of that role's plan (show "Preparing your plan…"). Record calls per role in the run as today.
- Plain language. Show "EEAT" once, with a tooltip explaining Experience, Expertise, Authority, Trust. Never show raw subcategory keys.
- Thin profiles (score < 10; open question in docs/open_questions.md): show the score as-is, with a reassuring line: "Most of your score is missing evidence, not weak evidence. The actions below add the most." Don't change scoring.

### 3.2 Application details ("fill once")
Stored in a new table (e.g. `candidate_application_details`, one row per candidate, JSON fields OK). Fields:
- **Work authorization:** country; authorized to work (yes/no); needs sponsorship now / in future (yes/no/prefer not to say).
- **Frequent questions:** earliest start date; notice period; salary expectation (min, currency); willing to relocate; remote/hybrid/onsite preference; preferred locations; links (personal site, etc.).
- **Voluntary self-identification (EEO):** gender, race/ethnicity, veteran status, disability status. Every field defaults to "Decline to answer". Show a short notice that answering is voluntary and never affects scores.
- Hard rules:
  - EEO fields are **never** sent to any model;
  - never shown to recruiters;
  - never used in scoring, pools, percentiles, peer signals or ranking.
  - Add tests for all of these.
- Editable later at Profile → Application details. Home shows a "Finish your profile" card until it's saved or dismissed.

## 4. Home
Layout follows the recruiter side: **ranked list on the left, job detail on the right** (desktop). On narrow screens, the list opens detail as a full page. No card grid.

```
┌───────────────────────────────────────────────────────────────────┐
│ AI Engineer baseline 72 · Top 30%  │ Next best action: Build a     │
│ [switch role ▾]  View plan →       │ RAG project with evaluation   │
│                                    │ (up to +6 points)  [Start]    │
├──────────────────────────┬────────────────────────────────────────┤
│ Jobs for you   [filters] │ JOB DETAIL (§5)                        │
│ ── Strong match (3) ──   │                                        │
│ ▸ AI Engineer            │                                        │
│   Quarry Labs · Remote   │                                        │
│   $120–140k · 3d ago     │                                        │
│   Recruiter active today │                                        │
│   JobMatch 84 Preview    │                                        │
│   Must-haves 3/4 Preview │                                        │
│ ── Close: one step away ─│                                        │
│ ── Stretch ──            │                                        │
│ ▸ Other jobs (collapsed) │                                        │
└──────────────────────────┴────────────────────────────────────────┘
```

- **Top strip:** baseline score for the primary role (role switcher), percentile or empty state, and **one** next best action (highest-points plan action not yet marked done). Action buttons: "Start" (opens the plan item) and "Mark done".
- **Jobs for you:** open recruiter jobs only (seeded + recruiter-created). No market postings.
  - Groups by dummy JobMatch: **Strong match** ≥ 75, **Close** 55–74, **Stretch** < 55. Thresholds go in config.
  - Within a group, sort by JobMatch, then by newest.
  - Jobs whose role matches a target role come first; others go under "Other jobs" (collapsed).
- **Job row:**
  - title, company, location/work mode;
  - salary range or a muted "Salary not listed";
  - posted age;
  - recruiter activity (§4.1);
  - JobMatch (Preview);
  - must-haves met "n/m" (Preview, from the dummy);
  - Save and Not interested.
  - Not interested asks for an optional reason (location, salary, role, seniority, other). It hides the job and is stored. It doesn't change any score.
- **Filters:** role, location/remote, salary listed, saved. Pre-filled from application details when present.
- **Empty states:** no jobs for your roles; all jobs hidden; profile not confirmed (explain and link to Review).
- **"Finish your profile"** card when §3.2 was skipped.

### 4.1 Recruiter activity signals (anti-ghost-job)
- From real data where it exists: the last recruiter action on the job (view of an applicant panel, stage change, email approved). Show "Recruiter active today / N days ago". Add a lightweight event for "applicant panel opened" if none exists.
- Seeded demo jobs without events get seeded timestamps. Mark them in the DB as demo data.
- Show "Reviewing applications" when any application on the job moved stage in the last 14 days (config).
- Jobs with no recruiter activity for 30 days (config) show a muted "Low recent activity".

## 5. Job detail
1. **Header:** title, company, location, salary, posted age, recruiter activity, Save. Primary button **Apply**, or the status chip if already applied.
2. **JobMatch block** with a **Preview** badge:
   - the number;
   - one sentence: "Sample value. JobMatch compares your evidence to this job's requirements and is coming soon";
   - must-haves n/m (Preview).
   - Directly below: "Your baseline score for <role>: 72". This makes clear these are two different scores.
3. **Requirements:** the job's requirements (must-have tag, required/preferred).
   - **[proposal]** For each requirement, show "Your closest evidence" **only** when the requirement name or an alias exactly matches a criterion in the candidate's baseline run for the same role. Reuse that saved judgment (label and evidence item) and caption it "From your <role> baseline score".
   - Otherwise show "Checked when JobMatch launches".
   - Code only, no model calls.
4. **What would raise your match:** up to 3 plan actions from the same-role baseline plan whose `covers_criteria` intersect this job's requirement names or aliases. If none, show the top plan action.
5. **About the job:** the posting text (Job description fields from spec 14).
6. **Apply flow:**
   - a confirm sheet titled "What the recruiter will see": profile snapshot (sources, roles, top evidence). The recruiter sees their own job scoring, not the candidate's baseline score or the JobMatch preview;
   - checkbox "My profile is up to date";
   - **Apply** runs the existing apply logic unchanged.
   - Blocked with an explanation if the profile isn't confirmed.

## 6. Dummy JobMatch (code, deterministic)
- `jobmatch_preview(candidate_id, job_id)`:
  - if the candidate has a baseline run for the job's role: base = that baseline score, offset = stable hash(candidate_id, job_id) mapped to [−12, +8];
  - else base = 55, offset mapped to [−15, +15];
  - clamp to [5, 98]; round to an integer.
- Must-haves preview: n = round(m × JobMatch/100), clamped to [0, m].
- Stable across reloads and restarts. One function, one config flag `JOBMATCH_PREVIEW=1`, so it can later be replaced by real math.
- Never stored as a score row. Never used in recruiter views, pools or percentiles.

## 7. My applications
List with status chips and a simple timeline per application. Candidate-facing statuses map from existing workflow/board stages:

| Candidate sees | From |
|---|---|
| Applied | application created |
| Viewed by recruiter | first "applicant panel opened" event (§4.1) |
| In review / Shortlisted | shortlist or screening stages |
| Interview | any scheduling/interview stage ("Round N" if available) |
| Offer | offer stage |
| Not moving forward | rejection; show the reasons from the sent email in plain text, each with a link to the matching plan gap |
| Hired / Declined | final |

- Never show scores, ranks, other candidates, recruiter notes, scorecards or internal tasks.
- **Closure principle [proposal, accepted]:** an application with no stage change for `CLOSURE_DAYS` (14, config) creates a recruiter **Tasks** item: "<Name> has waited 14 days for an answer". It never sends anything by itself. The candidate sees "Waiting for the recruiter. RightStaff reminds recruiters to answer every application."

## 8. My scores and Profile
- **My scores:** tabs per target role, each with the existing Score and Plan pages. "Add a role" (runs scoring for the new role; job-independent judgments are reused) and "Remove role". The primary role can be changed here.
- **Profile:** sources (re-upload, add LinkedIn/GitHub, etc.), Review again, Application details.

## 9. Seed demo jobs
- Add about **9 fictional jobs** (3 per role) in a seed file, with their job baselines written directly from the role baselines plus small differences (no model calls). Each job has: company, location/work mode, salary range (2 jobs without salary), posted dates spread over 1–40 days, seeded recruiter activity.
- Keep the 3 existing demo jobs and Brightwater.
- Mark all of them as demo data. They show in the candidate Home and in the recruiter Jobs page.

## 10. UX principles (acceptance)
- One primary action per screen. The stepper always shows where you are and what's left.
- Long waits show progress in plain words and give something useful to do.
- Plain language. No internal keys, model names or "judgment" wording on candidate screens.
- Empty, loading and error states for every list.
- Keyboard accessible; visible focus; labels on all inputs; contrast AA.
- Works at 1440×900 and at 390 px width without horizontal scroll.
- Light theme, existing shadcn/ui components, consistent with the recruiter side.

## 11. Phases (stop and report after each)
- **Phase A:** §2 current candidate + Demo tools move, §3 onboarding (stepper, roles during extraction, results, application details), §8 My scores/Profile basics.
- **Phase B:** §9 seed jobs, §6 dummy JobMatch, §4 Home, §5 job detail, §4.1 activity signals.
- **Phase C:** §7 My applications + closure task, polish against §10, browser e2e for the full path (new profile → onboarding → Start applying → open job → apply → status visible).

## 12. Tests (offline)
- Onboarding:
  - resumable at each step;
  - roles chosen during extraction are used;
  - primary plan only during onboarding; other roles' plans lazy;
  - job-independent judgments reused when adding a role.
- EEO and application details: never in model inputs, never in recruiter API responses, never in scoring/pools.
- JobMatch preview: deterministic; clamped; not stored as a score; absent from recruiter responses.
- Home grouping, sorting, target-role-first, Not interested hiding.
- Evidence matching on job detail: exact name/alias only; no model calls.
- Candidate status mapping, including the rejection reasons. No scores or ranks leak into candidate API responses.
- Closure task created after CLOSURE_DAYS; no send.
- Regression snapshot identical; existing tests pass.

## 13. Report format
For each phase:
- what was built;
- tests (counts);
- screenshots (each onboarding step, results, Home desktop + 390 px, job detail, apply sheet, My applications);
- model calls per role (expected: only existing onboarding calls);
- decisions marked "my interpretation; please confirm";
- what you couldn't do.
