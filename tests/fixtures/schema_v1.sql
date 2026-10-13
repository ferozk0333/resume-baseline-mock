-- Frozen v1 schema, used to test migrations. Do not edit.
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
  updated_at TEXT
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
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS evidence_items (
  id TEXT PRIMARY KEY,
  candidate_id TEXT REFERENCES candidates(id),
  type TEXT,
  source TEXT,
  file_path TEXT,
  file_sha256 TEXT,
  raw_text TEXT,
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
  needs_review INTEGER DEFAULT 0
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
  needs_review INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bullets (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  role_id TEXT, project_id TEXT,
  text TEXT,
  location TEXT
);

CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  bullet_id TEXT,
  kind TEXT,
  name_raw TEXT,
  name_norm TEXT,
  quote TEXT
);

CREATE TABLE IF NOT EXISTS certifications (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  name TEXT, issuer TEXT, issued_date TEXT,
  credential_id TEXT, verify_url TEXT,
  verified INTEGER DEFAULT 0
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
  started_at TEXT, finished_at TEXT
);

CREATE TABLE IF NOT EXISTS judgments (
  id TEXT PRIMARY KEY,
  candidate_id TEXT,
  baseline_id TEXT,
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
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_judgments_lookup
  ON judgments(candidate_id, baseline_id, question, subject_id);

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

CREATE TABLE IF NOT EXISTS gap_reports (
  id TEXT PRIMARY KEY,
  run_id TEXT,
  weights_json TEXT,
  ranked_gaps TEXT,
  report_markdown TEXT,
  model_version TEXT,
  created_at TEXT
);
