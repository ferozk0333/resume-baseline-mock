"""Scoring runs: created with what is needed to explain or reproduce them, finished, listed."""
from __future__ import annotations

import json
import sqlite3

from core import config
from core.db.connection import new_id, now_iso


DELETED_BASELINE_LABEL = "(deleted)"


def create_run(conn: sqlite3.Connection, candidate_id: str, baseline_id: str, weights: dict, *,
               baseline_hash: str | None = None, models: dict | None = None, scored_on: str | None = None) -> str:
    """A scoring run, with everything needed to explain or reproduce it later (scored_on: the scoring date)."""
    run_id = new_id()
    with conn:
        conn.execute(
            "INSERT INTO runs (id, candidate_id, baseline_id, weights_json, status, started_at, logic_version, "
            "baseline_hash, models_json, scored_on) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)",
            (run_id, candidate_id, baseline_id, json.dumps(weights, sort_keys=True), now_iso(),
             config.LOGIC_VERSION, baseline_hash, json.dumps(models or {}, sort_keys=True),
             scored_on or config.today().isoformat()),
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
