# Cleanup inventory (Phase 1)

Branch `refactor/cleanup` from main `8f7e659`, tagged `v0.4-pre-refactor`. Nothing has been deleted or changed yet:
this file is the only change. Every row has a proposed action; change any of them before Phase 2.

Actions: **remove** (recoverable from tag `v0.4-pre-refactor`), **keep**, **merge** (into one), **move**.
Risk: what could break if the action is wrong, and how the safety net would catch it.

## 0. Preconditions and safety net (done)

| Item | Result |
|---|---|
| Branches | main is up to date with origin. feature/audit-extraction, feature/deploy and feature/amendments (PR #2) are all in main; origin has no other branches. |
| Tag, branch | `v0.4-pre-refactor` on main (local, not pushed yet); `refactor/cleanup` created. |
| Backup | `~/rightstaff-backups/pre-refactor-2026-10-24/`: data/, uploads/, timings/, .verify/, rightstaff.db and a consistent SQLite copy of demo.db (integrity ok, schema 29). Outside the repo. |
| Python tests | 838 passed, 14 skipped. |
| Browser tests | 26/26 on the second run. One known flaky test failed once: recruiter LinkedIn tab, click on a re-rendering applicant row. Next.js 16.4 exits on alternate starts after printing its security-upgrade notice, so the safety-net runner starts the e2e servers itself and retries the web start. |
| Archetype regression | Part of the Python suite (test_regression.py): identical. |
| Golden snapshot, demo.db | 128 entries: every synthetic profile's runs (final, dimensions, subcategory status/score/details/guidance, used labels) and their applications. Read twice: identical. Kept outside the repo with the tool (`~/rightstaff-backups/safety-net/golden.py`). |
| Golden snapshot, reseed | 45 entries from two temporary databases built offline with `scripts/reseed.py` and `data/judgment-archive.db` (0 unrecorded questions): identical to each other. Ids are canonicalized, since a reseed makes new ones. |
| OpenAPI | 107 paths, 128 operations, 48 schemas. |
| Page routes | 26 (`page.tsx`/`route.ts`). Every page is linked from somewhere in the app. |

**Corrections to the brief (my interpretation; please confirm):**
- The judgment archive is `data/judgment-archive.db` (SQLite), not `data/judgment_archive.jsonl`.
- The schema is already at 29 (28 was `runs.scored_on`; 29 is `weight_suggestions`). A migration in Phase 2c would be **30**.
- The weight assistant (spec 21) is built and merged. It stays; only its live sample run waits for your go.

## 1. Personal data

Synthetic = `data_source` in archetype (7), demo_parsed (15, including Kevin Shaw and Hannah Cole) or test_variant
(1, DS4_padded): **23 synthetic candidates**, all `@example.com` or archetype markers. All 10 others are personal.
The repo refers to them as P1–P10; names, GitHub and LinkedIn are in the local key
`~/rightstaff-backups/safety-net/personal_profiles.md`, kept out of the repo.

### 1a. Personal candidates in data/demo.db

| # | Created | Sources | Linked records | Action |
|---|---|---|---|---|
| P1 | 2026-10-16 | none | the candidate row only | remove |
| P2 | 2026-10-19 | résumé, LinkedIn PDF, GitHub (owner), portfolio, LinkedIn URL | 33 documents, 934 judgments, 1 run, 2 tasks | remove |
| P3 | 2026-10-20 | résumé, LinkedIn PDF | 2 documents, 389 judgments, 2 runs, 2 gap reports | remove |
| P4 | 2026-10-20 | résumé, LinkedIn PDF, LinkedIn URL | 27 runs, 2,167 judgments, 3 applications, 1 workflow, 3 emails, 3 messages, 1 interview, 4 deleted items | remove |
| P5 | 2026-10-20 | résumé, LinkedIn PDF | 3 runs, 659 judgments, 1 application | remove |
| P6 | 2026-10-21 | résumé, LinkedIn PDF, GitHub (owner), portfolio | 33 documents, 689 judgments, no run | remove |
| P7 | 2026-10-22 | résumé, LinkedIn PDF, GitHub (owner), portfolio | 2 runs, 956 judgments | remove |
| P8 | 2026-10-22 | résumé, LinkedIn PDF, GitHub (owner), portfolio | 12 runs, 1,281 judgments, 2 applications, 1 workflow, 5 emails, 3 messages, 1 interview | remove |
| P9 | 2026-10-23 | résumé, LinkedIn PDF, GitHub (owner), portfolio | 972 judgments, no run | remove |
| P10 | 2026-10-24 | résumé, LinkedIn PDF, **a third party's GitHub** | 1 run, 271 judgments | remove |

Rows removed in total, per table: candidates 10, evidence_items 172, roles 32, projects 199, bullets 369, claims 681,
certifications 33, extra_items 17, dropped_items 93, deleted_items 4, media_links 5, judgments 8,318, runs 48,
scores 912, gap_reports 15, generated_summaries 8, tasks 36, task_events 1,029, candidate_targets 18,
candidate_onboarding 9, candidate_application_details 3, parser_runs 10, prompt_guard_flags 6, applications 6,
activity 11, emails 8, workflows 2, workflow_events 36, messages 6, interviews 2, job_events 32.
Caches: document_texts 10 (+1 orphan whose document no longer exists), http_cache 122 (90 owner GitHub, 32 third
party), prompt_guard_cache 153 (only hashes and scores, with no way to tell whose; proposed: empty it, since it
refills on the next screening). LangGraph checkpoints: 4 threads (2 hire, 2 rejection) in data/demo.graphs.db.
No orphan rows were found in any table.

Kept: the jobs you created (for example Brightwater) and their settings, which are not personal; only the personal
applicants' applications on them go. Your email stays only as `DEMO_EMAIL_TO` in `.env`.

Tool: `scripts/purge_person.py` already deletes all of the above (every table, the checkpoints, the caches, upload
folders, log lines and report files) and re-searches every text column afterwards. Phase 2a runs it on all ten,
with `--dry-run` first.

### 1b. Personal data outside the database (all gitignored, local only)

| Item | Where | Contents | Action |
|---|---|---|---|
| Uploads | `uploads/` (385 entries, 3.6 MB) | the live uploads: every file in it belongs to a personal profile or to one deleted earlier. The synthetic PDFs live in `tests/fixtures/profiles/rendered/` | remove the folder's contents |
| Default database | `rightstaff.db` (repo root, 12 MB) | the DB `make dev`/`make api` use: 7 personal profiles, no synthetic ones | remove (`make reset-db` makes a clean one) |
| Old demo copies | `data/demo.db.bak-*` (10), `data/demo-before-{13,floor,live}.db`, `data/audit-live.db`, `data/weight-live.db` (~200 MB) | earlier states of the demo DB, each with 2–7 personal profiles | remove |
| Checkpoints | `data/demo.graphs.db` | 4 personal threads | purge those threads |
| Live log | `data/live-api.log` (4,946 lines) | no names, ids or GitHub user found; logs of live runs | remove (my interpretation) |
| Judgment archive | `data/judgment-archive.db` | `check` passes: the 23 synthetic profiles only | keep |
| Timings and reports | `timings/` | phase-9 timing runs, rescore logs, `audit_merge.json` and `weight_assistant_live.md/json` naming P4, `purge_report.json` | remove the folder |
| Local prod checks | `.verify/` (17 MB) | fake-stack DBs (e2e names such as Jane Doe), a live smoke DB ("Live Smoke (synthetic)") | remove (recreated by the docs/deploy.md checks) |
| Extraction reference | `tests/extraction_reference/resume.json`, `linkedin_pdf.json` | a real résumé and LinkedIn extraction | remove, with the README and `scripts/compare_extraction.py` (§7) |
| Backup | `~/rightstaff-backups/pre-refactor-2026-10-24/` | everything above, as it was | keep until you have checked v0.4-clean, then delete (your call) |

### 1c. Committed repo files with identifiable details (tracked in git)

OCR of all 159 screenshots found one with names, and no screenshot shows your real roles, projects or schools
(checked against the 74 employer, project, school and certificate names in the personal profiles). Text files:

| File | Detail | Action |
|---|---|---|
| `docs/screenshots/phase13/candidate-demo-banner.png` | two personal profile names in the Saved candidates list | remove (or re-take on the synthetic demo) |
| `specs/21_weight_assistant.md` (lines 3, 89) | your first name | replace with "the owner", as in the earlier scrub |
| `tests/test_merge_rules.py` | your school and degree with dates, the named fellowship with its amount, a hackathon placement | replace with synthetic equivalents (same shapes: same school/degree-group logic, "&" vs "and", bracketed amount, "Recognized with …", "Top 10 finalist from 600+ teams in …") |
| `tests/test_links.py`, `frontend/e2e/links.spec.ts`, `core/links.py`, `extraction/parse/resume.py` | your publication's article URL and your Credly badge id | replace with made-up URLs of the same shape (balanced parentheses, `%28`, query and fragment) |
| `tests/test_extraction.py`, `extraction/validate.py` (docstrings) | the hackathon placement and the publication title | replace with synthetic equivalents |
| `prompts/extract_system.txt` (line 12) | the hackathon placement as the example of an award with its context | replace with an equivalent made-up example. **Risky:** a prompt change could shift live extraction slightly. Scores do not change, since judging and the replayed archive don't read this prompt. Your call. |
| `tests/test_review_edits.py` | one of your repository names | replace with a synthetic name |

Git history keeps all of these and older files. The working tree is cleaned, history is not rewritten (as asked);
§1d below describes what a history purge would take.

### 1d. What a history purge would involve (not done)

`git filter-repo` with a path list (the PNG, the reference JSONs if ever committed) and a `--replace-text` list (the
strings in 1c). Then all commit ids from the first affected commit onward change, so: force-push main, delete and
re-push every tag (v0.1-mock … v0.4-*), Anjitha re-clones (her local branches can't be merged afterwards and must be
rebased by patch), and GitHub keeps the old objects reachable through PR #1/#2 refs until GitHub Support purges them.
The `backup/iteration-2-before-scrub` branch must be deleted locally (it is the unscrubbed history).

## 2. Python code

vulture (confidence ≥ 60) gives 178 hits, almost all FastAPI route handlers and LangGraph step methods registered by
decorator, so not dead. Checked by hand. The import graph from production entry points reaches every module; nothing
is orphaned. The real findings:

| Item | Where | Evidence | Risk | Action |
|---|---|---|---|---|
| `clone` | backend/jobs.py:492 | no caller anywhere | none | remove |
| `first_url` | core/links.py:58 | no caller | none | remove |
| `same_item`, `skill_or_tool` | extraction/parse/common.py:101,115 | no caller | none | remove |
| `relevant_months_bucket` | scoring/peer_features.py:78 | no caller | none | remove |
| `pacific_day` | core/llm.py:336 | no caller; core/routing.py has the one used by routing and spend | none | remove (one copy left) |
| `new_id` | extraction/records.py:9 | copy of core/db.py `new_id` | none | merge into core/db.py's |
| `now()` | backend/scheduling.py, market/store.py | same one-liner twice; tests patch scheduling's | low | keep both: tests patch each module's own clock |
| Two handlers named `save_job` | backend/main.py:1018, 1386 | ruff F811; different routes | low: renaming changes the OpenAPI operationId | rename the recruiter one and pin its old `operation_id`, so OpenAPI is unchanged |
| Unused imports, f-string, variables | 39 findings with ruff's default rules (19 unused imports) | ruff | none | fix (§5e) |
| Résumé shadow parser | extraction/parse/resume.py (381 lines), extraction/parse/compare.py (103) | PARSE_RESUME=off; shadow only, never scored | none: off by default | remove (§4) |
| README parser | extraction/parse/readme.py (393) | PARSE_README=0 | none | remove (§4) |
| Repo cap | extraction/repo_cap.py (22) + branches in graph/pipeline.py, graph/context.py | REPO_CAP=0 | none | remove (§4) |
| LinkedIn parser and its helpers | extraction/parse/{linkedin,common,labeller,claims,dates,sections,run}.py | PARSE_LINKEDIN=1 (kept); `dates` and `sections` are also used by extraction/extra.py | — | keep; `run.py` loses the shadow and README parts |

## 3. Web app

knip fails on this TypeScript version, so I used an import-graph scan from every route entry (page, layout, route)
plus the e2e specs.

| Item | Where | Evidence | Risk | Action |
|---|---|---|---|---|
| accordion, checkbox, progress, select, separator | components/ui/ | no import from any page or component | none (shadcn primitives; re-added with `npx shadcn add`) | remove |
| `cn` imported two ways | 20 files import `"cn"`, 33 import `@/lib/utils` (which re-exports it) | duplicate path | none | merge: all through `@/lib/utils` |
| Exports used only inside their own file | lib/api.ts types (Gap, Repo, …), lib/links.ts `findUrls`/`markdownLinks` (tests/test_links.py runs them through Node), a few constants | — | — | keep |
| Pages, hooks, API client functions | — | every page is linked, every hook used, every `lib/api.ts` call used | — | keep |
| eslint, tsc | — | both clean today | — | keep clean |

## 4. Parked experiments

Every one is off by default and in no `.env` profile (`.env.prod.example` lists them empty).

| Experiment | Code | Tests | Docs/config | Action |
|---|---|---|---|---|
| PARSE_README | extraction/parse/readme.py; `Parsers.wants` github branch; README_MAX_CHARS, README_TABLE_MAX_ROWS | 4 in test_parsers.py | .env.example, .env.prod.example, scripts/readme_live_gate.py, parser_gates.py | remove |
| PARSE_RESUME (shadow) + PARSE_SHADOW_CALLS | extraction/parse/resume.py, compare.py; `Parsers.shadow`, `wait_shadow`, the shadow thread | 5 in test_parsers.py, 1 in test_links.py (`strip_links`, only used by this parser) | env examples | remove |
| PARTITIONED_BATCHES | graph/context.py:261, graph/nodes/expertise.py per-source route | test_partitioned.py (3) | env examples, phase2_compare.py | remove |
| GROUPED_ITEM_JUDGING | graph/context.py:243–255, judges/claude_judge.py `ITEM_FAMILIES` branch | test_phase2_flags.py (part) | env examples | remove |
| GEMMA_JUDGE, GROQ_JUDGE | core/routing.py:310–317 judging rotation | test_phase2_flags.py (part) | env examples | remove. Groq stays for Prompt Guard; GROQ_API_KEY etc. are kept |
| REPO_CAP, MAX_REPOS | extraction/repo_cap.py, graph/pipeline.py:148, graph/context.py:211–231; `stars` on the GitHub repo record (used only as REPO_CAP's tie-break) | test_phase2_flags.py (part) | env examples | remove |
| HEDGE_AFTER_S, HEDGE_TASKS | core/routing.py:342, 451–460 | 1 in test_routing.py | env examples | remove |
| Gemma 4 26B entry | core/config.py:130, its rate limits (no route uses it: "not found on the v1beta API", line 90) | — | — | remove the limits entry; Gemma 4 31B stays in the overflow routes |
| legacy/app (Streamlit) | legacy/app (1,020 lines) | test_ui.py (15 tests, the Streamlit AppTest) | README lines 19–21, requirements.txt `streamlit` | remove; docs/backlog.md keeps the two items the new app lacks |

Kept as asked: routing/failover, the capability table, early start, early Review, PARSE_LINKEDIN,
REMOVE_HIDDEN_TEXT, PROMPT_GUARD, SCORE_AUDIT, the market pipeline, the judgment archive and reseed, and the weight
assistant.

Expected test count change from §4 and §7: about −40 tests (test_ui 15, test_partitioned 3, test_phase2_flags 4,
test_parsers ~12, the hedge test, the `strip_links` test, the `compare_extraction` helper test). These are tests of
removed code, not failures. The 14 skipped tests are the live-model tests (RIGHTSTAFF_LIVE=1) and stay.

## 5. Flags and config

### 5a. Environment variables (128 read by code)

Grouped; each is read in core/config.py unless noted. "Example" = listed in .env.example / .env.prod.example.

| Group | Variables | Default | Still needed | Action |
|---|---|---|---|---|
| Keys | ANTHROPIC_API_KEY, JEV_API_KEY, GITHUB_TOKEN, GEMINI_API_KEY, GROQ_API_KEY | empty | yes | keep |
| Judge and models | JUDGE, LLM_PROVIDER, LIVE, JEV_MODEL, JEV_CONFIDENCE_THRESHOLD, EXTRACT/JUDGE_FALLBACK/REASONING/COMPLEXITY/EXTRACT_WEB/EMAIL/REPLY/FEEDBACK/WEIGHTS/JOB_EXTRACT/MARKET_*_MODEL, GEMINI_*_MODEL, *_EFFORT, GEMINI_THINKING_LEVEL, GROQ_REASONING_EFFORT | see config | yes | keep |
| `JUDGE_BATCH_MODEL` | judges/claude_judge.py, judges/service.py | empty (falls back to JUDGE_FALLBACK_MODEL) | only as an override | keep |
| Routing and limits | ROUTE_MAX_*, ROUTE_BUSY_BUDGET_S, ROUTE_OVERLOAD_RETRY_ROUNDS, OVERLOAD_COOLDOWN_S, UNUSABLE_REST_S, TASK_TIMEOUT_S, TASK_MAX_TOKENS, MODEL_LIMITS/CAPS/CONCURRENCY, GEMINI_LIMITS/RPM/TPM/RPD/CONCURRENCY/TIMEOUT_S, GROQ_CONCURRENCY/TPM/TIMEOUT_S, JUDGE_CONCURRENCY (+ old name MAX_PARALLEL_JUDGE_CALLS), JEV/CLAUDE_MAX_CONCURRENCY, JEV/CLAUDE_DAILY_MAX_REQUESTS | see config | yes | keep. MAX_PARALLEL_JUDGE_CALLS: keep (the old name still works) |
| Parked (§4) | PARSE_README, PARSE_RESUME, PARSE_SHADOW_CALLS, README_MAX_CHARS, README_TABLE_MAX_ROWS, PARTITIONED_BATCHES, GROUPED_ITEM_JUDGING, GEMMA_JUDGE, GROQ_JUDGE, REPO_CAP, MAX_REPOS, HEDGE_AFTER_S, HEDGE_TASKS | off | no | remove |
| LinkedIn parser | PARSE_LINKEDIN, PARSER_MAX_UNCLEAR, JEV_PARSE_BATCH | on, 3, 20 | yes | keep |
| Safety | REMOVE_HIDDEN_TEXT, PROMPT_GUARD, PROMPT_GUARD_MODELS, PROMPT_GUARD_THRESHOLD | on | yes | keep, including the on/off switches: PROMPT_GUARD needs a Groq key and REMOVE_HIDDEN_TEXT has tests that turn it off |
| Always on, pure code | DEDUPE_NAMES (=1), PUNCT_SPACING (=1) | on | the off branch is unused (one test each turns it off) | flag removed, behaviour kept. PUNCT_SPACING's "+punct-1" stays in the normalizer key, so saved document texts still match |
| Always on, constant | GITHUB_README_EXTRACTION (=True, not an env var) | True | no | removed, behaviour kept |
| SCORE_AUDIT | =1; the API's run view returns `audit` | on | it can hide the computation view | keep (the API field stays) |
| JOBMATCH_PREVIEW, JOBMATCH_STRONG/CLOSE | backend/candidate_jobs.py | on | it turns the placeholder match off when the real JobMatch arrives | keep |
| Scoring constants | SCOPE_OF_IMPACT_RELEVANCE_FILTER, NO_ROLES_RELEVANCE_SCOPE_NA ([OPEN] in specs/04), DECAY, PROJECT_TARGET, … | — | scoring rules | keep (no scoring change) |
| Email, workflows, market, benchmarks, weights, uploads | EMAIL_*, SMTP_*, IMAP_*, REPLY_INBOX, DEMO_EMAIL_TO, REQUIRE_DEMO_EMAIL_TO, NUDGE_DAYS, WORKFLOW_TICK_SECONDS, REVIEWING_DAYS, LOW_ACTIVITY_DAYS, CLOSURE_DAYS, PEER_MIN_RATE, MIN_POOL, MIN_GROUP, WINDOW_DAYS, RECOMPUTE_DAYS, REQ_SHARE, PREF_SHARE, MARGIN, WEIGHT_DIM_MIN/MAX, WEIGHT_STEP, UPLOAD_MAX_MB, UPLOAD_RATE_PER_HOUR, TRUST_PROXY | see config | yes | keep |
| Paths and test hooks | RIGHTSTAFF_DB, RIGHTSTAFF_UPLOADS, RIGHTSTAFF_DEMO_DB, RIGHTSTAFF_DEPS_FACTORY, RIGHTSTAFF_TODAY, RIGHTSTAFF_RECORD_SNAPSHOT, RIGHTSTAFF_LIVE, PRINT_TIMINGS, JOB_EXTRACT_FAKE, JOB_EXTRACT_LIVE, EMAIL_LIVE, LIVE_PROFILE, LIVE_CANDIDATE_ID, REAL_PROFILE, JOBS_PROFILE, E2E_* (web) | — | yes (tests, scripts) | keep. LIVE_CANDIDATE_ID, REAL_PROFILE, LIVE_PROFILE: check after §7; remove if only the removed scripts read them |

Every name in the env examples is read somewhere: `ROUTE_<TASK>` through an f-string in core/config.py:102, and
`PROXY_MAX_BODY_MB` by the Caddyfile.

Missing from .env.example (read by code, documented only in .env.prod.example): SCORE_AUDIT, the WEIGHT_* bounds,
UPLOAD_RATE_PER_HOUR, the daily caps, the GEMINI_* model names → add in §5f.

### 5b. Config files

| File | Read by | Action |
|---|---|---|
| config/market_sources.yaml, market_synonyms.json | market pipeline | keep (`market_synonyms.json` has your uncommitted edit; it isn't touched and won't be committed by me) |
| config/skill_groups.yaml, wait_tips.yaml, weight_text_screen.yaml | extraction/skill_groups.py, backend/wait_screens.py, backend/weight_assistant.py | keep |
| config/claim_aliases.json, project_aliases.json, award_aliases.json, degree_aliases.json | extraction/dedupe.py | keep |
| seed/baselines.json, seed/demo_jobs.json | core/db.py, backend/demo_jobs.py | keep |
| prompts/*.txt (11) | each is loaded by its caller; all 11 used | keep |
| deploy/Caddyfile, deploy/verify-fake.env, docker-compose.prod.yml, Dockerfile, frontend/Dockerfile, constraints-prod.txt | deployment | keep; update paths in §5d |

## 6. Database (schema 29, 49 tables)

Every table has a writer and a reader in production code, so no table is unused. A column check found no column
that production code never mentions. Leftovers checked: no karma, peer-validation or Integrity tables exist. The
README gate wrote no rows (parser_runs holds only 10 LinkedIn rows, mode "on"). Row counts are from demo.db.

| Table (rows) | Note | Action |
|---|---|---|
| parser_runs (10) | written by the LinkedIn parser (kept), read by Admin → Screening. After §4, `mode` is always "on" and `comparison_json` always null | keep the table. Drop `comparison_json`? (needs a table rebuild; it is null in all rows) — proposed **keep**, low value |
| gap_reports (15) | `report_markdown` is written but never read: the API re-renders the markdown from `plan_json` | keep (dropping needs a rebuild for no gain) |
| runs (153) | `timings_json` written; read only by scripts/time_run.py (proposed removal) | keep as a diagnostic |
| extraction_cache (0), suppressed_items (0), candidate_plan_done (0) | empty in demo.db, but live features (cache on in the app; "LinkedIn also mentions X"; Mark done) | keep |
| prompt_guard_cache (153) | hashes and scores only | empty it in 2a (§1a) |
| all others | in use | keep |

**Proposed: no schema migration.** Nothing needs dropping, so Phase 2c does nothing unless you approve one of the
column drops above (that would be schema 30, tested on a copy, with archive and reseed checked). Migration code
v1–v29 stays: Anjitha's and the server's databases upgrade through it.

## 7. Scripts, fixtures, test data, docs, screenshots

### Scripts

| Script | Purpose | Used by | Action |
|---|---|---|---|
| reseed, judgment_archive, seed_recruiter_demo, seed_demo_jobs, render_test_profiles, score_test_profiles, report_test_profiles, freeze_constraints, market_dry_run, rebuild_market_version | operations, demo, deploy | Makefile, Playwright config, docs/deploy.md, README, tests | keep |
| purge_person | delete a person's data | tests (guard lists), Phase 2a | keep |
| weight_assistant_live | spec 21 live sample run | waits for your go | keep |
| parser_gates, readme_live_gate, phase2_compare | specs/18 gates for the parked flags | nothing after §4 | remove |
| dedupe_live, dedupe_apply, dedupe_report, consistency_live, audit_merge | one-off live runs already applied (spec 19 phase C, OQ 45, audit round) | nothing | remove |
| compare_extraction, compare_complexity | provider comparisons against the personal reference extraction | test_extraction.py (one test, skips without the reference files) | remove, with that test and tests/extraction_reference/ |
| prompt_guard_check | Prompt Guard false-positive check (live, Groq) | nothing | remove? It's the only way to re-check false positives if the threshold changes — proposed **keep** |
| time_run | times a live first run on given files | README | remove (it needs a real résumé, and the timings it wrote are under timings/) |

### Fixtures and test data

| Item | Action |
|---|---|
| tests/fixtures/archetype_scores.json, archetypes/, schema_v1.sql, weight_texts.yaml, market/, jd/ (Brightwater JD and its text) | keep (regression, migration test, weight assistant, market and job-reading tests) |
| rightstaff-test-profiles/ (the 16 profiles, DESIGN, HOW_TO_JUDGE, judge_packs/, build_judge_packs.py, judge_results.csv) | keep: the synthetic bundle. The judge packs and results are the human-judgement record behind the test profiles |
| `AI_Engineer_JD_Brightwater.pdf` (untracked, repo root) | a copy of tests/fixtures/jd's PDF; not touched |

### Docs and specs

The brief proposes moving specs_old/ and the finished specs to docs/archive/. Two facts change that (my
interpretation; please confirm):
- `specs_old/01–07` are the base specs: CLAUDE.md lists them as `specs/01…07` (a broken path today), and
  `specs/04_scoring_rules.md` is the scoring source of truth.
- The finished specs 09–21 are cited about 400 times in code comments as `specs/NN §x`.

| Item | Proposed action |
|---|---|
| specs_old/01–07 | **move back to specs/** (fixes CLAUDE.md and the comment pointers; 04 is the live source of truth) |
| specs/09–19, 21 | **keep in specs/**, with a short specs/README.md listing each one as done or active. Moving them would mean rewriting ~400 comment pointers, or leaving them broken |
| ITERATION_2.md (root), docs/spec18_summary.md, docs/cleanup_strings.md | move to docs/archive/ |
| docs/open_questions.md, docs/backlog.md, docs/deploy.md | keep (still in use) |
| docs/screenshots/ (159 PNG + 2 JSON, 21 MB; referenced only by comments in 3 e2e specs and cleanup_strings.md) | move to docs/archive/screenshots/, minus the one with names (§1c). Or remove (recoverable from the tag) to shrink checkouts — your call |
| README.md, CLAUDE.md, .env.example, docs/schema.md (new), docs/architecture.md (new) | update in 2f |

## 8. Dependencies

| Package | Used by | Action |
|---|---|---|
| streamlit (requirements.txt) | legacy/app and test_ui.py only | remove with legacy |
| httpx (requirements-prod.txt) | FastAPI's test client only; still installed in prod through anthropic | remove from requirements-prod.txt |
| hypothesis, pytest, reportlab | tests, render_test_profiles (reseed renders PDFs on the server, so reportlab stays in prod) | keep |
| all others (anthropic, langgraph, langgraph-checkpoint-sqlite, pydantic, python-dotenv, pymupdf4llm, pymupdf, python-docx, requests, beautifulsoup4, fastapi, uvicorn, python-multipart, pyyaml, tzdata) | in use | keep |
| npm: @base-ui/react, class-variance-authority, cn, lucide-react, next, next-themes, react, react-dom, shadcn (its CSS), sonner, tw-animate-css; dev: playwright, tailwind, eslint, typescript, types | all imported or used by the build | keep |
| Next.js 16.4.0 | flagged by Next's own security notice (vulnerable version) | not part of this cleanup: the upgrade question is still open from earlier |

No package is used only by code proposed for removal except streamlit.

## 9. Target folder structure

Moves only; behaviour, URLs and the API stay as they are. The layers today are close to right; the biggest problem is
the 2,034-line `backend/main.py` holding all 128 routes, with "backend" mixing routes, services and the workflow engine.

### Backend

```
api/                      HTTP layer (FastAPI)
  app.py                  create_app: middleware, errors, deps, the shared conn() and run_view helpers
  routers/                one file per tag: candidates, candidate (onboarding, targets, jobs), scoring (runs),
                          plan, baselines, jobs, recruiter, workflows, hiring, market, tasks, admin, ops
services/                 request-level logic (was backend/*.py, except the workflow engine)
workflows/                hiring workflow engine, email and scheduling (was backend/{workflows,hiring,
                          hiring_flow,rejection_flow,scheduling,replies,mailer}.py)
graph/                    the LangGraph scoring pipeline (unchanged; see the optional rename below)
scoring/  judges/  extraction/  ingest/  market/      unchanged
providers/                model providers and routing (was core/{llm,routing,spend,limits}.py)
core/                     config, models (Pydantic), text, links, timing
core/db/                  was core/db.py, split: connection (connect, new_id, now_iso), schema (DDL),
                          migrations (v1–v29), baselines (seed + CRUD); `from core import db` keeps working
scripts/  seed/  config/  prompts/  tests/            unchanged (tests stay flat)
```

| Old path | New path |
|---|---|
| backend/main.py | api/app.py + api/routers/*.py (route functions keep their names; registration order is kept wherever two patterns overlap, e.g. `/jobs/templates` before `/jobs/{id}`) |
| backend/{candidate,candidate_jobs,my_applications,review,media,linkedin,jobs,recruiter,weight_assistant,wait_screens,tasks,demo_jobs}.py | services/… (same file names) |
| backend/{workflows,hiring,hiring_flow,rejection_flow,scheduling,replies,mailer}.py | workflows/… (`workflows.py` → `workflows/engine.py`) |
| graph/ | unchanged if the optional rename below is skipped (else pipeline/) |
| core/{llm,routing,spend,limits}.py | providers/… |
| core/db.py | core/db/{__init__,connection,schema,migrations,baselines}.py |
| `backend.main:create_app` (Makefile, Dockerfile, compose, Playwright config, docs) | `api.app:create_app` |

Checks for this step: the LangGraph checkpoints hold no module paths (checked: only `langgraph.types`), so moving
modules doesn't break paused workflows. The judgment archive holds no module paths. `RIGHTSTAFF_DEPS_FACTORY`
(`tests.ui_fakes:make_deps`) is unchanged. The guard tests that list file paths ("sources never scored", "deletes
only") get the new paths.

Optional (higher churn, lower gain; proposed **skip**): renaming `graph/` → `pipeline/` touches ~150 imports for
the name alone. Skipping it keeps `graph/`.

### Web

```
app/                  page routes (unchanged: the URLs are the routes)
components/ui/        shadcn primitives (5 unused removed)
components/shared/    app-main, site-header, mode-banner, progress-panel, ext-link (were loose in components/app/)
components/features/  baselines, candidate, jobs, onboarding, recruiter, review, score (was components/app/<area>/)
hooks/  lib/          unchanged; one `cn` import path
e2e/                  unchanged
```

| Old path | New path |
|---|---|
| components/app/{app-main,site-header,mode-banner,progress-panel,ext-link}.tsx | components/shared/… |
| components/app/<area>/ | components/features/<area>/ |

## Summary of proposed removals

| Area | Removed |
|---|---|
| Personal data | 10 profiles and ~11,900 linked rows; 4 checkpoint threads; 122 HTTP-cache, 11 document-text and 153 prompt-guard-cache rows; uploads/, rightstaff.db, 15 old DB copies, timings/, .verify/, the live log and the reference extraction; 1 screenshot and ~25 identifiable strings in 9 tracked files |
| Python | 3 parser modules + repo_cap (~900 lines), the parked branches in routing/context/expertise/claude_judge, 6 dead functions, 1 duplicate helper, legacy/app (1,020 lines), 13 scripts (~3,000 lines), ~40 tests of removed code |
| Web | 5 unused UI components; one `cn` path |
| Flags | 13 parked env vars, 3 always-on switches (2 env vars + 1 constant) |
| Dependencies | streamlit; httpx from the prod list |
| Database | nothing (no migration) unless you approve a column drop |
| Docs | 3 docs to docs/archive/; screenshots to docs/archive/ (or removed); specs_old/ back into specs/ |
