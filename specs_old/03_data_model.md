# 03 — Data Model (SQLite)

Adapted from the *Scoring Pipeline: Technical Implementation* doc. Baselines take the place of `jobs`. IDs are UUID strings; dates are ISO `YYYY-MM-DD`; JSON stored as TEXT.

Rules:
- **Append, don't overwrite** judgments and scores. Latest row wins in queries; old rows stay for comparison.
- Every judgment and score row carries a version (`model_version`, `logic_version`).
- `baseline_id = NULL` in `judgments` means the judgment is job-independent and reusable.
- `judgments.baseline_hash` is set for job-dependent judgments only (see `02` for what it hashes). A cached judgment is reused only when its `baseline_hash` matches the baseline's current hash.
- Deleting a baseline deletes its criteria (cascade) but keeps its `runs`, `judgments`, `scores` and `gap_reports` as history. Run lists show such runs with the baseline as **"(deleted)"**.
- Schema version is tracked with `PRAGMA user_version` (currently 15; v2 added `judgments.baseline_hash`; v3 added `roles.review_reasons`, `projects.review_reasons`, `evidence_items.extracted_json`; v4 added `source` on `bullets`/`claims`/`certifications`, `evidence_items.items_extracted`/`items_dropped`, and the `dropped_items` table; v5 added `judgments.used`; v6 added `judgments.confidence`/`score`; v7 added `judgments.input_hash` and `runs.logic_version`/`baseline_hash`/`models_json`; v8 added `gap_reports.plan_json`; iteration 2: v9 `runs.timings_json`, v10 the `document_texts` and `http_cache` tables, v11 `runs.plan_status`, v12 the `extraction_cache` table; v13 `provider_usage`; v14 `tasks`, `task_events`, `media_links`; v15 `candidates.data_source`, `jobs`, `applications`, `generated_summaries`).
- Items dropped by the quote check are kept in `dropped_items` so the review step can offer **Add back**; an added-back item is saved with `source = user_edit`. Re-running extraction replaces the profile and the dropped list.

```sql
-- Baselines (mock role profiles)
CREATE TABLE baselines (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  level TEXT NOT NULL,                 -- entry | mid | senior ...
  required_years REAL NOT NULL,        -- 0 treated as 1 in Depth (see 04)
  expected_reach INTEGER NOT NULL,     -- 1..5
  scope_expectation TEXT,
  complexity_criteria TEXT,
  description TEXT,                    -- short JD
  is_mock INTEGER DEFAULT 1,
  updated_at TEXT
);

CREATE TABLE baseline_criteria (
  id TEXT PRIMARY KEY,
  baseline_id TEXT REFERENCES baselines(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                  -- skill | tool | certification
  name TEXT NOT NULL,
  description TEXT,                    -- definition used as judge rubric
  aliases TEXT,                        -- JSON list of synonyms
  priority TEXT NOT NULL               -- required | preferred
);

-- Candidate and evidence (job-independent)
CREATE TABLE candidates (
  id TEXT PRIMARY KEY,
  name TEXT,
  target_role TEXT,
  github_username TEXT,
  portfolio_url TEXT,
  created_at TEXT
);

CREATE TABLE evidence_items (
  id TEXT PRIMARY KEY,
  candidate_id TEXT REFERENCES candidates(id),
  type TEXT,                           -- resume | linkedin_pdf | github_repo | portfolio | certification | reference
  source TEXT,                         -- upload | url | api | form
  file_path TEXT,
  file_sha256 TEXT,
  raw_text TEXT,
  extracted_json TEXT,                 -- this source's own validated items, before cross-source dedupe (used by Consistency)
  items_extracted INTEGER,             -- items kept by the quote check
  items_dropped INTEGER,               -- items dropped by the quote check (drop rate = dropped / (kept + dropped))
  status TEXT,                         -- uploaded | parsed | extracted | validated | failed
  error TEXT,
  created_at TEXT
);

CREATE TABLE roles (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  title TEXT, employer TEXT, industry TEXT,
  start_date TEXT, end_date TEXT, is_current INTEGER,
  team_size INTEGER,
  quote TEXT,
  source TEXT DEFAULT 'extracted',     -- extracted | user_edit
  needs_review INTEGER DEFAULT 0,
  review_reasons TEXT                  -- JSON list: year_only_start, missing_end_date, future_date, ...
);

CREATE TABLE projects (                -- projects, hackathons, publications, repos, case studies, work samples
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  role_id TEXT,                        -- NULL = personal project
  kind TEXT,                           -- project | hackathon | publication | repo | case_study | work_sample | presentation
  name TEXT, description TEXT, url TEXT,
  start_date TEXT, end_date TEXT,
  is_fork INTEGER, own_commits INTEGER, -- GitHub only; own_commits NOT NULL marks a GitHub-backed project
                                       -- (a resume project merged with its repo keeps kind = project)
  quote TEXT,
  source TEXT DEFAULT 'extracted',
  needs_review INTEGER DEFAULT 0,
  review_reasons TEXT
);

CREATE TABLE bullets (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  role_id TEXT, project_id TEXT,       -- exactly one is set
  text TEXT,
  location TEXT,                       -- e.g. "role 2, bullet 3"
  source TEXT DEFAULT 'extracted'      -- extracted | user_edit
);

CREATE TABLE claims (                  -- skills, tools, awards
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  bullet_id TEXT,                      -- NULL = listed only (skills section)
  kind TEXT,                           -- skill | tool | award
  name_raw TEXT,
  name_norm TEXT,                      -- normalized: lowercase, punctuation to spaces, spelling variants from extraction/synonyms.py
  quote TEXT,
  source TEXT DEFAULT 'extracted'
);

CREATE TABLE certifications (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  name TEXT, issuer TEXT, issued_date TEXT,
  credential_id TEXT, verify_url TEXT,
  verified INTEGER DEFAULT 0,          -- 1 only if verify_url/credential_id present (MVP rule, see 04)
  source TEXT DEFAULT 'extracted'
);

CREATE TABLE dropped_items (           -- extracted items whose quote was not found in the source
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  item_type TEXT,                      -- role | project | bullet | claim | certification
  text TEXT,                           -- shown in the review step
  payload TEXT,                        -- JSON of the raw extracted item, used to rebuild it on Add back
  parent_type TEXT, parent_id TEXT,    -- bullets: owning role/project (after cross-source merge)
  reason TEXT,                         -- quote_not_found
  restored_id TEXT,                    -- id of the re-created record once added back
  created_at TEXT
);

CREATE TABLE references_ (
  id TEXT PRIMARY KEY,
  candidate_id TEXT,
  name TEXT, relationship TEXT,        -- manager | supervisor | peer | other
  contact TEXT, statement TEXT
);

-- Runs, judgments, scores
CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, baseline_id TEXT,
  weights_json TEXT,                   -- snapshot of weights used
  status TEXT,                         -- running | done | error
  started_at TEXT, finished_at TEXT,
  logic_version TEXT,
  baseline_hash TEXT,                  -- baseline content hash at run time
  models_json TEXT,                    -- judge, Jev model + threshold, Claude models used
  timings_json TEXT,                   -- per-step timing, call counts, tokens, estimated cost (specs/09 §1)
  plan_status TEXT                     -- generating | ready | failed: the plan is written after the score (09 §2.6)
);

CREATE TABLE judgments (
  id TEXT PRIMARY KEY,
  candidate_id TEXT,
  baseline_id TEXT,                    -- NULL = job-independent
  baseline_hash TEXT,                  -- NULL = job-independent; else SHA-256 of judge-visible baseline fields
  run_id TEXT,
  subject_type TEXT,                   -- role | project | bullet | claim | evidence_item | criterion | candidate
  subject_id TEXT,
  question TEXT,                       -- e.g. relevance, impact_type, ownership, proof_support
  label TEXT,
  probabilities TEXT,                  -- JSON (Jev)
  quote TEXT,
  reasoning TEXT,
  method TEXT,                         -- jev | claude | rule
  model_version TEXT,
  used INTEGER DEFAULT 1,              -- 0 = superseded/not trusted (low-confidence Jev answer, cleared by Re-judge); cache reads used = 1 only
  confidence REAL,                     -- Jev confidence (yes/no: |2p-1|)
  score REAL,                          -- Jev Score in-between value; stored, never used in scoring
  input_hash TEXT,                     -- SHA-256 of question + exact judge context
  created_at TEXT
);
CREATE INDEX idx_judgments_lookup ON judgments(candidate_id, baseline_id, question, subject_id);
CREATE INDEX idx_judgments_cache ON judgments(subject_id, question, baseline_id, baseline_hash, model_version);

CREATE TABLE scores (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  candidate_id TEXT, baseline_id TEXT,
  subcategory TEXT,
  score REAL,                          -- 0..1, NULL when na/error
  status TEXT,                         -- scored | missing | na | error
  details TEXT,                        -- JSON: evidence ids, winning item, per-criterion breakdown
  guidance TEXT,                       -- message for missing evidence
  logic_version TEXT,
  computed_at TEXT
);
CREATE INDEX idx_scores_run ON scores(run_id);

CREATE TABLE gap_reports (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  weights_json TEXT,
  ranked_gaps TEXT,                    -- JSON from code (at generation weights)
  plan_json TEXT,                      -- validated structured plan: actions, quick_wins, not_actionable, dropped, first_attempt_errors, gap_keys
  report_markdown TEXT,                -- plan rendered by code at generation weights
  model_version TEXT,
  created_at TEXT
);

-- Iteration 2 caches (no scoring data)
CREATE TABLE document_texts (          -- normalized Markdown of each upload; exactly what extraction and quotes use
  file_sha256 TEXT, normalizer TEXT,   -- normalizer = parser + clean-up version
  text TEXT, created_at TEXT,
  PRIMARY KEY (file_sha256, normalizer)
);
CREATE TABLE extraction_cache (        -- extraction output reused for an identical document (app only)
  key TEXT PRIMARY KEY,                -- sha256(model, system prompt, output schema, user message)
  model TEXT, output_json TEXT, created_at TEXT
);
CREATE TABLE http_cache (              -- GitHub API responses, 24 h, revalidated with ETag
  key TEXT PRIMARY KEY,                -- URL + Accept header (never the token)
  status INTEGER, headers_json TEXT, body TEXT, fetched_at TEXT
);
```

Note: `scores.status = missing` means score 0 with guidance; `na` and `error` are excluded from averages (see `04`).
