# 01 — Requirements

## Goal
Score one candidate against one baseline role profile with the EEAT framework, show the scores with the evidence behind them, and explain the highest-value gaps. Purpose: check that the scoring logic behaves correctly. Local mock only.

## In scope
- Baseline profiles: create, read, update, delete (3 seeded: AI Engineer, Data Scientist, Product Manager; early career).
- Onboarding: upload resume, optional LinkedIn PDF export, GitHub username/URL, portfolio URL, certifications, references, a few form fields.
- Extraction to structured data, shown to the user for review and edit before scoring.
- 19 subcategory scores (Integrity deferred), 4 dimension scores, 1 final score.
- Weights adjustable in a sidebar (defaults from the weights table in `04`).
- Gaps and improvement plan: ranked gaps (code), and 3–5 concrete actions that build new evidence (see `05` §C).
- Everything stored in SQLite, including every model judgment.

## Out of scope
- Integrity checks, recruiter views, real job postings, multi-user auth, deployment, ESCO/Lightcast integration (baselines are hand-maintained mocks).

## User flow
1. **Select a baseline** (or create/edit one on the Baselines page).
2. **Onboarding**
   - Upload resume (PDF or DOCX). Required.
   - Optional: LinkedIn profile PDF (LinkedIn's own "Save to PDF" export), GitHub username or URL, portfolio URL.
   - Optional form fields: certifications (name, issuer, date, verify URL), references (name, relationship, contact, statement), target role.
3. **Extraction runs.** A prefilled, editable profile is shown: roles, projects, skills/tools, outcomes, certifications, references.
4. **User reviews and confirms.** Edits are saved; edited items are marked `source = user_edit`.
5. **Scoring runs** (the graph in `02`).
6. **Results page:** final score, dimension scores, subcategory scores with the quote that drove each one, N/A and missing markers.
7. **Gaps and improvement plan:** top gaps ranked by potential gain (code), with an improvement plan of actions that build new evidence.
8. User can change weights in the sidebar; scores and gap ranking update instantly with no model calls.
9. User can re-run against a different baseline without re-extracting.

## LinkedIn
Do **not** scrape linkedin.com (blocked and against LinkedIn's terms). Accept only the user-uploaded "Save to PDF" export and parse it like a resume.

## GitHub
Use the public REST API. For each non-archived repo: name, description, primary language, languages, fork flag, last push date, and the number of commits authored by the user. Repos that are forks with zero commits by the user are **not** counted as the user's work. The GitHub profile README repo (named exactly like the username) and the personal-site repo (`<username>.github.io`) are **skipped**: they describe the person, not a project (the site counts through the portfolio URL instead).

## Portfolio
Fetch the URL, strip HTML to text, extract with the LLM. If the page returns little text (JavaScript-rendered), show a message asking the user to paste the content or add projects manually.

## Edge cases (must be handled)
| Case | Behaviour |
|---|---|
| No resume uploaded | Block scoring. Message: "Upload a resume to get your score." |
| Resume unreadable (scanned image, empty text) | Tell the user; offer manual entry of roles/projects. Do not run OCR. |
| Extraction returns no roles and no projects | Block scoring; ask the user to add at least one role or project in the review step. |
| No GitHub / portfolio / references / certifications | Score those subcategories as **missing (0)**, not N/A; the improvement plan suggests adding them. |
| Only one role | Progression = **N/A**. |
| Role with no end date and not marked current | Use start date as end date; mark it for review in the review step. |
| Year-only dates | Start → January, end → December. |
| Future dates | Flag in review; exclude from date math until fixed. |
| Overlapping roles | Count overlapping months once in Depth. |
| GitHub user not found / rate limited | Show the error; continue without GitHub. |
| Portfolio fetch fails | Show the error; continue without portfolio. |
| Baseline list empty for a criteria type (e.g. no preferred tools) | That part is N/A (see `04`). |
| Jev unavailable or low confidence | Fall back to Claude for that judgment (see `05`). |
| Any model call fails after retries | Mark that subcategory `error`, show it, exclude from averages, do not crash the run. |

## Non-functional
- Same inputs, same weights → same scores (temperature 0; cached judgments).
- A full scoring run for one resume should finish in about a minute on a normal connection (target, not a hard limit).
- Every score is explainable: the UI can show the judgment(s) and quote(s) behind it.
