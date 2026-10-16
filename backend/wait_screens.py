"""The two onboarding waits (specs/17, wait screens): reading the documents and scoring the roles.

Both views are read from what is already saved; no model is called and nothing new is tracked:
- Reading: the sources the candidate added (recorded on the reading task), their evidence rows (read, failed
  with the reader's message, validated) and, once validated, the roles, projects and skills found in each.
- Scoring: each target role's run since the profile was confirmed, and how many of each dimension's checks are
  saved. Candidate-facing text never shows those counts: only a fill per dimension and a status per role.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from pathlib import Path

import yaml

from backend import candidate as cx
from core import config

TIPS_FILE = Path(__file__).resolve().parent.parent / "config" / "wait_tips.yaml"
SOURCES = {"resume": "Resume", "linkedin_pdf": "LinkedIn", "github": "GitHub", "portfolio": "Portfolio"}
EVIDENCE_TYPES = {"resume": ("resume",), "linkedin_pdf": ("linkedin_pdf",), "github": ("github_repo",),
                  "portfolio": ("portfolio",)}
WARNING_PREFIX = {"resume": "Resume:", "linkedin_pdf": "LinkedIn PDF:", "github": "GitHub:", "portfolio": "Portfolio:"}


def tips() -> dict:
    data = yaml.safe_load(TIPS_FILE.read_text())
    return {k: [{"title": t["title"], "text": t["text"], "icon": t.get("icon")} for t in data.get(k, [])]
            for k in ("reading", "scoring")}


def _seconds(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()


def _typical_minutes(values: list[float], factor: int = 1) -> int | None:
    """"Usually takes about N minutes": the median of recorded durations, only when it rounds to a minute or more."""
    if len(values) < 3:
        return None
    m = round(statistics.median(values) * factor / 60)
    return m if m >= 1 else None


# --- Reading -----------------------------------------------------------------------------------------------------

def added_sources(onboard_form: dict) -> list[str]:
    return [k for k in SOURCES if k == "resume" or onboard_form.get(k)]


def _counts(conn, candidate_id: str, types: tuple[str, ...]) -> dict:
    q = ",".join("?" * len(types))

    def n(table: str) -> int:
        return conn.execute(f"SELECT COUNT(*) FROM {table} t JOIN evidence_items e ON e.id = t.evidence_id "
                            f"WHERE t.candidate_id = ? AND e.type IN ({q})", (candidate_id, *types)).fetchone()[0]
    return {"roles": n("roles"), "projects": n("projects"), "skills": n("claims")}


def reading_view(conn, candidate_id: str, task: dict | None) -> dict:
    if task is None:
        return {"sources": [], "stage": "waiting", "stage_label": "Waiting to start", "percent": 0, "done": False,
                "error": None, "typical_minutes": None, "announce": []}
    result = task.get("result") or {}
    added = result.get("sources") or ["resume"]
    warnings = result.get("warnings") or []
    step = task.get("step") or "queued"
    finished, errored = task["status"] == "done", task["status"] == "error"
    rows = {t: [dict(r) for r in conn.execute(
        f"SELECT type, status, error FROM evidence_items WHERE candidate_id = ? AND type IN "
        f"({','.join('?' * len(EVIDENCE_TYPES[t]))}) AND created_at >= ?",
        (candidate_id, *EVIDENCE_TYPES[t], task["created_at"]))] for t in added}
    out, ingesting = [], None
    for t in added:
        r = rows[t]
        warn = next((w[len(WARNING_PREFIX[t]):].strip() for w in warnings if w.startswith(WARNING_PREFIX[t])), None)
        source = {"type": t, "label": SOURCES[t], "status": "waiting", "error": None, "counts": None}
        doc_failed = t != "github" and any(x["status"] == "failed" for x in r)
        if doc_failed:
            source.update(status="failed", error=next(x["error"] for x in r if x["status"] == "failed"))
        elif finished and r:
            source.update(status="done", counts=_counts(conn, candidate_id, EVIDENCE_TYPES[t]))
        elif finished or errored:
            source.update(status="failed", error=warn or (task.get("error") if errored else "Nothing could be read."))
        elif r or step in ("extract", "validate"):
            source["status"] = "reading"
        elif step in ("queued", "ingest") and ingesting is None:
            source["status"], ingesting = "reading", t
        out.append(source)
    labels = {"resume": "Reading your resume", "linkedin_pdf": "Reading your LinkedIn profile",
              "github": "Reading your GitHub and portfolio", "portfolio": "Reading your GitHub and portfolio"}
    if finished:
        stage, label, pct = "done", "Your documents are read", 100
    elif errored:
        stage, label, pct = "error", "We couldn't read your documents", 100
    elif step == "validate":
        stage, label, pct = "check", "Checking everything against your documents", 90
    elif step == "extract":
        stage, label, pct = "find", "Finding your roles, projects and skills", 45
    else:
        current = ingesting or next((s["type"] for s in out if s["status"] == "reading"), "resume")
        stage, label = "read", labels[current]
        pct = 5 + 30 * sum(1 for t in added if rows[t]) / max(1, len(added))
    durations = [d for r in conn.execute("SELECT created_at, updated_at FROM tasks WHERE kind = 'onboard' AND "
                                         "status = 'done' ORDER BY created_at DESC LIMIT 20")
                 if (d := _seconds(r[0], r[1])) is not None]
    return {"sources": out, "stage": stage, "stage_label": label, "percent": round(pct), "done": finished,
            "error": task.get("error") if errored else None,
            "typical_minutes": None if finished else _typical_minutes(durations)}


# --- Scoring -----------------------------------------------------------------------------------------------------

def scoring_view(conn, candidate_id: str, task: dict | None) -> dict:
    """One row per target role (primary first): status and a 0–1 fill per dimension."""
    ob = cx.onboarding(conn, candidate_id)
    since = ob.get("confirmed_at") or ""
    running = bool(task and task["status"] == "running")
    failed = ((task or {}).get("result") or {}).get("failed") or {}
    in_task = set(((task or {}).get("result") or {}).get("roles") or [])     # a Retry scores only some roles
    rows = []
    for t in cx.targets(conn, candidate_id):
        ids = cx._role_baseline_ids(conn, t["baseline_id"])
        run = conn.execute(f"SELECT id, status, started_at, finished_at FROM runs WHERE candidate_id = ? AND "
                           f"baseline_id IN ({','.join('?' * len(ids))}) AND started_at >= ? "
                           f"ORDER BY started_at DESC, rowid DESC LIMIT 1", (candidate_id, *ids, since)).fetchone()
        dims = {d: 0.0 for d in config.SUBCATEGORIES}
        if run:
            saved = {r[0] for r in conn.execute("SELECT DISTINCT subcategory FROM scores WHERE run_id = ?", (run["id"],))}
            dims = {d: round(len(saved & set(subs)) / len(subs), 3) for d, subs in config.SUBCATEGORIES.items()}
        if run and run["status"] == "done":
            status, dims = "ready", {d: 1.0 for d in dims}
        elif t["baseline_id"] in failed and not (run and run["status"] == "running" and running):
            status = "failed"
        elif run and run["status"] == "running":
            status = "scoring" if running else "failed"
        else:
            status = "waiting" if running and (not in_task or t["baseline_id"] in in_task) else "failed"
        rows.append({"baseline_id": t["baseline_id"], "title": t["title"], "is_primary": t["is_primary"],
                     "status": status, "dimensions": dims})
    n = max(1, len(rows))
    overall = sum(1.0 if r["status"] == "ready" else sum(r["dimensions"].values()) / 4 for r in rows) / n
    ready = [r["title"] for r in rows if r["status"] == "ready"]
    all_ready = bool(rows) and len(ready) == len(rows)
    line = ("Your scores are ready" if all_ready else
            f"Scoring your profile for {len(rows)} role{'s' if len(rows) != 1 else ''}"
            + (f" · {', '.join(ready)} ready" if ready else ""))
    runs = [d for r in conn.execute("SELECT started_at, finished_at FROM runs WHERE status = 'done' AND "
                                    "models_json LIKE '%\"judge_kind\": \"real\"%' ORDER BY started_at DESC LIMIT 20")
            if (d := _seconds(r[0], r[1])) is not None]
    return {"roles": rows, "overall": round(overall, 3), "line": line, "all_ready": all_ready, "running": running,
            "typical_minutes": None if all_ready else _typical_minutes(runs, len(rows))}
