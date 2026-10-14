"""SQLite connection, schema (specs/03), seed loading and baseline CRUD."""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core import config
from core.models import Baseline, BaselineCriterion

SCHEMA_VERSION = 15

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
  created_at TEXT,
  data_source TEXT                -- null = uploaded; demo_parsed | archetype (demo data, labelled in the UI)
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
  review_reasons TEXT
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
  review_reasons TEXT
);

CREATE TABLE IF NOT EXISTS bullets (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  role_id TEXT, project_id TEXT,
  text TEXT,
  location TEXT,
  source TEXT DEFAULT 'extracted'
);

CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  bullet_id TEXT,
  kind TEXT,
  name_raw TEXT,
  name_norm TEXT,
  quote TEXT,
  source TEXT DEFAULT 'extracted'
);

CREATE TABLE IF NOT EXISTS certifications (
  id TEXT PRIMARY KEY,
  candidate_id TEXT, evidence_id TEXT,
  name TEXT, issuer TEXT, issued_date TEXT,
  credential_id TEXT, verify_url TEXT,
  verified INTEGER DEFAULT 0,
  source TEXT DEFAULT 'extracted'
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
  plan_status TEXT                -- generating | ready | failed (improvement plan, written after the score)
);

CREATE TABLE IF NOT EXISTS document_texts (
  file_sha256 TEXT,
  normalizer TEXT,
  text TEXT,
  created_at TEXT,
  PRIMARY KEY (file_sha256, normalizer)
);

CREATE TABLE IF NOT EXISTS jobs (           -- recruiter view (phase 11): one job per baseline
  id TEXT PRIMARY KEY,
  baseline_id TEXT,
  title TEXT, company TEXT,
  weights_json TEXT,              -- defaults to the team table
  must_haves_json TEXT,           -- list of baseline criterion ids
  preset TEXT,                    -- weights preset name, or null for custom
  created_at TEXT
);

CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY,
  job_id TEXT, candidate_id TEXT,
  run_id TEXT,                    -- latest scoring run against the job's baseline
  status TEXT,                    -- new | shortlisted | not_moving_forward
  applied_at TEXT
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


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


# --- Connection / migrations ----------------------------------------------

def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the DB, enable foreign keys (needed for criteria cascade) and migrate."""
    conn = sqlite3.connect(str(path or config.DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # parallel graph nodes read while another writes
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create the latest schema on a new DB, or step an older DB forward."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    with conn:
        if version == 0:
            conn.executescript(SCHEMA)
        else:
            for target, statements in MIGRATIONS:
                if version < target:
                    for sql in statements:
                        conn.execute(sql)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


DOCUMENT_TEXTS = """CREATE TABLE IF NOT EXISTS document_texts (
  file_sha256 TEXT, normalizer TEXT, text TEXT, created_at TEXT, PRIMARY KEY (file_sha256, normalizer))"""
EXTRACTION_CACHE = """CREATE TABLE IF NOT EXISTS extraction_cache (
  key TEXT PRIMARY KEY, model TEXT, output_json TEXT, created_at TEXT)"""
JOBS = """CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, baseline_id TEXT, title TEXT, company TEXT, weights_json TEXT, must_haves_json TEXT,
  preset TEXT, created_at TEXT)"""
APPLICATIONS = """CREATE TABLE IF NOT EXISTS applications (
  id TEXT PRIMARY KEY, job_id TEXT, candidate_id TEXT, run_id TEXT, status TEXT, applied_at TEXT)"""
GENERATED_SUMMARIES = """CREATE TABLE IF NOT EXISTS generated_summaries (
  run_id TEXT, kind TEXT, weights_hash TEXT, content_json TEXT, model TEXT, created_at TEXT,
  PRIMARY KEY (run_id, kind, weights_hash))"""
TASKS = """CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, kind TEXT, candidate_id TEXT, run_id TEXT, status TEXT, step TEXT, message TEXT,
  percent REAL, error TEXT, result_json TEXT, created_at TEXT, updated_at TEXT)"""
TASK_EVENTS = """CREATE TABLE IF NOT EXISTS task_events (
  task_id TEXT, seq INTEGER, step TEXT, message TEXT, percent REAL, status TEXT, created_at TEXT,
  PRIMARY KEY (task_id, seq))"""
MEDIA_LINKS = """CREATE TABLE IF NOT EXISTS media_links (
  id TEXT PRIMARY KEY, candidate_id TEXT, url TEXT, kind TEXT, title TEXT, project_id TEXT, created_at TEXT)"""
PROVIDER_USAGE = """CREATE TABLE IF NOT EXISTS provider_usage (
  provider TEXT, model TEXT, day TEXT, requests INTEGER, tokens INTEGER, PRIMARY KEY (provider, model, day))"""
HTTP_CACHE = """CREATE TABLE IF NOT EXISTS http_cache (
  key TEXT PRIMARY KEY, status INTEGER, headers_json TEXT, body TEXT, fetched_at TEXT)"""


# Steps that bring an older DB up to SCHEMA. New DBs get SCHEMA directly.
MIGRATIONS: list[tuple[int, list[str]]] = [
    (2, [
        "ALTER TABLE judgments ADD COLUMN baseline_hash TEXT",
        "CREATE INDEX IF NOT EXISTS idx_judgments_cache "
        "ON judgments(subject_id, question, baseline_id, baseline_hash, model_version)",
    ]),
    (3, [
        "ALTER TABLE roles ADD COLUMN review_reasons TEXT",
        "ALTER TABLE projects ADD COLUMN review_reasons TEXT",
        "ALTER TABLE evidence_items ADD COLUMN extracted_json TEXT",
    ]),
    (4, [
        "ALTER TABLE bullets ADD COLUMN source TEXT DEFAULT 'extracted'",
        "ALTER TABLE claims ADD COLUMN source TEXT DEFAULT 'extracted'",
        "ALTER TABLE certifications ADD COLUMN source TEXT DEFAULT 'extracted'",
        "ALTER TABLE evidence_items ADD COLUMN items_extracted INTEGER",
        "ALTER TABLE evidence_items ADD COLUMN items_dropped INTEGER",
        "CREATE TABLE IF NOT EXISTS dropped_items (id TEXT PRIMARY KEY, candidate_id TEXT, evidence_id TEXT, "
        "item_type TEXT, text TEXT, payload TEXT, parent_type TEXT, parent_id TEXT, reason TEXT, "
        "restored_id TEXT, created_at TEXT)",
    ]),
    (5, ["ALTER TABLE judgments ADD COLUMN used INTEGER DEFAULT 1"]),
    (6, ["ALTER TABLE judgments ADD COLUMN confidence REAL",
         "ALTER TABLE judgments ADD COLUMN score REAL"]),
    (7, ["ALTER TABLE judgments ADD COLUMN input_hash TEXT",
         "CREATE INDEX IF NOT EXISTS idx_judgments_input ON judgments(subject_id, question, input_hash)",
         "ALTER TABLE runs ADD COLUMN logic_version TEXT",
         "ALTER TABLE runs ADD COLUMN baseline_hash TEXT",
         "ALTER TABLE runs ADD COLUMN models_json TEXT"]),
    (8, ["ALTER TABLE gap_reports ADD COLUMN plan_json TEXT"]),
    (9, ["ALTER TABLE runs ADD COLUMN timings_json TEXT"]),
    (10, [DOCUMENT_TEXTS, HTTP_CACHE]),
    (11, ["ALTER TABLE runs ADD COLUMN plan_status TEXT"]),
    (12, [EXTRACTION_CACHE]),
    (13, [PROVIDER_USAGE]),
    (14, [TASKS, TASK_EVENTS, MEDIA_LINKS]),
    (15, ["ALTER TABLE candidates ADD COLUMN data_source TEXT", JOBS, APPLICATIONS, GENERATED_SUMMARIES]),
]


# --- Baselines --------------------------------------------------------------

def _seed_criterion_id(baseline_id: str, kind: str, name: str) -> str:
    # Deterministic so reseeding keeps criterion ids (and cached judgments) stable.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rightstaff:{baseline_id}:{kind}:{name}"))


def _insert_criteria(conn: sqlite3.Connection, baseline_id: str, criteria: list[BaselineCriterion]) -> None:
    conn.executemany(
        "INSERT INTO baseline_criteria (id, baseline_id, kind, name, description, aliases, priority) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (c.id or new_id(), baseline_id, c.kind, c.name, c.description, json.dumps(c.aliases), c.priority)
            for c in criteria
        ],
    )


def _row_to_baseline(row: sqlite3.Row, criteria_rows: list[sqlite3.Row]) -> Baseline:
    return Baseline(
        id=row["id"],
        title=row["title"],
        level=row["level"],
        required_years=row["required_years"],
        expected_reach=row["expected_reach"],
        scope_expectation=row["scope_expectation"],
        complexity_criteria=row["complexity_criteria"],
        description=row["description"],
        is_mock=bool(row["is_mock"]),
        updated_at=row["updated_at"],
        criteria=[
            BaselineCriterion(
                id=c["id"],
                kind=c["kind"],
                name=c["name"],
                description=c["description"],
                aliases=json.loads(c["aliases"] or "[]"),
                priority=c["priority"],
            )
            for c in criteria_rows
        ],
    )


def _criteria_rows(conn: sqlite3.Connection, baseline_id: str) -> list[sqlite3.Row]:
    # rowid preserves insertion order, matching the order the user entered.
    return conn.execute(
        "SELECT * FROM baseline_criteria WHERE baseline_id = ? ORDER BY rowid", (baseline_id,)
    ).fetchall()


def get_baseline(conn: sqlite3.Connection, baseline_id: str) -> Baseline | None:
    row = conn.execute("SELECT * FROM baselines WHERE id = ?", (baseline_id,)).fetchone()
    if row is None:
        return None
    return _row_to_baseline(row, _criteria_rows(conn, baseline_id))


def list_baselines(conn: sqlite3.Connection) -> list[Baseline]:
    rows = conn.execute("SELECT * FROM baselines ORDER BY title, id").fetchall()
    return [_row_to_baseline(r, _criteria_rows(conn, r["id"])) for r in rows]


def create_baseline(conn: sqlite3.Connection, baseline: Baseline) -> str:
    baseline = Baseline.model_validate(baseline.model_dump())
    baseline_id = baseline.id or new_id()
    with conn:
        conn.execute(
            "INSERT INTO baselines (id, title, level, required_years, expected_reach, scope_expectation, "
            "complexity_criteria, description, is_mock, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                baseline_id, baseline.title, baseline.level, baseline.required_years,
                baseline.expected_reach, baseline.scope_expectation, baseline.complexity_criteria,
                baseline.description, int(baseline.is_mock), now_iso(),
            ),
        )
        _insert_criteria(conn, baseline_id, baseline.criteria)
    return baseline_id


def update_baseline(conn: sqlite3.Connection, baseline: Baseline) -> None:
    """Replace a baseline's fields and its full criteria list.

    Criteria that keep their `id` keep it in the DB; new ones get a fresh id.
    """
    if not baseline.id:
        raise ValueError("update_baseline requires baseline.id")
    baseline = Baseline.model_validate(baseline.model_dump())
    with conn:
        cur = conn.execute(
            "UPDATE baselines SET title = ?, level = ?, required_years = ?, expected_reach = ?, "
            "scope_expectation = ?, complexity_criteria = ?, description = ?, is_mock = ?, updated_at = ? "
            "WHERE id = ?",
            (
                baseline.title, baseline.level, baseline.required_years, baseline.expected_reach,
                baseline.scope_expectation, baseline.complexity_criteria, baseline.description,
                int(baseline.is_mock), now_iso(), baseline.id,
            ),
        )
        if cur.rowcount == 0:
            raise KeyError(f"baseline {baseline.id!r} not found")
        conn.execute("DELETE FROM baseline_criteria WHERE baseline_id = ?", (baseline.id,))
        _insert_criteria(conn, baseline.id, baseline.criteria)


def delete_baseline(conn: sqlite3.Connection, baseline_id: str) -> bool:
    """Delete a baseline and (via cascade) its criteria. Runs/judgments/scores are kept as history."""
    with conn:
        cur = conn.execute("DELETE FROM baselines WHERE id = ?", (baseline_id,))
    return cur.rowcount > 0


# --- Runs -------------------------------------------------------------------

DELETED_BASELINE_LABEL = "(deleted)"


def create_run(conn: sqlite3.Connection, candidate_id: str, baseline_id: str, weights: dict, *,
               baseline_hash: str | None = None, models: dict | None = None) -> str:
    """A scoring run, with everything needed to explain or reproduce it later."""
    run_id = new_id()
    with conn:
        conn.execute(
            "INSERT INTO runs (id, candidate_id, baseline_id, weights_json, status, started_at, logic_version, "
            "baseline_hash, models_json) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)",
            (run_id, candidate_id, baseline_id, json.dumps(weights, sort_keys=True), now_iso(),
             config.LOGIC_VERSION, baseline_hash, json.dumps(models or {}, sort_keys=True)),
        )
    return run_id


def finish_run(conn: sqlite3.Connection, run_id: str, status: str = "done") -> None:
    with conn:
        conn.execute("UPDATE runs SET status = ?, finished_at = ? WHERE id = ?", (status, now_iso(), run_id))


def set_plan_status(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    with conn:
        conn.execute("UPDATE runs SET plan_status = ? WHERE id = ?", (status, run_id))


def save_run_timings(conn: sqlite3.Connection, run_id: str, timings: dict) -> None:
    """Per-run timing summary (specs/09 §1)."""
    with conn:
        conn.execute("UPDATE runs SET timings_json = ? WHERE id = ?", (json.dumps(timings), run_id))


def list_runs(conn: sqlite3.Connection, candidate_id: str | None = None) -> list[dict]:
    """Runs newest first, with `baseline_title` = "(deleted)" when the baseline is gone."""
    sql = (
        "SELECT r.*, COALESCE(b.title, ?) AS baseline_title, b.id IS NULL AS baseline_deleted "
        "FROM runs r LEFT JOIN baselines b ON b.id = r.baseline_id"
    )
    params: list = [DELETED_BASELINE_LABEL]
    if candidate_id is not None:
        sql += " WHERE r.candidate_id = ?"
        params.append(candidate_id)
    sql += " ORDER BY r.started_at DESC, r.rowid DESC"
    rows = conn.execute(sql, params).fetchall()
    return [
        {**dict(r), "baseline_deleted": bool(r["baseline_deleted"]), "weights": json.loads(r["weights_json"] or "{}"),
         "models": json.loads(r["models_json"] or "{}")}
        for r in rows
    ]


# --- Seed -------------------------------------------------------------------

def load_seed_file(path: str | Path | None = None) -> list[Baseline]:
    data = json.loads(Path(path or config.SEED_PATH).read_text())
    baselines = []
    for raw in data["baselines"]:
        raw = dict(raw)
        raw["criteria"] = [
            {**c, "id": _seed_criterion_id(raw["id"], c["kind"], c["name"])}
            for c in raw.get("criteria", [])
        ]
        baselines.append(Baseline.model_validate({"is_mock": True, **raw}))
    return baselines


def seed_baselines(conn: sqlite3.Connection, path: str | Path | None = None, reset: bool = False) -> list[str]:
    """Insert seed baselines that are not in the DB yet.

    With reset=True, seed baselines are restored to the file's contents
    (user-created baselines are left alone). Returns the ids written.
    """
    written = []
    for baseline in load_seed_file(path):
        exists = get_baseline(conn, baseline.id) is not None
        if exists and not reset:
            continue
        if exists:
            update_baseline(conn, baseline)
        else:
            create_baseline(conn, baseline)
        written.append(baseline.id)
    return written


def init_app_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Open/migrate the DB and seed baselines when there are none.

    Seeding only an empty table means a seed baseline the user deleted stays
    deleted across restarts; the "Reset seed baselines" button restores it.
    """
    conn = connect(path)
    if conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 0:
        seed_baselines(conn)
    return conn
