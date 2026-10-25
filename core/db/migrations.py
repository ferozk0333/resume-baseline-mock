"""Schema migrations: a new database gets the latest schema; an older one steps forward version by version."""
from __future__ import annotations

import sqlite3

from core.db.schema import SCHEMA, SCHEMA_VERSION


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


PARSER_RUNS = """CREATE TABLE IF NOT EXISTS parser_runs (id TEXT PRIMARY KEY, candidate_id TEXT, evidence_id TEXT,
  doc_type TEXT, mode TEXT, outcome TEXT, reasons_json TEXT, notes_json TEXT, items_json TEXT, comparison_json TEXT,
  seconds REAL, jev_requests INTEGER, jev_questions INTEGER, llm_calls INTEGER, created_at TEXT)"""
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
INTERVIEW_STAGES = """CREATE TABLE IF NOT EXISTS interview_stages (
  id TEXT PRIMARY KEY, job_id TEXT, name TEXT, position INTEGER, focus_json TEXT, terminal INTEGER DEFAULT 0)"""
ACTIVITY = """CREATE TABLE IF NOT EXISTS activity (
  id TEXT PRIMARY KEY, application_id TEXT, job_id TEXT, kind TEXT, summary TEXT, detail_json TEXT, created_at TEXT)"""
EMAILS = """CREATE TABLE IF NOT EXISTS emails (
  id TEXT PRIMARY KEY, application_id TEXT, job_id TEXT, kind TEXT, stage_id TEXT, batch_id TEXT, status TEXT,
  intended_to TEXT, actual_to TEXT, subject TEXT, body TEXT, model TEXT, provider TEXT, reasons_json TEXT,
  checks_json TEXT, error TEXT, created_at TEXT, updated_at TEXT, sent_at TEXT)"""
MARKET_TABLES = [
    "CREATE TABLE IF NOT EXISTS market_postings (id TEXT PRIMARY KEY, role TEXT, company TEXT, tier TEXT, source TEXT, "
    "source_id TEXT, external_id TEXT, source_url TEXT, title TEXT, location TEXT, level TEXT, level_method TEXT, "
    "text_hash TEXT, text TEXT, raw_file TEXT, posted_at TEXT, first_seen TEXT, last_seen TEXT, status TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS market_extractions (text_hash TEXT PRIMARY KEY, model TEXT, extract_json TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS market_versions (id TEXT PRIMARY KEY, role TEXT, level TEXT, version INTEGER, "
    "window_start TEXT, window_end TEXT, postings INTEGER, companies INTEGER, content_json TEXT, content_hash TEXT, "
    "status TEXT, insufficient INTEGER, test_version INTEGER, baseline_id TEXT, summary_method TEXT, created_at TEXT, "
    "published_at TEXT)",
    "CREATE TABLE IF NOT EXISTS market_suggestions (id TEXT PRIMARY KEY, name TEXT, canonical TEXT, method TEXT, "
    "status TEXT, created_at TEXT, decided_at TEXT)",
    "CREATE TABLE IF NOT EXISTS market_settings (key TEXT PRIMARY KEY, value TEXT)",
]
CANDIDATE_TABLES = [
    "CREATE TABLE IF NOT EXISTS candidate_targets (candidate_id TEXT, baseline_id TEXT, is_primary INTEGER, "
    "position INTEGER, added_at TEXT, PRIMARY KEY (candidate_id, baseline_id))",
    "CREATE TABLE IF NOT EXISTS candidate_onboarding (candidate_id TEXT PRIMARY KEY, step TEXT, read_task_id TEXT, "
    "score_task_id TEXT, confirmed_at TEXT, finished_at TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS candidate_application_details (candidate_id TEXT PRIMARY KEY, work_auth_json TEXT, "
    "questions_json TEXT, eeo_json TEXT, saved_at TEXT, dismissed_at TEXT, updated_at TEXT)",
]
WORKFLOW_TABLES = [
    "CREATE TABLE IF NOT EXISTS workflows (application_id TEXT PRIMARY KEY, job_id TEXT, thread_id TEXT, token TEXT, "
    "stage TEXT, round INTEGER, phase TEXT, step TEXT, title TEXT, payload_json TEXT, status TEXT, paused_status TEXT, "
    "next_due TEXT, step_since TEXT, outcome TEXT, created_at TEXT, updated_at TEXT)",
    "CREATE TABLE IF NOT EXISTS workflow_events (id TEXT PRIMARY KEY, application_id TEXT, type TEXT, "
    "payload_json TEXT, actor TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, application_id TEXT, direction TEXT, subject TEXT, "
    "body TEXT, token TEXT, message_id TEXT, in_reply_to TEXT, intended_to TEXT, actual_to TEXT, from_addr TEXT, "
    "draft_id TEXT, status TEXT, source TEXT, reading_json TEXT, sent_at TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS interviews (id TEXT PRIMARY KEY, application_id TEXT, job_id TEXT, stage TEXT, "
    "start TEXT, end TEXT, timezone TEXT, ics_uid TEXT, status TEXT, created_at TEXT)",
    "CREATE TABLE IF NOT EXISTS scorecards (id TEXT PRIMARY KEY, application_id TEXT, interview_id TEXT, stage TEXT, "
    "interviewer TEXT, ratings_json TEXT, notes TEXT, recommendation TEXT, created_at TEXT)",
]
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
    (16, ["ALTER TABLE candidates ADD COLUMN email TEXT", "ALTER TABLE jobs ADD COLUMN settings_json TEXT",
          "ALTER TABLE applications ADD COLUMN stage_id TEXT", INTERVIEW_STAGES, ACTIVITY, EMAILS]),
    (17, ["ALTER TABLE candidates ADD COLUMN linkedin_url TEXT"]),
    (18, [*(f"ALTER TABLE emails ADD COLUMN {c} TEXT" for c in ("token", "message_id", "in_reply_to",
                                                                "attachments_json", "interview_id")),
          *WORKFLOW_TABLES]),
    (19, ["ALTER TABLE baselines ADD COLUMN owner_job_id TEXT", "ALTER TABLE jobs ADD COLUMN status TEXT DEFAULT 'open'",
          *(f"ALTER TABLE jobs ADD COLUMN {c} TEXT" for c in ("details_json", "jd_text", "jd_file", "draft_json",
                                                              "updated_at"))]),
    (20, ["ALTER TABLE baselines ADD COLUMN market_version_id TEXT", *MARKET_TABLES]),
    (21, CANDIDATE_TABLES),
    (22, ["ALTER TABLE jobs ADD COLUMN data_source TEXT", "ALTER TABLE jobs ADD COLUMN role_baseline_id TEXT",
          "CREATE TABLE IF NOT EXISTS job_events (id TEXT PRIMARY KEY, job_id TEXT, application_id TEXT, kind TEXT, "
          "demo INTEGER DEFAULT 0, created_at TEXT)",
          "CREATE TABLE IF NOT EXISTS candidate_job_prefs (candidate_id TEXT, job_id TEXT, saved INTEGER DEFAULT 0, "
          "hidden INTEGER DEFAULT 0, reason TEXT, updated_at TEXT, PRIMARY KEY (candidate_id, job_id))"]),
    (23, ["ALTER TABLE jobs ADD COLUMN published_at TEXT",
          "UPDATE jobs SET published_at = created_at WHERE status IS NOT 'draft'",
          "CREATE TABLE IF NOT EXISTS candidate_plan_done (candidate_id TEXT, baseline_id TEXT, action_key TEXT, "
          "title TEXT, done_at TEXT, PRIMARY KEY (candidate_id, baseline_id, action_key))"]),
    (24, [*(f"ALTER TABLE {t} ADD COLUMN method TEXT" for t in ("roles", "projects", "bullets", "claims",
                                                                 "certifications")),
          "ALTER TABLE document_texts ADD COLUMN hidden_json TEXT", PARSER_RUNS]),
    (25, ["CREATE TABLE IF NOT EXISTS deleted_items (id TEXT PRIMARY KEY, candidate_id TEXT, item_type TEXT, key TEXT, "
          "label TEXT, payload TEXT, created_at TEXT)",
          "CREATE TABLE IF NOT EXISTS suppressed_items (id TEXT PRIMARY KEY, candidate_id TEXT, evidence_id TEXT, "
          "item_type TEXT, key TEXT, label TEXT, payload TEXT, restored INTEGER DEFAULT 0, created_at TEXT)"]),
    (26, ["CREATE TABLE IF NOT EXISTS prompt_guard_cache (text_hash TEXT, model TEXT, score REAL, created_at TEXT, "
          "PRIMARY KEY (text_hash, model))",
          "CREATE TABLE IF NOT EXISTS prompt_guard_flags (id TEXT PRIMARY KEY, candidate_id TEXT, evidence_id TEXT, "
          "source TEXT, chunk_index INTEGER, chunk TEXT, score REAL, hidden INTEGER DEFAULT 0, model TEXT, "
          "created_at TEXT)"]),
    (27, ["ALTER TABLE roles ADD COLUMN location TEXT",
          *(f"ALTER TABLE {t} ADD COLUMN sources TEXT" for t in ("roles", "projects", "claims", "certifications")),
          "ALTER TABLE projects ADD COLUMN details TEXT", "ALTER TABLE claims ADD COLUMN details TEXT",
          "ALTER TABLE claims ADD COLUMN skill_group TEXT",
          "CREATE TABLE IF NOT EXISTS extra_items (id TEXT PRIMARY KEY, candidate_id TEXT, evidence_id TEXT, type TEXT, "
          "fields_json TEXT, quote TEXT, source TEXT DEFAULT 'extracted', position INTEGER, created_at TEXT)"]),
    (28, ["ALTER TABLE runs ADD COLUMN scored_on TEXT"]),
    (29, ["""CREATE TABLE IF NOT EXISTS weight_suggestions (   -- recruiter weight assistant (specs/21 §6): every suggestion
  id TEXT PRIMARY KEY, job_id TEXT, recruiter TEXT, text TEXT, model TEXT, cache_key TEXT, cached INTEGER DEFAULT 0,
  weights_json TEXT,              -- validated, bounded, normalized and rounded
  reasons_json TEXT, summary TEXT, ignored_json TEXT,
  current_weights_json TEXT,      -- the job's weights when it was suggested
  applied INTEGER DEFAULT 0, applied_at TEXT, previous_weights_json TEXT, undone_at TEXT, created_at TEXT
)""",
          "CREATE INDEX IF NOT EXISTS idx_weight_suggestions ON weight_suggestions(job_id, created_at)"]),
]
