# Cleanup round: strings removed or shortened

For review (cleanup item 3). One rule on every screen: remove text that explains how the system works, implementation details (file paths, config names, "cached", "in code", model or provider names, poll intervals, internal source values), restated obvious instructions and email addresses. Kept: labels, real data, plain empty and error states, the JobMatch "Preview" badge, "Shown to recruiters, not scored", the EEO title, the honest credential wording and tooltips that define a score (see docs/open_questions.md 47).

119 changes. Screenshots: docs/screenshots/cleanup/.


## Candidate screens

| Screen | Before | After |
|---|---|---|
| All candidate screens · Demo banner | Demo mode: uploads are not read. Reading, scoring and the plan are scripted, so every profile gets the same preset scores. For real processing, stop the demo and run make live. | Demo mode: uploads are not read, and every profile gets the same preset scores. |
| Candidate · Application details (Frequent questions) | Used to fill applications and job filters for you. | (removed) |
| Candidate · Application details (Work authorization) | Answer once; you won't retype it for every application. | (removed) |
| Candidate · Home (Finish your profile) | Answer the questions most applications ask, once: work authorization, start date, salary. | Work authorization, start date, salary. |
| Candidate · Home (next best action) | Open your plan for <role> to see what adds the most. | (removed; the "Open the plan" button stays) |
| Candidate · Home (setup card) | Step N of 5: <step>. Your progress is saved. | Step N of 5: <step> |
| Candidate · Job detail (How to raise it) | None of your plan's actions name this job's requirements; your top action: | Your top action: |
| Candidate · Job detail (How to raise it) | Your plan for <role> is prepared when you open it. Open the plan | Open the plan |
| Candidate · Job detail (JobMatch) | Sample value. JobMatch compares your evidence to this job's requirements and is coming soon. | (removed; the "Preview" badge and its tooltip stay) |
| Candidate · Jobs (Not interested dialog) | We'll hide it. A reason is optional and helps us show better jobs. It never changes a score. | A reason is optional. |
| Candidate · Jobs (hidden jobs dialog) | Jobs you marked Not interested. Hiding a job never changes a score. | Jobs you marked Not interested. |
| Candidate · My applications | Every application gets an answer. Here is where each one stands. | (removed) |
| Candidate · My scores | Your baseline score per role, from EEAT (tooltip). Where you stand on average for the role, not for one job. | Your baseline score per role (EEAT tooltip kept). |
| Candidate · My scores (Scored against switch) | (no data shown for the Market option) | Market: Based on N postings from M companies |
| Candidate · My scores (re-score dialog) | Your profile is scored again against <baseline>. The parts of the check that depend on the baseline are redone (already saved from an earlier score, so it's quick); the rest is reused. Percentiles compare you only with candidates scored on the same baseline version. | Your profile is scored again against <baseline>. |
| Candidate · My scores (remove a role dialog) | It is no longer shown on Home or here. Your saved score stays, and adding the role back reuses it. | Your saved score stays. |
| Candidate · Onboarding (done) | Next: apply to jobs. Your baseline scores and plan stay under My scores. | Next: apply to jobs. |
| Candidate · Onboarding (header) | Show your proof once. See where you stand for the roles you want, and how to get stronger. | (removed) |
| Candidate · Onboarding 1 (Add your proof) | Your resume is enough to start. Every extra source below makes your scores more trustworthy. | (removed; the Documents card says "Only the resume is required.") |
| Candidate · Onboarding 1 (Project demo videos) | Short walkthroughs of your projects: show the demo, explain the architecture and the design choices you made. 1–2 minutes each is ideal. Up to 3 YouTube or Loom links. | Up to 3 YouTube or Loom links, 1–2 minutes each. |
| Candidate · Onboarding 1 (References) | A manager or supervisor confirming relevant work counts most. | (removed; the line "A manager reference is the strongest proof (Trust)" stays) |
| Candidate · Onboarding 2 (Target roles) | Pick 1 to 3. We score your proof against each role. Your primary role leads your Home page. | Pick 1 to 3. |
| Candidate · Onboarding 4 (Baseline scores) | Each baseline score uses EEAT (tooltip) to compare your proof with what the role usually asks for, from 0 to 100. It shows where you stand on average for the role, not for one specific job. | Scores from 0 to 100 (EEAT tooltip kept). |
| Candidate · Onboarding 5 (Finish your profile) | Optional. Answer the questions most applications ask, once. You can change them any time under Profile. | Optional. |
| Candidate · Profile (Your proof) | What your scores are built from: N roles, N projects and N skills and tools. | N roles, N projects, N skills and tools |
| Candidate · Profile (Your proof) | Both re-score your target roles when you confirm. | (removed) |
| Candidate · Review (More proof) | Publications, talks, awards and more. Add what applies to you. | (removed) |
| Candidate · Review (More proof, empty) | Nothing here yet. Add what applies to you; skip the rest. | Nothing here yet. |
| Candidate · Review (Projects) | Projects, repositories and work samples. Publications, talks and the like are under More proof. | (removed) |
| Candidate · Review (Roles) | Your work experience. Edit anything that was read wrongly. | (removed) |
| Candidate · Review (Skills and tools) | Named in your documents, grouped to make them easy to check. | (removed) |
| Candidate · Review (Source text) | Each document as we read it, after clean-up. Quotes are checked against this text. | (removed) |
| Candidate · Review (project type, GitHub) | GitHub repository (from GitHub) | GitHub repository |
| Candidate · Score card (plan) | Your plan for this role is prepared when you open it. Your biggest gaps so far: | Your biggest gaps so far: |
| Candidate · Switch profile dialog | The demo has no sign-in. Pick whose candidate view to see. | (removed) |
| Candidate · What the market asks for | From recent entry-level job postings: how many companies list each skill or tool, and whether it is usually required. | (removed; each version shows "Based on N postings from M companies") |
| Candidate · What the market asks for | N postings from M companies · <dates> · version N + badge "Test version – insufficient data" | Based on N postings from M companies · <dates> · version N |
| Candidate/Admin · Weights drawer | Change how much each part counts. The score updates instantly; no model is asked again. | Change how much each part counts. |
| Score page (Check everything again dialog) | Saved answers for this role are cleared and every part of your profile is checked again. This takes a few minutes. | This takes a few minutes. |
| Score page (demo pool badge tooltip) | demo pool (tooltip: Comparisons use a small demo pool (minimum N); real use needs at least 20.) | demo pool |

## Recruiter screens

| Screen | Before | After |
|---|---|---|
| Recruiter · Advance drawer | Invite the candidate to a stage. Sending moves them there. | (removed) |
| Recruiter · Create job (Copy an existing job) | Any job or a role template. Opens prefilled; change what differs. | (removed) |
| Recruiter · Create job (Copy an existing job, Market templates) | <Role> (Market vN) · Test version – insufficient data | <Role> (Market vN) · Based on N postings from M companies |
| Recruiter · Create job (Start from scratch) | An empty form. Saved as a draft as you type. | (removed) |
| Recruiter · Create job (Upload or paste) | PDF, DOCX or text. Fields are filled for you and marked “From JD”; everything stays editable. | (removed; the drop zone lists the file types) |
| Recruiter · Create job (reading failed notice) | Reading job descriptions needs a model (JOB_EXTRACT_MODEL). Fill in the form; the job description is on the right to copy from. | The job description could not be read. Fill in the form; the job description is on the right to copy from. |
| Recruiter · Create job (reading failed notice) | The job description could not be read automatically (<provider error text>). Fill in the form; the job description is on the right to copy from. | The job description could not be read. Fill in the form; the job description is on the right to copy from. (the error goes to the log only) |
| Recruiter · Create job (reading) | One model call, usually 10–30 seconds. | Usually 10–30 seconds. |
| Recruiter · Email editor | Template used because the model draft was unavailable / failed the checks twice. | Template used. |
| Recruiter · Email send notice (drawers, Outbox) | Outbox mode: sent emails are stored and shown on the Outbox page; nothing leaves this machine. / SMTP: Send delivers the email. + Demo redirect on: every email goes to <email address> only. | Emails are kept in the Outbox; nothing is delivered. / Send delivers the email. + Demo: every email goes to the demo inbox only. |
| Recruiter · Interview plan (Scheduling) | Used in interview-loop emails. Calendar integration is out of scope. | (removed) |
| Recruiter · Interview plan (Stages) | Stages the Pipeline board shows. Drag to reorder; focus areas are what each stage checks. | (removed) |
| Recruiter · Job (no applicants) | No applicants yet. Candidates can apply from the candidate side once the job is open. | No applicants yet. |
| Recruiter · Job form (Availability) | Interview slots are proposed from these windows. | (removed) |
| Recruiter · Job form (Interview plan) | Used by the hiring workflow; no effect on the score. | (removed) |
| Recruiter · Job form (Level) | A label for the posting. Scoring uses the minimum years: up to 2 years is entry level. | (removed) |
| Recruiter · Job form (Minimum years) | Relevant experience is measured against this (0 counts as 1 year). | (removed) |
| Recruiter · Job form (Ranking emphasis) | Changes the order of applicants only; no model calls. | (removed) |
| Recruiter · Job form (Ranking emphasis) | All presets equal the team table until the team sets values. | (removed) |
| Recruiter · Job form (Requirements) | Required items count twice as much as preferred ones in the skills and tools scores. Must-haves don't change the score; the applicant list flags who lacks them. | (removed) |
| Recruiter · Job form (Role family) | Suggested from the title. Candidates' preview match for this job starts from their baseline score for this role. Doesn't change how you rank applicants. | Suggested from the title. (only when suggested) |
| Recruiter · Job form (What good looks like) | Plain answers; each one is what a part of the score is judged against. | (removed) |
| Recruiter · Job form (What will this person own?) | Ownership is judged against this, using the candidate's most recent relevant role. | (removed) |
| Recruiter · Job form (complexity) | Each relevant role or project is graded against this. | (removed) |
| Recruiter · Job form (re-score dialog) | N applicants will be re-scored. About N model calls. Requirements or “What good looks like” changed; saved judgments that don't depend on the job are reused. | N applicants will be re-scored. |
| Recruiter · Job form (reach) | Scope of impact: full marks at this reach or above, half one level below. | (removed) |
| Recruiter · Job form (step 2 notice) | Read from the job description by <model>. Hover “From JD” to see the sentence each field came from. Check everything before publishing. | Read from the job description. (+ the list of items left out, unchanged) |
| Recruiter · Job header | <Company> · ranked with the team weights table | <Company> |
| Recruiter · Job settings (Must-haves) | Shown as “x of y met” on each applicant. Must-haves do not change the score. | (removed) |
| Recruiter · Job settings (Presets) | All presets equal the team table until the team sets values. | (removed) |
| Recruiter · Job settings (Weights) | Changing them re-ranks the applicants at once; no model is asked again. | (removed) |
| Recruiter · Jobs | Applicants are ranked by match score. Open a job to review them. | (removed) |
| Recruiter · Jobs (Drafts, empty) | No drafts. Create job saves a draft as you go. | No drafts. |
| Recruiter · Not moving forward drawer | Pick the reasons; the draft uses only these. Nothing is sent until you click Send. | (removed) |
| Recruiter · Outbox | Emails sent from the recruiter view, with their full text. | (removed) |
| Recruiter · Pipeline board | N applicants not yet in the interview plan (shortlist them in Ranked). Moving a card logs it and updates the workflow; it never sends anything. | N applicants not yet in the interview plan |
| Recruiter · Profile panel (Activity, empty) | No activity yet: stage moves, emails and decisions appear here. | No activity yet. |
| Recruiter · Profile panel (EEAT tab) | Computed in code from the roles' and projects' relevance and dates (see Relevance). | Based on the roles' and projects' relevance and dates (see Relevance). |
| Recruiter · Profile panel (LinkedIn tab) | From the uploaded LinkedIn PDF + parsed roles, "Skills: …" (awards included) and "Certifications: …" | The uploaded LinkedIn PDF in a viewer, or "No LinkedIn PDF uploaded"; the profile link stays at the top |
| Recruiter · Profile panel (Work, videos) | Walkthroughs of the demo, architecture and design choices, from the candidate. | (removed) |
| Recruiter · Profile panel (demo badge) | Demo data: parsed from structured profile | Demo data |
| Recruiter · Profile panel (not scored) | Not scored yet: see Work, Resume and LinkedIn. Scores, “why” and questions appear after judging. | Not scored yet. |
| Recruiter · Start interview loop dialog | <Not shortlisted yet…>. The interview loop starts from Shortlisted. Nothing is sent until you approve each email. | <Not shortlisted yet…>. |
| Recruiter · Tasks | Everything waiting for you, oldest first. Each opens the candidate's workflow at that step. Applications that have waited too long for an answer are listed too. Nothing is sent until you approve it. | (removed) |
| Recruiter · Tasks | Replies are read from <email address> every 3 min. Last check 10:42: N matched, N skipped. [Check inbox now] | [Check inbox now] (tooltip: "Last checked at 10:42"); the API no longer returns the address |
| Recruiter · Tasks (empty) | Nothing waiting. Start an interview loop from a job to see steps here. | Nothing waiting. |
| Recruiter · Workflow (choose step) | Nothing is sent until you approve an email. | (removed) |
| Recruiter · Workflow (feedback summary note) | Feedback model unavailable (<provider error text>): code summary. | Check this summary. (the error goes to the log only) |
| Recruiter · Workflow (feedback summary note) | Feedback model not configured: code summary. | (removed) |
| Recruiter · Workflow (feedback summary) | N scorecards · summarized by <model name> / code summary | N scorecards |
| Recruiter · Workflow (feedback summary) | The summary makes no recommendation. The decision is yours. | (removed) |
| Recruiter · Workflow (feedback) | <Stage>: one scorecard per interviewer, each focus area rated 1–4. | <Stage>: rate each focus area 1–4. |
| Recruiter · Workflow (reply reading note) | Reply model unavailable (<provider error text>): read by code rules. | Check this reading. (the error goes to the log only) |
| Recruiter · Workflow (reply) | Reply · <date> · imap\|simulated | Reply · <date> |
| Recruiter · Workflow (reply) | read by <model name> / read by code rules | (removed) |

## Admin screens

| Screen | Before | After |
|---|---|---|
| Admin · Baseline (delete dialog) | Past runs against it are kept and show the baseline as “(deleted)”. | Past scores are kept. |
| Admin · Baselines | The role profiles candidates are scored against. | (removed) |
| Admin · Demo tools | Pick a target role, then start with a resume or continue with a saved candidate. | (removed) |
| Admin · Demo tools | The baseline the profile is scored against. / Upload a resume on the next screen. / Review their profile again, or open a past score. | (removed) |
| Admin · Demo tools (Apply to a job) | Open jobs. Applying uses the selected candidate's confirmed profile and scores it against the job; the recruiter sees the application. Pick a saved candidate first. | Pick a saved candidate first. (only while none is picked) |
| Admin · Market baselines (Add a posting) | For companies without a public job-board API. Paste the text or upload a PDF/DOCX. | (removed; the drop zone lists the file types) |
| Admin · Market baselines (Baseline source) | The default for new <Role> scores. Candidates can switch on their Score page; jobs created by recruiters keep their own baseline. | Default for new scores. |
| Admin · Market baselines (Fetch result) | Nothing to fetch: none of the N companies has a public job board set yet (N skipped: placeholders or manual). Add the real companies in config/market_sources.yaml (source greenhouse, lever or ashby with its board id), or use Add postings. | No company has a public job board yet. Use Add postings. |
| Admin · Market baselines (Fetch result) | Read N boards: N new postings, N already known, N other titles left out, N no longer listed, N skipped. | N new postings, N already known, N no longer listed. |
| Admin · Market baselines (New names) | Names not in the reviewed synonym map. Approve a merge into an existing criterion, or reject to keep the name separate.  + badge "proposed by <model>" | (removed) |
| Admin · Market baselines (Postings table) | Columns: Company, Tier, Title, Source (greenhouse/fixture/manual), Level (code), Read (✓ / on recompute), Last seen | Columns: Company, Tier, Title, Level, Last seen |
| Admin · Market baselines (Postings) | N in the window from M companies (x entry, y mid; only entry builds the baseline for now). 15 of 15 configured companies are placeholders (config/market_sources.yaml). | N postings from M companies (x entry, y mid) |
| Admin · Market baselines (Postings) | Load test fixtures (button) | (moved to Admin → Demo tools: "Load test fixtures: <role>") |
| Admin · Market baselines (Recompute) | A new draft version: new postings are read once (cached), then aggregated in code.  [Recompute] [Recompute without a model] | [Recompute] (one button; the model is skipped when no posting needs reading) |
| Admin · Market baselines (Versions) | Every version in one list, with "Insufficient data" and "test version" badges; N postings · M companies | Active version and the latest draft; older and discarded ones under a collapsed "History (n)"; "Based on N postings from M companies" |
| Admin · Market baselines (Versions, empty) | No versions yet: add postings, then Recompute. | No versions yet. |
| Admin · Market baselines (header) | Baselines built from real job postings over the last 120 days. Nothing goes live until a person publishes it. | (removed) |
| Admin · Market baselines (header) | ☐ Allow test versions (publish with fewer than 8 companies / 20 postings) | (removed: no minimum data; a person still publishes) |
| Admin · Market baselines (switch source dialog) | Switching will re-score N candidates (about N model calls) the next time they are scored: judgments that depend on the baseline are redone. | N candidates are re-scored the next time they are scored. |
| Admin · Market version page | badges "Insufficient data", "test version"; N postings · M companies; Publish tooltip "Insufficient data: turn on “Allow test versions” to publish" | Based on N postings from M companies; Publish always available on a draft |
| Admin · Market version page | Text: <summary model name>. | (removed) |
| Admin · Screening | Hidden text removed before reading, how each document was read, and text the injection screen flagged (threshold 0.5). Admin only: nothing here changes a score or reaches a recruiter. | (removed) |
| Admin · Screening (Hidden text) | Removed before reading (white or near-background colour, tiny, off-page). | (removed; each item still shows its reason) |
| Admin · Screening (Parser log) | Columns "Jev", "LLM" | Columns "Label requests", "Model reads" (provider names removed) |
