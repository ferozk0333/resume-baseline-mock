"""Seeded demo jobs (specs/17 §9): about 9 fictional jobs, 3 per role, so the candidate Home has something to rank.

Each job's own baseline is written directly from its role baseline plus small listed differences (added or dropped
criteria, a preferred one made required, other years). No model calls. Posting dates and recruiter activity are
seeded relative to the seeding time and marked as demo data (jobs.data_source = 'demo', job_events.demo = 1).
Seeding is idempotent: a job that already exists (same fixed id) is left alone.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import config, db
from core.db import new_id
from core.models import Baseline, BaselineCriterion

SEED_FILE = Path(__file__).resolve().parent.parent / "seed" / "demo_jobs.json"
LEGACY_DEMO_COMPANIES = ("Northwind Analytics", "Quarry Labs", "Fernway Health", "Brightwater Systems")


def job_id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"rightstaff:demo-job:{key}"))


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def job_baseline(role: Baseline, spec: dict, owner: str) -> Baseline:
    """The role baseline with the job's differences. Criterion ids are new (the job owns its baseline)."""
    drop = {n.casefold() for n in spec.get("drop", [])}
    promote = {n.casefold() for n in spec.get("promote", [])}
    unknown = (drop | promote) - {c.name.casefold() for c in role.criteria}
    if unknown:
        raise ValueError(f"{spec['key']}: not in {role.title}: {sorted(unknown)}")
    criteria = [BaselineCriterion(kind=c.kind, name=c.name, description=c.description, aliases=list(c.aliases),
                                  priority="required" if c.name.casefold() in promote else c.priority)
                for c in role.criteria if c.name.casefold() not in drop]
    criteria += [BaselineCriterion(kind=a["kind"], name=a["name"], aliases=a.get("aliases", []), priority=a["priority"])
                 for a in spec.get("add", [])]
    return Baseline(id=f"baseline-demo-{spec['key']}", title=spec["title"], level=role.level,
                    required_years=float(spec.get("required_years", role.required_years)),
                    expected_reach=role.expected_reach, scope_expectation=role.scope_expectation,
                    complexity_criteria=role.complexity_criteria, description=spec["short_description"],
                    is_mock=True, criteria=criteria)


def seed(conn, now: datetime | None = None, path: Path | None = None) -> dict:
    """Insert the demo jobs that are not there yet, and mark the earlier demo jobs (the 3 seeded jobs and
    Brightwater) as demo data. Returns counts."""
    from backend import workflows as wf
    now = now or datetime.now(timezone.utc)
    data = json.loads((path or SEED_FILE).read_text())["jobs"]
    added = 0
    for spec in data:
        jid = job_id(spec["key"])
        if conn.execute("SELECT 1 FROM jobs WHERE id = ?", (jid,)).fetchone():
            continue
        role = db.get_baseline(conn, spec["role"])
        if role is None:
            raise ValueError(f"role baseline {spec['role']} is missing")
        b = job_baseline(role, spec, jid)
        if db.get_baseline(conn, b.id) is None:
            db.create_baseline(conn, b)
        baseline = db.get_baseline(conn, b.id)
        by_name = {c.name.casefold(): c.id for c in baseline.criteria}
        musts = [by_name[n.casefold()] for n in spec["must_haves"]]
        posted = now - timedelta(days=spec["posted_days_ago"])
        sal = spec.get("salary")
        details = {"title": spec["title"], "company": spec["company"], "department": spec.get("department", ""),
                   "location": spec["location"], "workplace": spec["workplace"], "employment_type": "Full-time",
                   "level": role.level, "min_years": baseline.required_years,
                   "short_description": spec["short_description"], "responsibilities": spec["responsibilities"],
                   "salary_min": sal[0] if sal else None, "salary_max": sal[1] if sal else None,
                   "salary_currency": sal[2] if sal else None}
        with conn:
            conn.execute("UPDATE baselines SET owner_job_id = ? WHERE id = ?", (jid, baseline.id))
            conn.execute("INSERT INTO jobs (id, baseline_id, title, company, weights_json, must_haves_json, preset, "
                         "created_at, status, details_json, updated_at, data_source, role_baseline_id) "
                         "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, 'open', ?, ?, 'demo', ?)",
                         (jid, baseline.id, spec["title"], spec["company"], json.dumps(config.default_weights()),
                          json.dumps(musts), _iso(posted), json.dumps(details), _iso(posted), spec["role"]))
            for kind, days in (("panel_opened", spec.get("activity_days_ago")),
                               ("stage_moved", spec.get("stage_moved_days_ago"))):
                if days is not None:
                    conn.execute("INSERT INTO job_events (id, job_id, application_id, kind, demo, created_at) "
                                 "VALUES (?, ?, NULL, ?, 1, ?)", (new_id(), jid, kind, _iso(now - timedelta(days=days))))
        wf.stages(conn, jid)                                  # default interview plan, like a published job
        added += 1
    with conn:
        marked = conn.execute(f"UPDATE jobs SET data_source = 'demo' WHERE data_source IS NULL AND company IN "
                              f"({','.join('?' * len(LEGACY_DEMO_COMPANIES))}) AND status != 'draft'",
                              LEGACY_DEMO_COMPANIES).rowcount
    return {"added": added, "marked_demo": marked, "total_demo": conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE data_source = 'demo'").fetchone()[0]}
