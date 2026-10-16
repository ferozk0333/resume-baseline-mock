# 10 — API + professional frontend (candidate side)

Replace the Streamlit UI with a FastAPI backend and a Next.js frontend. The pipeline, scoring and database stay; this phase only adds an API around them and a new UI on top.

## 1. Structure
```
backend/     FastAPI app; imports the existing core/, scoring/, graph/, judges/, extraction/, ingest/
frontend/    Next.js (App Router) + TypeScript + Tailwind CSS + shadcn/ui
```
- One command starts both for local use (`make dev` or similar). Document it in the README.
- Move the Streamlit app to `legacy/` once the new UI covers everything it did, then stop maintaining it.

## 2. API
Long-running work (onboarding, scoring, plan) runs as background tasks. Progress is stored in the database and streamed to the UI with **Server-Sent Events**.

| Method | Path | Purpose |
|---|---|---|
| GET/POST/PUT/DELETE | `/baselines`, `/baselines/{id}` | List, create, edit, delete baselines; `POST /baselines/reset-seed` |
| POST | `/candidates` | Create a candidate |
| POST | `/candidates/{id}/onboard` | Multipart: resume (required), LinkedIn PDF, GitHub username, portfolio URL, demo video URLs, certifications, references → returns a task ID |
| GET | `/tasks/{id}/events` | SSE progress stream (step, message, percent, done/error) |
| GET | `/candidates/{id}/profile` | Extracted profile for review, including dropped items and warnings |
| PATCH | `/candidates/{id}/profile` | Review edits (add, edit, delete, add back a dropped item) |
| POST | `/candidates/{id}/score` | Body: baseline ID → task ID; starts the graph |
| GET | `/runs/{id}` | Final, dimension and subcategory scores, statuses, details, judgments with quotes and method |
| POST | `/runs/{id}/rescore` | Body: weights → re-aggregated scores and gaps. **No model calls.** |
| GET | `/runs/{id}/plan` | Improvement plan (or status `generating`) |
| POST | `/runs/{id}/plan/regenerate` | New plan with current weights |
| POST | `/runs/{id}/rejudge` | Clear saved judgments for this candidate and baseline, then re-score |

- Pydantic models for every request and response; OpenAPI docs at `/docs`.
- Errors return a clear message; stack traces go to the server log only.

## 3. New input: demo videos
- Candidates may add up to 3 demo links. Allowed hosts: YouTube and Loom (validate the URL; reject others with a message).
- Store in a new `media_links` table (candidate ID, URL, kind, title, optional linked project).
- Display only. **Not scored** in this iteration.

## 4. Candidate screens
- **Header:** "RightStaff", and a **Candidate / Recruiter** switch. Recruiter shows a placeholder until phase 11.
- **Home:** pick or create a candidate and a target role (baseline).
- **Onboarding (stepper):** 1 Upload → 2 Review → 3 Score.
  - Upload: the inputs above, with LinkedIn "Save to PDF" help text.
  - Review: editable cards for roles (with bullets), projects, skills and tools, certifications; `needs_review` items highlighted with the reason; source quote shown on expand; dropped items listed with "Add back"; ingest warnings at the top. "Confirm & score" disabled until there is at least one role or project.
  - Progress: live steps from SSE ("Reading resume", "Judging skills 12/16", …).
- **Score:**
  - Top: final score and the four dimension scores as cards. No "strong/weak" labels.
  - Per dimension: subcategory rows with score, status badge (scored / missing / N/A / error) and weight. Expanding a row shows the formula in plain words, the per-item breakdown, and the evidence (quote, label, method). Jev judgments show the judged text with "Jev returns no quote".
  - `missing` rows show their guidance.
  - Weights drawer (right side, closed by default): 4 dimension sliders plus per-dimension subcategory sliders, normalized percentages, "Reset to table". Changes call `/rescore` and update instantly.
  - "Re-judge" button with a confirmation dialog.
- **Plan:** ranked gaps ("up to N points"), action cards (type, title, what to do, what it must show, gaps addressed, points), quick wins, and "not actionable now". Shows a loading state while the plan is `generating`.
- **Baselines:** list and edit form, as in `06`, in the new style.

## 5. Visual style
- **Light theme** only for now. Neutral off-white page background, white cards with a subtle border and small radius, one accent colour for actions and highlights, a muted palette for status badges.
- Inter (or the system font stack), clear type scale, 8-px spacing grid, generous whitespace.
- Use shadcn/ui components (cards, tables, tabs, badges, sliders, dialog, sheet/drawer, toast). No custom component library.
- Desktop first (1280 px+), but nothing breaks at 768 px.
- Accessible: visible keyboard focus, sufficient contrast, labels on every input.

## 6. Tests
- API tests (pytest + FastAPI test client) with FakeJudge for every endpoint, including SSE completion and `/rescore` making no model calls.
- One Playwright smoke test of the candidate flow (upload → review → score → plan) using fixtures. Chromium is available locally.
- The existing 249+ tests still pass.

## 7. Report
Screenshots of each screen, the commands to run it, and anything from the Streamlit app not yet carried over.
