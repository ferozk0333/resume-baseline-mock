"""SQLite: connection, schema and migrations, baselines and runs (specs/03). `from core import db` gives all of it."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from core.db.baselines import (create_baseline, delete_baseline, get_baseline, list_baselines, load_seed_file,
                               seed_baselines, update_baseline)
from core.db.connection import connect, copy_db, new_id, now_iso
from core.db.migrations import MIGRATIONS, init_db
from core.db.runs import DELETED_BASELINE_LABEL, create_run, finish_run, list_runs, save_run_timings, set_plan_status
from core.db.schema import SCHEMA, SCHEMA_VERSION

__all__ = ["MIGRATIONS", "SCHEMA", "SCHEMA_VERSION", "DELETED_BASELINE_LABEL", "connect", "copy_db", "create_baseline",
           "create_run", "delete_baseline", "finish_run", "get_baseline", "init_app_db", "init_db", "list_baselines",
           "list_runs", "load_seed_file", "new_id", "now_iso", "save_run_timings", "seed_baselines", "set_plan_status",
           "update_baseline"]


def init_app_db(path: str | Path | None = None) -> sqlite3.Connection:
    """Open/migrate the DB and seed baselines when there are none.

    Seeding only an empty table means a seed baseline the user deleted stays
    deleted across restarts; the "Reset seed baselines" button restores it.
    """
    conn = connect(path)
    if conn.execute("SELECT COUNT(*) FROM baselines").fetchone()[0] == 0:
        seed_baselines(conn)
    return conn
