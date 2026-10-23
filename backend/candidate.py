"""Candidate experience (specs/17): first-run onboarding state, target roles, baseline score summaries and the
"fill once" application details.

Two scores are never mixed: this module only deals with the Baseline score (EEAT against a role baseline). Code
only: no model is called here.

Application details (work authorization, frequent questions, voluntary self-identification) are stored for the
candidate alone: nothing here is ever sent to a model, shown to a recruiter, or read by scoring, pools,
percentiles, peer signals or ranking (tests/test_candidate.py checks all of these).
"""
from __future__ import annotations

import json
from datetime import date

from core import config, db
from core.db import now_iso

STEPS = ("proof", "roles", "review", "scores", "details", "done")
MAX_TARGETS = 3
THIN_SCORE = 10            # below this, the results say most of the score is missing evidence (no scoring change)

# --- Onboarding progress --------------------------------------------------------------------------------------


def onboarding(conn, candidate_id: str) -> dict:
    """Where the candidate is in the first run. A candidate created before specs/17 (or in Demo tools) with a
    scored role baseline counts as set up."""
    row = conn.execute("SELECT * FROM candidate_onboarding WHERE candidate_id = ?", (candidate_id,)).fetchone()
    if row:
        return dict(row)
    scored = conn.execute("SELECT 1 FROM runs WHERE candidate_id = ? AND status = 'done' LIMIT 1",
                          (candidate_id,)).fetchone()
    return {"candidate_id": candidate_id, "step": "done" if scored else "proof", "read_task_id": None,
            "score_task_id": None, "confirmed_at": None, "finished_at": None, "updated_at": None}


def set_step(conn, candidate_id: str, step: str, **fields) -> dict:
    if step not in STEPS:
        raise ValueError(f"step must be one of {STEPS}")
    cur = onboarding(conn, candidate_id)
    row = {**cur, **fields, "step": step, "updated_at": now_iso()}
    if step == "done" and not row.get("finished_at"):
        row["finished_at"] = now_iso()
    with conn:
        conn.execute("INSERT OR REPLACE INTO candidate_onboarding (candidate_id, step, read_task_id, score_task_id, "
                     "confirmed_at, finished_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (candidate_id, row["step"], row["read_task_id"], row["score_task_id"], row["confirmed_at"],
                      row["finished_at"], row["updated_at"]))
    return onboarding(conn, candidate_id)


# --- Target roles ---------------------------------------------------------------------------------------------

def role_baselines(conn) -> dict[str, str]:
    """The role baselines a candidate can target (id → title): never a job's own or a Market baseline."""
    return {b.id: b.title for b in db.list_baselines(conn)}


def targets(conn, candidate_id: str) -> list[dict]:
    titles = role_baselines(conn)
    rows = conn.execute("SELECT baseline_id, is_primary, position FROM candidate_targets WHERE candidate_id = ? "
                        "ORDER BY is_primary DESC, position", (candidate_id,)).fetchall()
    return [{"baseline_id": r[0], "title": titles.get(r[0], "(deleted role)"), "is_primary": bool(r[1])}
            for r in rows]


def primary(conn, candidate_id: str) -> dict | None:
    return next(iter(targets(conn, candidate_id)), None)


def _write_targets(conn, candidate_id: str, ids: list[str], primary_id: str) -> None:
    with conn:
        conn.execute("DELETE FROM candidate_targets WHERE candidate_id = ?", (candidate_id,))
        conn.executemany("INSERT INTO candidate_targets (candidate_id, baseline_id, is_primary, position, added_at) "
                         "VALUES (?, ?, ?, ?, ?)",
                         [(candidate_id, b, int(b == primary_id), i, now_iso()) for i, b in enumerate(ids)])
        title = role_baselines(conn).get(primary_id)
        conn.execute("UPDATE candidates SET target_role = ? WHERE id = ?", (title, candidate_id))


def set_targets(conn, candidate_id: str, baseline_ids: list[str], primary_id: str | None = None) -> list[dict]:
    """1–3 role baselines; one primary (default: the first picked)."""
    ids = list(dict.fromkeys(baseline_ids))
    if not 1 <= len(ids) <= MAX_TARGETS:
        raise ValueError(f"Pick 1 to {MAX_TARGETS} roles.")
    known = role_baselines(conn)
    unknown = [b for b in ids if b not in known]
    if unknown:
        raise ValueError(f"Not a role baseline: {', '.join(unknown)}")
    primary_id = primary_id or ids[0]
    if primary_id not in ids:
        raise ValueError("The primary role must be one of the picked roles.")
    _write_targets(conn, candidate_id, ids, primary_id)
    return targets(conn, candidate_id)


def add_target(conn, candidate_id: str, baseline_id: str) -> list[dict]:
    cur = [t["baseline_id"] for t in targets(conn, candidate_id)]
    if baseline_id in cur:
        return targets(conn, candidate_id)
    if len(cur) >= MAX_TARGETS:
        raise ValueError(f"You can target up to {MAX_TARGETS} roles. Remove one first.")
    return set_targets(conn, candidate_id, [*cur, baseline_id], cur[0] if cur else baseline_id)


def remove_target(conn, candidate_id: str, baseline_id: str) -> list[dict]:
    """Runs and plans are kept (history); the role is no longer shown. The last role can't be removed."""
    cur = targets(conn, candidate_id)
    rest = [t["baseline_id"] for t in cur if t["baseline_id"] != baseline_id]
    if not rest:
        raise ValueError("Keep at least one target role.")
    was_primary = next((t["is_primary"] for t in cur if t["baseline_id"] == baseline_id), False)
    prim = rest[0] if was_primary else next(t["baseline_id"] for t in cur if t["is_primary"])
    return set_targets(conn, candidate_id, rest, prim)


def set_primary(conn, candidate_id: str, baseline_id: str) -> list[dict]:
    ids = [t["baseline_id"] for t in targets(conn, candidate_id)]
    if baseline_id not in ids:
        raise ValueError("Add this role first.")
    return set_targets(conn, candidate_id, ids, baseline_id)


# --- Baseline scores per role ---------------------------------------------------------------------------------

def _role_baseline_ids(conn, baseline_id: str) -> list[str]:
    """A role baseline and its Market versions (a candidate may switch the Score page to Market)."""
    from market import sources
    role = sources.role_for_baseline(sources.load(), baseline_id)
    if role is None:
        return [baseline_id]
    rows = conn.execute("SELECT v.baseline_id FROM market_versions v WHERE v.role = ? AND v.baseline_id IS NOT NULL",
                        (role.key,)).fetchall()
    return [baseline_id, *(r[0] for r in rows)]


def role_run(conn, candidate_id: str, baseline_id: str, *, finished: bool = True) -> dict | None:
    """The candidate's latest run for this role (Standard or Market baseline). Job runs (Apply) are never
    included: a job has its own baseline."""
    ids = _role_baseline_ids(conn, baseline_id)
    status = "AND status = 'done' " if finished else ""
    row = conn.execute(f"SELECT * FROM runs WHERE candidate_id = ? AND baseline_id IN ({','.join('?' * len(ids))}) "
                       f"{status}ORDER BY started_at DESC, rowid DESC LIMIT 1", (candidate_id, *ids)).fetchone()
    return dict(row) if row else None


def score_card(conn, candidate_id: str, target: dict) -> dict:
    """One role's results card: baseline score, percentile (or why not), 4 dimensions, top 3 actions.
    Code only; a plan that was deferred is not written here (it is written when the plan is first opened)."""
    from graph.pipeline import current_plan, rescore, run_gaps
    from scoring import benchmarks
    from scoring.plan import TYPE_LABELS
    out = {**target, "run_id": None, "final": None, "dimensions": {}, "percentile": None, "plan_status": None,
           "actions": [], "gaps": [], "thin": False, "baseline_source": "standard"}
    run = role_run(conn, candidate_id, target["baseline_id"])
    if run is None:
        pending = role_run(conn, candidate_id, target["baseline_id"], finished=False)
        return {**out, "run_id": pending["id"] if pending else None, "status": "running" if pending else "not_scored"}
    weights = json.loads(run["weights_json"])
    agg = rescore(conn, run["id"], weights)
    pct = benchmarks.for_run(conn, run["id"])["percentiles"]
    final = pct.get("final") if pct.get("shown") else None
    plan = current_plan(conn, run["id"], weights)
    done = done_actions(conn, candidate_id, target["baseline_id"])
    actions = [{"key": action_key(p.action.title), "title": p.action.title, "type": p.action.type,
                "type_label": TYPE_LABELS.get(p.action.type, p.action.type), "points": round(p.points, 1),
                "points_text": p.points_text, "covers_criteria": p.action.covers_criteria,
                "done": action_key(p.action.title) in done}
               for p in (plan.actions if plan and not plan.error else [])]
    ranked = sorted(actions, key=lambda a: -a["points"])
    gaps = [{"label": g.label, "dimension": g.dimension, "gain_text": g.gain_text, "status": g.status}
            for g in run_gaps(conn, run["id"], weights)]
    status = (run.get("plan_status") or "none") if plan is None else ("failed" if plan.error else "ready")
    return {**out, "run_id": run["id"], "status": "scored", "final": agg.final, "dimensions": agg.dimensions,
            "percentile": {"shown": bool(final), "top": None if not final else max(5, 5 * round((100 - final["percentile"]) / 5)),
                           "value": final["percentile"] if final else None, "pool_size": pct.get("pool_size"),
                           "min_pool": pct.get("min_pool", config.MIN_POOL)},
            "plan_status": status, "actions": ranked[:3], "gaps": gaps[:3],
            "next_action": next((a for a in ranked if not a["done"]), None),
            "done_actions": [a for a in ranked if a["done"]],
            "thin": agg.final is not None and agg.final < THIN_SCORE,
            "baseline_source": "standard" if run["baseline_id"] == target["baseline_id"] else "market"}


# --- "Mark done" on plan actions (changes no score) --------------------------------------------------------------

def action_key(title: str) -> str:
    import hashlib
    return hashlib.sha1(" ".join(title.casefold().split()).encode()).hexdigest()[:12]


def done_actions(conn, candidate_id: str, baseline_id: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT action_key FROM candidate_plan_done WHERE candidate_id = ? AND "
                                       "baseline_id = ?", (candidate_id, baseline_id))}


def mark_done(conn, candidate_id: str, baseline_id: str, title: str, done: bool = True) -> None:
    """Stored per candidate, role and action. The score is unchanged until new proof is added and scored."""
    with conn:
        if done:
            conn.execute("INSERT OR REPLACE INTO candidate_plan_done (candidate_id, baseline_id, action_key, title, done_at) "
                         "VALUES (?, ?, ?, ?, ?)", (candidate_id, baseline_id, action_key(title), title, now_iso()))
        else:
            conn.execute("DELETE FROM candidate_plan_done WHERE candidate_id = ? AND baseline_id = ? AND action_key = ?",
                         (candidate_id, baseline_id, action_key(title)))


def score_cards(conn, candidate_id: str) -> list[dict]:
    return [score_card(conn, candidate_id, t) for t in targets(conn, candidate_id)]


# --- Application details ("fill once") -------------------------------------------------------------------------

DECLINE = "decline"
YES_NO = ("yes", "no")
YES_NO_PREFER = ("yes", "no", DECLINE)
EEO_OPTIONS = {
    "gender": ("woman", "man", "non_binary", "self_describe", DECLINE),
    "race_ethnicity": ("american_indian_alaska_native", "asian", "black_african_american", "hispanic_latino",
                       "native_hawaiian_pacific_islander", "white", "two_or_more", DECLINE),
    "veteran_status": ("protected_veteran", "not_a_veteran", DECLINE),
    "disability_status": ("yes", "no", DECLINE),
}
WORK_MODES = ("remote", "hybrid", "on_site", "no_preference")
NOTICE = ("immediately", "1_week", "2_weeks", "1_month", "2_months", "3_months_plus")


def empty_details() -> dict:
    return {
        "work_authorization": {"country": "", "authorized": None, "sponsorship_now": DECLINE,
                               "sponsorship_future": DECLINE},
        "questions": {"earliest_start": None, "notice_period": None, "salary_min": None, "salary_currency": "USD",
                      "relocate": None, "work_mode": None, "locations": [], "links": []},
        "eeo": {k: DECLINE for k in EEO_OPTIONS},
    }


def application_details(conn, candidate_id: str) -> dict:
    row = conn.execute("SELECT * FROM candidate_application_details WHERE candidate_id = ?",
                       (candidate_id,)).fetchone()
    base = empty_details()
    if row is None:
        return {**base, "saved_at": None, "dismissed_at": None}
    return {"work_authorization": {**base["work_authorization"], **json.loads(row["work_auth_json"] or "{}")},
            "questions": {**base["questions"], **json.loads(row["questions_json"] or "{}")},
            "eeo": {**base["eeo"], **json.loads(row["eeo_json"] or "{}")},
            "saved_at": row["saved_at"], "dismissed_at": row["dismissed_at"]}


def _check(value, allowed, field: str):
    if value is not None and value not in allowed:
        raise ValueError(f"{field}: choose one of {', '.join(allowed)}")


def validate_details(d: dict) -> dict:
    """Clean and check every field (unknown keys are dropped). EEO answers default to "Decline to answer"."""
    base = empty_details()
    wa = {k: (d.get("work_authorization") or {}).get(k, v) for k, v in base["work_authorization"].items()}
    q = {k: (d.get("questions") or {}).get(k, v) for k, v in base["questions"].items()}
    eeo = {k: (d.get("eeo") or {}).get(k) or DECLINE for k in base["eeo"]}
    wa["country"] = (wa["country"] or "").strip()[:80]
    _check(wa["authorized"], YES_NO, "Authorized to work")
    _check(wa["sponsorship_now"], YES_NO_PREFER, "Needs sponsorship now")
    _check(wa["sponsorship_future"], YES_NO_PREFER, "Needs sponsorship in future")
    if q["earliest_start"]:
        try:
            date.fromisoformat(q["earliest_start"])
        except ValueError as exc:
            raise ValueError("Earliest start date: use YYYY-MM-DD") from exc
    _check(q["notice_period"], NOTICE, "Notice period")
    if q["salary_min"] is not None:
        if not isinstance(q["salary_min"], (int, float)) or q["salary_min"] < 0:
            raise ValueError("Salary expectation: a positive number")
        q["salary_min"] = int(q["salary_min"])
    q["salary_currency"] = (q["salary_currency"] or "USD").strip().upper()[:3]
    _check(q["relocate"], YES_NO, "Willing to relocate")
    _check(q["work_mode"], WORK_MODES, "Work mode")
    q["locations"] = [s.strip()[:80] for s in (q["locations"] or []) if s and s.strip()][:10]
    links = [s.strip()[:300] for s in (q["links"] or []) if s and s.strip()][:5]
    bad = [u for u in links if not u.lower().startswith(("http://", "https://"))]
    if bad:
        raise ValueError(f"Links must start with http:// or https:// ({bad[0]})")
    q["links"] = links
    for k, v in eeo.items():
        _check(v, EEO_OPTIONS[k], k.replace("_", " ").capitalize())
    return {"work_authorization": wa, "questions": q, "eeo": eeo}


def save_application_details(conn, candidate_id: str, data: dict) -> dict:
    d = validate_details(data)
    now = now_iso()
    with conn:
        conn.execute("INSERT INTO candidate_application_details (candidate_id, work_auth_json, questions_json, "
                     "eeo_json, saved_at, dismissed_at, updated_at) VALUES (?, ?, ?, ?, ?, NULL, ?) "
                     "ON CONFLICT(candidate_id) DO UPDATE SET work_auth_json = excluded.work_auth_json, "
                     "questions_json = excluded.questions_json, eeo_json = excluded.eeo_json, "
                     "saved_at = excluded.saved_at, updated_at = excluded.updated_at",
                     (candidate_id, json.dumps(d["work_authorization"]), json.dumps(d["questions"]),
                      json.dumps(d["eeo"]), now, now))
    return application_details(conn, candidate_id)


def dismiss_application_details(conn, candidate_id: str) -> dict:
    """"Skip for now": Home stops showing the "Finish your profile" card. Nothing is stored but the dismissal."""
    with conn:
        conn.execute("INSERT INTO candidate_application_details (candidate_id, dismissed_at, updated_at) "
                     "VALUES (?, ?, ?) ON CONFLICT(candidate_id) DO UPDATE SET dismissed_at = excluded.dismissed_at",
                     (candidate_id, now_iso(), now_iso()))
    return application_details(conn, candidate_id)


def finish_profile_card(conn, candidate_id: str) -> bool:
    d = application_details(conn, candidate_id)
    return not d["saved_at"] and not d["dismissed_at"]


# --- Plain-language progress (no model names, no internal keys) ------------------------------------------------

READING = {"ingest": ("Reading your documents", 10), "extract": ("Finding your roles, projects and skills", 70),
           "validate": ("Checking every item against your documents", 95)}


def reading_progress(progress):
    """Wraps a task's Progress: the onboarding stepper shows these lines while the candidate picks roles."""
    def on_event(e):
        if e.path or e.node not in READING or e.error:
            return
        label, pct = READING[e.node]
        if e.kind == "start":
            progress.say(e.node, f"{label}…")
        else:
            progress.say(e.node, f"{label}: done", pct)
    return on_event


def scoring_progress(progress, title: str, index: int, total: int):
    """Overall percentage across the roles being scored. The wait screen reads per-dimension progress from the saved
    scores (`scoring_view`), so these lines never show internal counts."""
    from backend.tasks import SUBCATEGORY_COUNT
    span = 90 / total
    done = {"n": 0}

    def on_event(e):
        if len(e.path) == 2 and e.node == "save" and e.kind == "end":
            done["n"] += 1
            progress.say("score", f"Scoring your {title} profile", 5 + span * index + span * done["n"] / SUBCATEGORY_COUNT)
    return on_event


# --- Errors people see ---------------------------------------------------------------------------------------------

import re as _re

_TECHNICAL = _re.compile(r"gemini|groq|claude|jev|gpt|gemma|qwen|llama|HTTP \d|extract_|judge_|route|"
                         r"no model could|no valid answer|attempt\(s\)|timeout|overloaded", _re.I)
BUSY_TEXT = "We couldn't read this right now: the reading service was busy. Upload it again in a few minutes."


def public_error(text: str | None) -> str | None:
    """What a candidate sees for a failed source: the reader's own message (e.g. a scanned PDF), the daily-limit
    message (it names the step, never a model), or a plain "busy" line. Model and provider details stay in the log."""
    if not text:
        return text
    if text.startswith("Daily free-tier limit reached"):
        return text
    return BUSY_TEXT if _TECHNICAL.search(text) else text
