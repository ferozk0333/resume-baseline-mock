"""My applications (specs/17 §7) and the closure principle. Code only: no model calls, nothing is ever sent.

The candidate sees a status and a timeline mapped from the recruiter's stages and the hiring workflow:
Applied → Viewed by recruiter (first applicant panel opened) → Shortlisted → Interview ("Round N") → Offer →
Hired / Declined, or Not moving forward with the reasons from the email that was actually sent. Never scores,
ranks, other candidates, recruiter notes, scorecards or internal tasks.

Closure: an open application with no stage change for CLOSURE_DAYS becomes a recruiter Tasks item
("<Name> has waited N days for an answer"); the candidate sees that RightStaff reminds the recruiter.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from backend import candidate_jobs as cj
from backend import recruiter as rc
from core import config

INTERVIEW_STAGES = {"Scheduling", "Phone screen", "Interview", "Hiring manager review"}
ROUND_PHASES = ("scheduling", "scheduled", "interview", "feedback", "decision")
STEPS = [("applied", "Applied"), ("viewed", "Viewed by recruiter"), ("shortlisted", "Shortlisted"),
         ("interview", "Interview"), ("offer", "Offer")]
WAITING_TEXT = "Waiting for the recruiter. RightStaff reminds recruiters to answer every application."
KIND_SUBCATEGORY = {"skill": "role_skills", "tool": "tools", "certification": "certifications"}


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _workflow(conn, app_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM workflows WHERE application_id = ?", (app_id,)).fetchone()
    return dict(row) if row else None


def _stage_moves(conn, app_id: str) -> list[tuple[str, str]]:
    """(stage name moved to, when), oldest first."""
    out = []
    for r in conn.execute("SELECT detail_json, created_at FROM activity WHERE application_id = ? AND kind = 'stage' "
                          "ORDER BY created_at, rowid", (app_id,)):
        to = json.loads(r[0] or "{}").get("to")
        if to:
            out.append((to, r[1]))
    return out


def _phase_events(conn, app_id: str) -> list[tuple[str, str]]:
    return [(json.loads(r[0] or "{}").get("key", ""), r[1]) for r in conn.execute(
        "SELECT payload_json, created_at FROM workflow_events WHERE application_id = ? AND type = 'phase' "
        "ORDER BY created_at, rowid", (app_id,))]


def last_stage_change(conn, app: dict) -> str | None:
    times = [app.get("applied_at")] + [t for _, t in _stage_moves(conn, app["id"])] + \
        [t for _, t in _phase_events(conn, app["id"])]
    return max((t for t in times if t), default=None)


def _reasons(conn, app: dict, job: dict, role_id: str | None) -> tuple[list[dict], str | None]:
    """The selected reasons of the "Not moving forward" email that was sent (none if it wasn't sent), each linked
    to the matching gap in the candidate's plan for the job's role."""
    row = conn.execute("SELECT reasons_json, sent_at FROM emails WHERE application_id = ? AND kind = "
                       "'not_moving_forward' AND status = 'sent' ORDER BY sent_at DESC LIMIT 1", (app["id"],)).fetchone()
    if row is None:
        return [], None
    kinds = {}
    if job.get("baseline_id"):
        from core import db
        b = db.get_baseline(conn, job["baseline_id"])
        kinds = {c.id: c.kind for c in (b.criteria if b else [])}
    out = []
    for r in (json.loads(row[0] or "{}") or {}).get("reasons", []):
        if not r.get("selected"):
            continue
        kind, _, key = r["id"].partition(":")
        sub = key if kind in ("gap", "missing") else KIND_SUBCATEGORY.get(kinds.get(key, "skill")) if kind == "must" else None
        sub = sub if sub in config.SUBCATEGORY_LABELS else None
        link = f"/scores?role={role_id}&tab=plan#gap-{sub}" if role_id and sub else None
        out.append({"title": r["title"], "detail": r.get("detail") or "", "advice": r.get("advice") or "", "link": link})
    return out, row[1]


def application_view(conn, app: dict, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    job = rc.get_job(conn, app["job_id"]) or {}
    from backend import jobs as jb
    full = jb.get(conn, app["job_id"]) or job
    role_id = cj.job_role(conn, full) if full else None
    wfrow = _workflow(conn, app["id"])
    moves = _stage_moves(conn, app["id"])
    phases = _phase_events(conn, app["id"])
    stage = None
    if app.get("stage_id"):
        st = conn.execute("SELECT name FROM interview_stages WHERE id = ?", (app["stage_id"],)).fetchone()
        stage = st[0] if st else None
    viewed = conn.execute("SELECT MIN(created_at) FROM job_events WHERE application_id = ? AND kind = 'panel_opened'",
                          (app["id"],)).fetchone()[0]

    def first_move(names) -> str | None:
        return next((t for n, t in moves if n in names), None)

    def first_phase(pred) -> str | None:
        return next((t for k, t in phases if pred(k)), None)
    dates = {"applied": app.get("applied_at"), "viewed": viewed,
             "shortlisted": first_move({"Shortlisted"}) or first_phase(lambda k: k == "shortlisted"),
             "interview": first_move(INTERVIEW_STAGES) or first_phase(lambda k: ":" in k),
             "offer": first_move({"Offer"}) or first_phase(lambda k: k == "offer")}
    outcome = (wfrow or {}).get("outcome")
    phase = (wfrow or {}).get("phase")
    # The candidate learns "Not moving forward" from the email the recruiter approved and sent; a board move to that
    # column while the email is still a draft shows the stage the application was in before.
    sent = conn.execute("SELECT 1 FROM emails WHERE application_id = ? AND kind = 'not_moving_forward' AND "
                        "status = 'sent'", (app["id"],)).fetchone() is not None
    nmf = sent and (app.get("status") == "not_moving_forward" or outcome == "not_moving_forward")
    if not nmf and stage == "Not moving forward":                 # use the stage before it
        stage = next((n for n, _ in reversed(moves) if n != "Not moving forward"), None)
    if outcome == "hired" or stage == "Hired" or phase == "hired":
        status, final = "Hired", "hired"
    elif outcome in ("declined", "withdrew"):
        status, final = "Declined", "declined"
    elif nmf:
        status, final = "Not moving forward", "not_moving_forward"
    elif phase == "offer" or stage == "Offer":
        status, final = "Offer", None
    elif (phase in ROUND_PHASES) or stage in INTERVIEW_STAGES:
        status, final = "Interview", None
    elif stage == "Shortlisted" or phase == "shortlisted" or (app.get("status") in ("shortlisted", "in_loop")
                                                                and stage not in (None, "New")):
        status, final = "Shortlisted", None
    elif viewed:
        status, final = "Viewed by recruiter", None
    else:
        status, final = "Applied", None
    round_label = None
    if status == "Interview" and wfrow and wfrow.get("round") is not None and phase in ROUND_PHASES:
        round_label = f"Round {wfrow['round'] + 1}"
    if final:                                       # how far it got before the end: the last step with a date
        at = max(i for i, (k, _) in enumerate(STEPS) if dates[k] or k == "applied")
    else:
        at = {"Applied": 0, "Viewed by recruiter": 1, "Shortlisted": 2, "Interview": 3, "Offer": 4}[status]
    timeline = []
    for i, (key, label) in enumerate(STEPS):
        # Applied / Viewed / Shortlisted are implied by a later step; an interview or offer only counts if it happened.
        implied = key in ("applied", "viewed", "shortlisted")
        if final:
            state = "done" if i <= at and (dates[key] or implied) else "not_reached"
        elif i < at:
            state = "done" if dates[key] or implied else "not_reached"
        else:
            state = "current" if i == at else "upcoming"
        timeline.append({"key": key, "label": label + (f" · {round_label}" if key == "interview" and round_label else ""),
                         "state": state, "date": dates[key] if state in ("done", "current") else None})
    reasons, decided_at = ([], None)
    if final:
        end = {"hired": ("hired", "Hired"), "declined": ("declined", "Declined"),
               "not_moving_forward": ("not_moving_forward", "Not moving forward")}[final]
        if final == "not_moving_forward":
            reasons, decided_at = _reasons(conn, app, full, role_id)
        timeline.append({"key": end[0], "label": end[1], "state": "done",
                         "date": decided_at or (wfrow or {}).get("updated_at")})
    last = _dt(last_stage_change(conn, app))
    waiting_days = (now - last).days if last else 0
    waiting = final is None and waiting_days >= config.CLOSURE_DAYS
    return {"application_id": app["id"], "job_id": app["job_id"], "title": full.get("title") or job.get("title"),
            "company": full.get("company") or job.get("company"), "applied_at": app.get("applied_at"),
            "status": status, "round": round_label, "final": final is not None, "timeline": timeline,
            "reasons": reasons, "waiting": waiting, "waiting_text": WAITING_TEXT if waiting else None}


def my_applications(conn, candidate_id: str, now: datetime | None = None) -> list[dict]:
    rows = [dict(r) for r in conn.execute("SELECT * FROM applications WHERE candidate_id = ? ORDER BY applied_at DESC, "
                                          "rowid DESC", (candidate_id,))]
    return [application_view(conn, a, now) for a in rows]


# --- Closure principle --------------------------------------------------------------------------------------------

def closure_tasks(conn, now: datetime | None = None) -> list[dict]:
    """Recruiter Tasks items for open applications with no stage change for CLOSURE_DAYS. Nothing is sent."""
    now = now or datetime.now(timezone.utc)
    out = []
    rows = conn.execute("SELECT a.*, j.title AS job_title, j.company AS company, j.status AS job_status FROM applications "
                        "a JOIN jobs j ON j.id = a.job_id WHERE a.status IS NOT 'not_moving_forward'").fetchall()
    for r in rows:
        app = dict(r)
        if app["job_status"] == "draft":
            continue
        wfrow = _workflow(conn, app["id"])
        if wfrow and (wfrow["status"] == "closed" or wfrow.get("outcome")):
            continue
        st = conn.execute("SELECT name, terminal FROM interview_stages WHERE id = ?", (app["stage_id"],)).fetchone() \
            if app.get("stage_id") else None
        if st and (st[1] or st[0] == "Hired"):
            continue
        last = _dt(last_stage_change(conn, app))
        if last is None or (now - last).days < config.CLOSURE_DAYS:
            continue
        name = conn.execute("SELECT name FROM candidates WHERE id = ?", (app["candidate_id"],)).fetchone()
        name = (name[0] if name else None) or "Applicant"
        days = (now - last).days
        out.append({"application_id": app["id"], "candidate": name, "job_id": app["job_id"], "job_title": app["job_title"],
                    "company": app["company"], "stage": st[0] if st else "New", "step": "closure",
                    "title": f"{name} has waited {days} days for an answer", "since": last.isoformat(timespec="seconds"),
                    "kind": "closure"})
    return out
