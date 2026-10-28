# Architecture

A FastAPI backend and a Next.js web app over one SQLite database. Scoring is a LangGraph pipeline: models only give
labels, and Python applies the formulas in `specs/04_scoring_rules.md`. Tables are described in
[schema.md](schema.md).

## Layers (backend)

Dependencies point down the table, with three exceptions: `services/jobs.py` and `services/demo_jobs.py` set up
interview stages through `workflows/engine.py`; `scoring/benchmarks.py` reads a run's saved scores through
`graph/pipeline.py`; the LinkedIn parser's labeller (`extraction/parse/labeller.py`) asks Jev through `judges/`.

| Folder | Layer | What lives there |
|---|---|---|
| `api/` | HTTP | `app.py` builds the app: middleware, error handlers, and the routers in a fixed order. `state.py` builds the dependencies (judge, models, database path) and keeps the per-process state; `context.py` holds what every router shares (a connection per request, 404s, ValueError → 422, background scoring, early start); `schemas.py` the request and response models; `routers/` one module per area (candidates, candidate, scoring, jobs, recruiter, market, workflows, hiring, emails, admin, baselines, tasks, ops). A router only parses the request, calls a service and shapes the response. |
| `workflows/` | Hiring workflow | `engine.py` (interview plan, stages, emails, pipeline moves), the hiring and Not moving forward graphs (`hiring_flow.py`, `rejection_flow.py`), scheduling, reply reading and the mailer. Nothing is sent without the recruiter's Send. |
| `services/` | Request-level logic | Candidate experience, Home jobs and My applications, Review edits, recruiter views (job, applicants, panel, compare), creating and publishing jobs, the weight assistant, wait screens, the background task store, demo jobs. |
| `graph/` | Scoring pipeline | The LangGraph pipeline (`pipeline.py`), its context (`context.py`), one node module per dimension (`nodes/`) and the read-only computation view (`audit.py`). |
| `scoring/` | Formulas and the plan | Subcategory formulas, aggregation with weights and gap ranking as pure functions; benchmarks from saved runs; the improvement plan (one planner call, then validated and priced in code). |
| `market/` | Market baselines | Fetching postings, levels, extraction, name normalization, aggregation into a baseline version. |
| `judges/` | Labels | The judge interface, Jev, the LLM judge (Claude, Gemini or Groq), the fake judge for tests, the replay judge (the archive), the question catalogue and the judgment service (saves and reuses every judgment). |
| `extraction/` | Reading documents | LLM extraction to structured records, validation (every item needs its quote), merging sources, dedupe, the LinkedIn PDF parser (`parse/`), the prompt-injection screen, saving the profile. |
| `ingest/` | Inputs | PDF and DOCX to text (with hidden-text removal), GitHub, portfolio pages, the HTTP cache. |
| `providers/` | Model access | The structured-output clients (`llm.py`), routes with failover and limits (`routing.py`), spending caps and concurrency limits. |
| `core/` | Foundations | Settings and constants (`config.py`), the baseline models (`models.py`), text and link helpers, timing, and the database (`db/`: schema, migrations, connection, baselines, runs). |

Configuration that people review lives in `config/` (aliases, skill groups, market sources and synonyms, wait tips,
the weight-assistant screen), seed data in `seed/`, prompts in `prompts/`. Operational scripts are in `scripts/`
(reseed, the judgment archive, the demo seeds, rendering and scoring the test profiles, purging a person's data).

## Web app (`frontend/`)

| Folder | What lives there |
|---|---|
| `app/` | The page routes (Next.js app router): candidate (`/`, `/onboarding`, `/scores`, `/profile`, `/jobs`, `/applications`), recruiter (`/recruiter/...`), admin (`/admin/...`), runs (`/runs/[id]`). |
| `components/ui/` | Shared primitives (shadcn on Base UI). |
| `components/shared/` | The app frame: header, main area, demo-mode banner, progress panel, external links. |
| `components/features/` | One folder per area: baselines, candidate, jobs, onboarding, recruiter, review, score. |
| `hooks/`, `lib/` | Task polling and reduced motion; the API client (`lib/api.ts`), link handling, the current candidate. |
| `e2e/` | Playwright browser tests (run against the API on fakes). |

## Request flow (scoring a candidate)

1. `POST /candidates/{id}/onboard` (api/routers/candidates.py) saves the uploads and starts a background task.
2. The pipeline (`graph/pipeline.py`) reads each document (`ingest/`, `extraction/`) and saves the profile. Review
   opens once the resume is read, and the run pauses for the candidate's confirmation (a LangGraph interrupt).
3. On Confirm, the dimension nodes ask the judges for labels (`judges/service.py` saves each one), and
   `scoring/formulas.py` turns them into subcategory scores, which are saved to `scores`.
4. Dimension and final scores are computed from the saved scores and the weights whenever they are shown
   (`scoring/aggregate.py`), so changing weights never calls a model.

## Tests

`tests/` (pytest, offline: fake judge, scripted extraction, recorded answers), `frontend/e2e/` (Playwright), and
`tests/test_regression.py`, which pins the archetype scores. Live-model tests run only with `RIGHTSTAFF_LIVE=1`.
