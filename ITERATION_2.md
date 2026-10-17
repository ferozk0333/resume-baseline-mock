# RightStaff mock — Iteration 2

Read this file first, then the spec for the phase you are on. The existing `CLAUDE.md` and `specs/01`–`07` still apply unless a spec below says otherwise.

## Goal
Turn the working prototype into something that looks and behaves like a real product:
1. **Faster** scoring (phase 9).
2. A **professional web app** (FastAPI + Next.js) replacing Streamlit (phase 10).
3. A **recruiter view** alongside the candidate view (phase 11).
4. **Benchmarks**: percentiles and "what higher scorers have" (phase 12).

## Rules for this iteration
1. **Scoring logic does not change.** Formulas, weights, status rules and `logic_version` stay as they are. Every phase must keep the FakeJudge archetype scores identical to before the phase (add a regression test that records them at the start of phase 9).
2. Before starting, tag the current repo state `v0.1-mock` (if not already) and work on a branch `iteration-2`. Commit at the end of each phase.
3. One phase at a time. At the end of each phase, report: what was built, test results, any decision you had to make (as "my interpretation; please confirm"), and anything you could not do.
4. All tests run without network (FakeJudge, fixtures). Live-model checks are separate and opt-in, as before.
5. Anything not defined in these specs is a proposal: say so in the report instead of deciding silently.
6. Never commit `.env`, the SQLite database, uploads or rendered fixtures.

## Phases
| Phase | Spec | Outcome | Done when |
|---|---|---|---|
| 9 | `specs/09_speed.md` | Confirm → score in about 30 s | Timing report before/after; scores unchanged |
| 10 | `specs/10_api_frontend.md` | FastAPI backend + Next.js candidate app (light theme) | Candidate flow works end to end in the browser; Streamlit retired |
| 11 | `specs/11_recruiter.md` | Recruiter view with ranked applicants and profile panel | 16 seeded applicants across 3 jobs; list, detail, compare work |
| 12 | `specs/12_benchmarks.md` | Percentiles and peer signals | Shown only when the pool is large enough; no profile text leaks |

Phase 11 needs the test profiles bundle (`rightstaff-test-profiles/`, provided separately) for seeding.
