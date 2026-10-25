"""Baseline role profiles: create, read, update, delete, and the seed file."""
from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from core import config
from core.db.connection import new_id, now_iso
from core.models import Baseline, BaselineCriterion


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


def list_baselines(conn: sqlite3.Connection, include_owned: bool = False) -> list[Baseline]:
    """Role baselines. A recruiter job's own baseline (specs/14) is listed only with include_owned."""
    rows = conn.execute("SELECT * FROM baselines" + ("" if include_owned else
                        " WHERE owner_job_id IS NULL AND market_version_id IS NULL") +
                        " ORDER BY title, id").fetchall()
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
