"""The latest schema (specs/03): a new database is created from it in one go."""
from __future__ import annotations

SCHEMA_VERSION = 29

SCHEMA = """
CREATE TABLE IF NOT EXISTS baselines (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  level TEXT NOT NULL,
  required_years REAL NOT NULL,
  expected_reach INTEGER NOT NULL,
  scope_expectation TEXT,
  complexity_criteria TEXT,
  description TEXT,
  is_mock INTEGER DEFAULT 1,
  updated_at TEXT,
  owner_job_id TEXT,              -- set for a recruiter-created job's own baseline (specs/14); never listed as a role
  market_version_id TEXT          -- set for a published Market baseline (specs/15); used through the role's toggle
);

CREATE TABLE IF NOT EXISTS baseline_criteria (
  id TEXT PRIMARY KEY,
  baseline_id TEXT REFERENCES baselines(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  aliases TEXT,
  priority TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY,
  name TEXT,
  target_role TEXT,
  github_username TEXT,
  portfolio_url TEXT,
  created_at TEXT,
  data_source TEXT,               -- null = uploaded; demo_parsed | archetype | test_variant (demo data, labelled in the UI)
  email TEXT,                     -- contact address for recruiter emails (demo: fictional, example.com)
  linkedin_url TEXT               -- profile link entered on the upload form (display only, never scored)
);

CREATE TABLE IF NOT EXISTS evidence_items (
  id TEXT PRIMARY KEY,
  candidate_id TEXT REFERENCES candidates(id),
  type TEXT,
  source TEXT,
  file_path TEXT,
  file_sha256 TEXT,
  raw_text TEXT,
  extracted_json TEXT,
  items_extracted INTEGER,
  items_dropped INTEGER,
  status TEXT,
  error TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS roles (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  title TEXT, employer TEXT, industry TEXT,
  start_date TEXT, end_date TEXT, is_current INTEGER,
  team_size INTEGER,
  quote TEXT,
  source TEXT DEFAULT 'extracted',
  needs_review INTEGER DEFAULT 0,
  review_reasons TEXT,
  method TEXT,                    -- parser | jev | llm (specs/18 §4); null = typed or edited by the user
  location TEXT,                  -- display only (specs/19)
  sources TEXT                    -- JSON: evidence ids merged into this item; display only, never scored
);

CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  role_id TEXT,
  kind TEXT,
  name TEXT, description TEXT, url TEXT,
  start_date TEXT, end_date TEXT,
  is_fork INTEGER, own_commits INTEGER,
  quote TEXT,
  source TEXT DEFAULT 'extracted',
  needs_review INTEGER DEFAULT 0,
  review_reasons TEXT,
  method TEXT,
  details TEXT,                   -- JSON: type-specific Review fields (venue, event, placement, …)
  sources TEXT
);

CREATE TABLE IF NOT EXISTS bullets (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  role_id TEXT, project_id TEXT,
  text TEXT,
  location TEXT,
  source TEXT DEFAULT 'extracted',
  method TEXT
);

CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  bullet_id TEXT,
  kind TEXT,
  name_raw TEXT,
  name_norm TEXT,
  quote TEXT,
  source TEXT DEFAULT 'extracted',
  method TEXT,
  details TEXT, skill_group TEXT, sources TEXT
);

CREATE TABLE IF NOT EXISTS certifications (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  name TEXT, issuer TEXT, issued_date TEXT,
  credential_id TEXT, verify_url TEXT,
  verified INTEGER DEFAULT 0,
  source TEXT DEFAULT 'extracted',
  method TEXT,
  sources TEXT
);

CREATE TABLE IF NOT EXISTS extra_items (   -- display-only proof types (specs/19 Phase D): never judged or scored
  id TEXT PRIMARY KEY, candidate_id TEXT, evidence_id TEXT,
  type TEXT,                      -- education | patent | volunteering | course | language
  fields_json TEXT, quote TEXT, source TEXT DEFAULT 'extracted', position INTEGER, created_at TEXT
);

CREATE TABLE IF NOT EXISTS dropped_items (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  item_type TEXT,
  text TEXT,
  payload TEXT,
  parent_type TEXT, parent_id TEXT,
  reason TEXT,
  restored_id TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS references_ (
  id TEXT PRIMARY KEY,
  candidate_id TEXT,
  name TEXT, relationship TEXT,
  contact TEXT, statement TEXT
);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, baseline_id TEXT,
  weights_json TEXT,
  status TEXT,
  started_at TEXT, finished_at TEXT,
  logic_version TEXT,
  baseline_hash TEXT,
  models_json TEXT,
  timings_json TEXT,
  plan_status TEXT,               -- generating | ready | failed (improvement plan, written after the score)
  scored_on TEXT                  -- the scoring date (months, recency); the score's Computation view uses it
);

CREATE TABLE IF NOT EXISTS document_texts (
  file_sha256 TEXT,
  normalizer TEXT,
  text TEXT,
  created_at TEXT,
  hidden_json TEXT,               -- hidden text removed before extraction (REMOVE_HIDDEN_TEXT; for Admin only)
  PRIMARY KEY (file_sha256, normalizer)
);

CREATE TABLE IF NOT EXISTS deleted_items (   -- deleted in Review while sources are read: not re-added by a later one
  id TEXT PRIMARY KEY, candidate_id TEXT, item_type TEXT, key TEXT, label TEXT, payload TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS suppressed_items ( -- a later source named a deleted item: "LinkedIn also mentions X · Add back"
  id TEXT PRIMARY KEY, candidate_id TEXT, evidence_id TEXT, item_type TEXT, key TEXT, label TEXT, payload TEXT,
  restored INTEGER DEFAULT 0, created_at TEXT
);

CREATE TABLE IF NOT EXISTS prompt_guard_cache (  -- Prompt Guard score per chunk text (specs/18 Phase 4)
  text_hash TEXT, model TEXT, score REAL, created_at TEXT, PRIMARY KEY (text_hash, model)
);

CREATE TABLE IF NOT EXISTS prompt_guard_flags (  -- chunks above the threshold: Admin only, never scored or shown
  id TEXT PRIMARY KEY, candidate_id TEXT, evidence_id TEXT, source TEXT, chunk_index INTEGER, chunk TEXT,
  score REAL, hidden INTEGER DEFAULT 0, model TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS parser_runs (     -- code-first extraction per document (specs/18 §4): used or shadow
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  doc_type TEXT,
  mode TEXT,                      -- on | shadow
  outcome TEXT,                   -- parsed | fallback
  reasons_json TEXT, notes_json TEXT, items_json TEXT,
  comparison_json TEXT,           -- shadow: against the LLM extraction of the same document
  seconds REAL, jev_requests INTEGER, jev_questions INTEGER, llm_calls INTEGER,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (           -- recruiter view (phase 11): one job per baseline
  id TEXT PRIMARY KEY,
  baseline_id TEXT,
  title TEXT, company TEXT,
  weights_json TEXT,              -- defaults to the team table
  must_haves_json TEXT,           -- list of baseline criterion ids
  preset TEXT,                    -- weights preset name, or null for custom
  created_at TEXT,
  settings_json TEXT,             -- recruiter name, interview duration, availability windows, scheduling link
  status TEXT DEFAULT 'open',     -- draft | open | closed (specs/14)
  details_json TEXT,              -- department, location, workplace, employment type, level, years, short description
  jd_text TEXT, jd_file TEXT,     -- the original job description (normalized text, saved file)
  draft_json TEXT,                -- the create/edit form while it is being worked on (autosaved)
  updated_at TEXT,
  data_source TEXT,               -- 'demo' for seeded demo jobs (specs/17 §9), null otherwise
  role_baseline_id TEXT,          -- "Role family": a role baseline id, 'none', or null (unknown)
  published_at TEXT               -- when the job was posted (drafts: null)
);

CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY,
  job_id TEXT, candidate_id TEXT,
  run_id TEXT,                    -- latest scoring run against the job's baseline
  status TEXT,                    -- new | shortlisted | in_loop | not_moving_forward (follows the stage)
  applied_at TEXT,
  stage_id TEXT                   -- interview_stages.id; null = not yet in the interview plan
);

CREATE TABLE IF NOT EXISTS interview_stages (   -- interview plan per job (specs/13 §3)
  id TEXT PRIMARY KEY, job_id TEXT,
  name TEXT, position INTEGER,
  focus_json TEXT,                -- focus areas (criterion names), pre-filled from the job's must-haves
  terminal INTEGER DEFAULT 0      -- 1 = "Not moving forward"
);

CREATE TABLE IF NOT EXISTS activity (       -- stage moves, emails, decisions (Activity tab)
  id TEXT PRIMARY KEY, application_id TEXT, job_id TEXT,
  kind TEXT,                      -- stage | email | decision | note
  summary TEXT, detail_json TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS emails (         -- every drafted or sent email (specs/13 §4)
  id TEXT PRIMARY KEY, application_id TEXT, job_id TEXT,
  kind TEXT,                      -- not_moving_forward | scheduling | stage
  stage_id TEXT, batch_id TEXT,
  status TEXT,                    -- draft | sent | failed | cancelled
  intended_to TEXT, actual_to TEXT,
  subject TEXT, body TEXT,
  model TEXT,                     -- model id, or "template" (labelled "Template draft")
  provider TEXT,                  -- outbox | smtp
  reasons_json TEXT, checks_json TEXT, error TEXT,
  created_at TEXT, updated_at TEXT, sent_at TEXT,
  token TEXT,                     -- hiring workflow token "[RS-1a2b3c]" added to the subject on send (specs/16)
  message_id TEXT,                -- Message-ID header, set on send
  in_reply_to TEXT,               -- Message-ID of the candidate reply this answers
  attachments_json TEXT,          -- [{filename, content_type, text}] e.g. the .ics invite
  interview_id TEXT
);

CREATE TABLE IF NOT EXISTS workflows (      -- hiring workflow per application (specs/16 §6)
  application_id TEXT PRIMARY KEY, job_id TEXT,
  thread_id TEXT,                 -- LangGraph thread (SQLite checkpointer)
  token TEXT,                     -- matches replies: "[RS-<token>]" in the subject
  stage TEXT,                     -- timeline position, e.g. "Phone screen · Scheduling"
  round INTEGER, phase TEXT,      -- interview round (index into the plan's interview stages) and its phase
  step TEXT,                      -- the waiting interrupt: approve_email | await_reply | confirm_slot | …
  title TEXT,                     -- Tasks line, e.g. "Approve scheduling email"
  payload_json TEXT,              -- what the step shows (email id, reply id, slots, options)
  status TEXT,                    -- needs_action | waiting_candidate | scheduled | paused | closed
  paused_status TEXT, next_due TEXT, step_since TEXT, outcome TEXT,
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS workflow_events (
  id TEXT PRIMARY KEY, application_id TEXT,
  type TEXT, payload_json TEXT,
  actor TEXT,                     -- recruiter | agent | candidate
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (       -- the email thread: every sent and received message
  id TEXT PRIMARY KEY, application_id TEXT,
  direction TEXT,                 -- out | in
  subject TEXT, body TEXT, token TEXT,
  message_id TEXT, in_reply_to TEXT,
  intended_to TEXT, actual_to TEXT, from_addr TEXT,
  draft_id TEXT,                  -- emails.id for outbound
  status TEXT,                    -- out: sent | in: new | read
  source TEXT,                    -- outbox | smtp | pasted | canned | imap
  reading_json TEXT,              -- reply classification shown to the recruiter
  sent_at TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS interviews (
  id TEXT PRIMARY KEY, application_id TEXT, job_id TEXT,
  stage TEXT, start TEXT, end TEXT, timezone TEXT,
  ics_uid TEXT, status TEXT,      -- tentative | confirmed | done | cancelled
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS scorecards (
  id TEXT PRIMARY KEY, application_id TEXT, interview_id TEXT, stage TEXT,
  interviewer TEXT,
  ratings_json TEXT,              -- [{area, rating 1-4, notes}]
  notes TEXT,
  recommendation TEXT,            -- strong_yes | yes | no | strong_no
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS generated_summaries (  -- "why" points and phone-screen questions, cached per run and weights
  run_id TEXT, kind TEXT,         -- why | questions
  weights_hash TEXT,
  content_json TEXT, model TEXT,  -- model = "template-v1" for code templates ("Demo text")
  created_at TEXT,
  PRIMARY KEY (run_id, kind, weights_hash)
);

CREATE TABLE IF NOT EXISTS tasks (          -- background work started by the API (phase 10)
  id TEXT PRIMARY KEY,
  kind TEXT,                      -- onboard | score | plan | rejudge
  candidate_id TEXT, run_id TEXT,
  status TEXT,                    -- running | awaiting_review | done | error
  step TEXT, message TEXT, percent REAL, error TEXT,
  result_json TEXT,
  created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS task_events (    -- progress lines streamed over SSE
  task_id TEXT, seq INTEGER,
  step TEXT, message TEXT, percent REAL, status TEXT, created_at TEXT,
  PRIMARY KEY (task_id, seq)
);

CREATE TABLE IF NOT EXISTS media_links (    -- demo videos (YouTube, Loom); display only, not scored
  id TEXT PRIMARY KEY,
  candidate_id TEXT,
  url TEXT, kind TEXT,            -- youtube | loom
  title TEXT, project_id TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS provider_usage (  -- free-tier daily quota, counted across restarts
  provider TEXT, model TEXT,
  day TEXT,                       -- Pacific date (quotas reset at midnight Pacific)
  requests INTEGER, tokens INTEGER,
  PRIMARY KEY (provider, model, day)
);

CREATE TABLE IF NOT EXISTS extraction_cache (
  key TEXT PRIMARY KEY,           -- sha256 of model + system prompt + schema + user message
  model TEXT,
  output_json TEXT,
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS http_cache (
  key TEXT PRIMARY KEY,           -- URL + Accept header; never the token
  status INTEGER,
  headers_json TEXT,              -- ETag, Link, Content-Type
  body TEXT,
  fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS judgments (
  id TEXT PRIMARY KEY,
  candidate_id TEXT,
  baseline_id TEXT,
  baseline_hash TEXT,
  run_id TEXT,
  subject_type TEXT,
  subject_id TEXT,
  question TEXT,
  label TEXT,
  probabilities TEXT,
  quote TEXT,
  reasoning TEXT,
  method TEXT,
  model_version TEXT,
  used INTEGER DEFAULT 1,
  confidence REAL,
  score REAL,
  input_hash TEXT,
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_judgments_lookup
  ON judgments(candidate_id, baseline_id, question, subject_id);
CREATE INDEX IF NOT EXISTS idx_judgments_cache
  ON judgments(subject_id, question, baseline_id, baseline_hash, model_version);
CREATE INDEX IF NOT EXISTS idx_judgments_input ON judgments(subject_id, question, input_hash);

CREATE TABLE IF NOT EXISTS scores (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  candidate_id TEXT, baseline_id TEXT,
  subcategory TEXT,
  score REAL,
  status TEXT,
  details TEXT,
  guidance TEXT,
  logic_version TEXT,
  computed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_scores_run ON scores(run_id);

CREATE TABLE IF NOT EXISTS market_postings (   -- specs/15 §3
  id TEXT PRIMARY KEY, role TEXT, company TEXT, tier TEXT, source TEXT, source_id TEXT, external_id TEXT,
  source_url TEXT, title TEXT, location TEXT, level TEXT, level_method TEXT,
  text_hash TEXT, text TEXT, raw_file TEXT, posted_at TEXT, first_seen TEXT, last_seen TEXT,
  status TEXT,                    -- active | closed | excluded (title filter)
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS market_extractions (   -- one extraction per posting text, never repeated
  text_hash TEXT PRIMARY KEY, model TEXT, extract_json TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS market_versions (
  id TEXT PRIMARY KEY, role TEXT, level TEXT, version INTEGER, window_start TEXT, window_end TEXT,
  postings INTEGER, companies INTEGER, content_json TEXT, content_hash TEXT,
  status TEXT,                    -- draft | published | discarded
  insufficient INTEGER, test_version INTEGER, baseline_id TEXT, summary_method TEXT,
  created_at TEXT, published_at TEXT
);
CREATE TABLE IF NOT EXISTS market_suggestions (   -- new names waiting for a person: merge into a canonical name?
  id TEXT PRIMARY KEY, name TEXT, canonical TEXT, method TEXT, status TEXT, created_at TEXT, decided_at TEXT
);
CREATE TABLE IF NOT EXISTS market_settings (key TEXT PRIMARY KEY, value TEXT);

-- Candidate experience (specs/17)
CREATE TABLE IF NOT EXISTS candidate_targets (    -- target roles (1-3 role baselines), one primary
  candidate_id TEXT, baseline_id TEXT, is_primary INTEGER, position INTEGER, added_at TEXT,
  PRIMARY KEY (candidate_id, baseline_id)
);
CREATE TABLE IF NOT EXISTS candidate_onboarding ( -- first-run progress, so every step is resumable
  candidate_id TEXT PRIMARY KEY,
  step TEXT,                      -- proof | roles | review | scores | details | done
  read_task_id TEXT, score_task_id TEXT, confirmed_at TEXT, finished_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS job_events (           -- recruiter activity on a job (specs/17 §4.1)
  id TEXT PRIMARY KEY, job_id TEXT, application_id TEXT,
  kind TEXT,                      -- panel_opened | stage_moved (seeded demo activity uses both)
  demo INTEGER DEFAULT 0, created_at TEXT
);
CREATE TABLE IF NOT EXISTS candidate_job_prefs (  -- Save / Not interested on the candidate Home (no score effect)
  candidate_id TEXT, job_id TEXT, saved INTEGER DEFAULT 0, hidden INTEGER DEFAULT 0, reason TEXT, updated_at TEXT,
  PRIMARY KEY (candidate_id, job_id)
);
CREATE TABLE IF NOT EXISTS candidate_plan_done (    -- "Mark done" on a plan action (changes no score)
  candidate_id TEXT, baseline_id TEXT, action_key TEXT, title TEXT, done_at TEXT,
  PRIMARY KEY (candidate_id, baseline_id, action_key)
);
CREATE TABLE IF NOT EXISTS candidate_application_details (  -- "fill once": never sent to a model, never shown
  candidate_id TEXT PRIMARY KEY,                            -- to recruiters, never used in scoring or pools
  work_auth_json TEXT, questions_json TEXT, eeo_json TEXT, saved_at TEXT, dismissed_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS weight_suggestions (   -- recruiter weight assistant (specs/21 §6): every suggestion
  id TEXT PRIMARY KEY, job_id TEXT, recruiter TEXT, text TEXT, model TEXT, cache_key TEXT, cached INTEGER DEFAULT 0,
  weights_json TEXT,              -- validated, bounded, normalized and rounded
  reasons_json TEXT, summary TEXT, ignored_json TEXT,
  current_weights_json TEXT,      -- the job's weights when it was suggested
  applied INTEGER DEFAULT 0, applied_at TEXT, previous_weights_json TEXT, undone_at TEXT, created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_weight_suggestions ON weight_suggestions(job_id, created_at);

CREATE TABLE IF NOT EXISTS gap_reports (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  weights_json TEXT,
  ranked_gaps TEXT,
  plan_json TEXT,
  report_markdown TEXT,
  model_version TEXT,
  created_at TEXT
);
"""
