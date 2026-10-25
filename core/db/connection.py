"""Opening a database (migrated on open), ids and timestamps, and a consistent copy of a database in use."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core import config
from core.db.migrations import init_db


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def copy_db(src, dst) -> None:
    """A consistent copy of a database in use (a running server's recent writes may still be in its WAL file)."""
    Path(dst).unlink(missing_ok=True)
    a, b = sqlite3.connect(str(src)), sqlite3.connect(str(dst))
    with b:
        a.backup(b)
    a.close()
    b.close()


def new_id() -> str:
    return str(uuid.uuid4())


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the DB, enable foreign keys (needed for criteria cascade) and migrate."""
    conn = sqlite3.connect(str(path or config.DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # parallel graph nodes read while another writes
    init_db(conn)
    return conn
