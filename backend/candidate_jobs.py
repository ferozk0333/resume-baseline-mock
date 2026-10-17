"""Candidate Home and job detail (specs/17 §4–§6). Code only: no model is called here.

- JobMatch is a **dummy preview** (§6): one deterministic function, `jobmatch_preview`, labelled "Preview" in the UI.
  It is never stored as a score and never part of a recruiter response, a pool or a percentile.
- Recruiter activity (§4.1) comes from real recruiter actions where they exist (applicant panel opened, stage moves,
  approved emails, workflow decisions, posting or editing the job); seeded demo jobs have seeded events.
- "Your closest evidence" (§5) reuses the saved judgment of the candidate's baseline run for the same role, and only
  when the requirement's name or an alias exactly matches a criterion of that role baseline.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from backend import candidate as cx
from backend import jobs as jb
from core import config, db
from core.db import new_id, now_iso
from extraction import store

HIDE_REASONS = ("location", "salary", "role", "seniority", "other")
BACKING_TEXT = {"artifact": "Shown in a project repository", "in a bullet": "Shown in your experience",
                "certification": "Shown by a certification", "listed only": "Listed in your skills only",
                "not shown": "Not shown in your profile yet"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    t = datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# --- Which role a job belongs to -----------------------------------------------------------------------------------

def job_role(conn, job: dict) -> str | None:
    """The role baseline a job was written from: recorded (seeded jobs), the job's baseline itself (the first demo
    jobs), or the role whose title filter matches the job title (config/market_sources.yaml). None if unknown."""
    if job.get("role_baseline_id"):                  # the recruiter's "Role family" ("none": no role family)
        return None if job["role_baseline_id"] == "none" else job["role_baseline_id"]
    roles = cx.role_baselines(conn)
    if job.get("baseline_id") in roles:
        return job["baseline_id"]
    from market import sources
    for r in sources.load().values():
        if r.manual_baseline in roles and r.title_matches(job.get("title") or ""):
            return r.manual_baseline
    return None


# --- JobMatch (dummy preview, §6) ------------------------------------------------------------------------------------

def _unit(candidate_id: str, job_id: str) -> float:
    """A stable number in [0, 1] for this candidate and job (same across reloads and restarts)."""
    h = hashlib.sha256(f"{candidate_id}:{job_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def jobmatch_value(candidate_id: str, job_id: str, baseline_score: float | None) -> int:
    """With a baseline score for the job's role: that score + an offset in [-12, +8]; else 55 + an offset in
    [-15, +15]. Clamped to [5, 98], rounded."""
    u = _unit(candidate_id, job_id)
    if baseline_score is not None:
        value = baseline_score + (-12 + 20 * u)
    else:
        value = 55 + (-15 + 30 * u)
    return int(round(min(98.0, max(5.0, value))))


def must_haves_preview(value: int, total: int) -> int:
    return max(0, min(total, round(total * value / 100)))


def baseline_score(conn, candidate_id: str, role_id: str | None, cache: dict | None = None) -> float | None:
    if role_id is None:
        return None
    cache = cache if cache is not None else {}
    if role_id not in cache:
        from graph.pipeline import rescore
        import json
        run = cx.role_run(conn, candidate_id, role_id)
        cache[role_id] = rescore(conn, run["id"], json.loads(run["weights_json"])).final if run else None
    return cache[role_id]


def jobmatch_preview(conn, candidate_id: str, job: dict, cache: dict | None = None) -> dict | None:
    """The dummy JobMatch for one job, or None when JOBMATCH_PREVIEW is off. Never written to the database."""
    if not config.JOBMATCH_PREVIEW:
        return None
    base = baseline_score(conn, candidate_id, job_role(conn, job), cache)
    value = jobmatch_value(candidate_id, job["id"], base)
    total = len(job["must_haves"])
    return {"value": value, "must_haves_met": must_haves_preview(value, total), "must_haves_total": total,
            "preview": True}


def group_of(value: int | None) -> str | None:
    if value is None:
        return None
    return "strong" if value >= config.JOBMATCH_STRONG else "close" if value >= config.JOBMATCH_CLOSE else "stretch"


# --- Recruiter activity (§4.1) ---------------------------------------------------------------------------------------

def record_panel_opened(conn, application_id: str) -> None:
    """The recruiter opened an applicant panel: a lightweight activity event on the job."""
    row = conn.execute("SELECT job_id FROM applications WHERE id = ?", (application_id,)).fetchone()
    if row is None:
        return
    with conn:
        conn.execute("INSERT INTO job_events (id, job_id, application_id, kind, demo, created_at) "
                     "VALUES (?, ?, ?, 'panel_opened', 0, ?)", (new_id(), row[0], application_id, now_iso()))


def activity(conn, job: dict, now: datetime | None = None) -> dict:
    """Last recruiter action on the job, and whether applications are being reviewed (a stage moved lately)."""
    now = now or _now()
    jid = job["id"]
    # Posting the job counts once; editing it does not. Afterwards only actions on applicants count: an applicant
    # panel opened, a stage change, an email approved (sent), a workflow step the recruiter took.
    times = [job.get("published_at") or job.get("created_at")]
    times += [r[0] for r in conn.execute("SELECT created_at FROM job_events WHERE job_id = ?", (jid,))]
    times += [r[0] for r in conn.execute("SELECT created_at FROM activity WHERE job_id = ? AND kind IN "
                                         "('stage', 'email', 'decision')", (jid,))]
    times += [r[0] for r in conn.execute("SELECT sent_at FROM emails WHERE job_id = ? AND status = 'sent'", (jid,))]
    times += [r[0] for r in conn.execute("SELECT e.created_at FROM workflow_events e JOIN applications a ON "
                                         "a.id = e.application_id WHERE a.job_id = ? AND e.actor = 'recruiter'", (jid,))]
    moves = [r[0] for r in conn.execute("SELECT created_at FROM job_events WHERE job_id = ? AND kind = 'stage_moved'",
                                        (jid,))]
    moves += [r[0] for r in conn.execute("SELECT created_at FROM activity WHERE job_id = ? AND kind = 'stage'", (jid,))]
    last = max((t for t in map(_dt, times) if t), default=None)
    days = (now - last).days if last else None
    recent_move = any(t and (now - t).days < config.REVIEWING_DAYS for t in map(_dt, moves))
    low = days is None or days >= config.LOW_ACTIVITY_DAYS
    label = ("Low recent activity" if low else "Recruiter active today" if days == 0
             else "Recruiter active yesterday" if days == 1 else f"Recruiter active {days} days ago")
    return {"last_at": last.isoformat(timespec="seconds") if last else None, "days": days, "label": label,
            "low": low, "reviewing": recent_move}


# --- Saved / Not interested ------------------------------------------------------------------------------------------

def prefs(conn, candidate_id: str) -> dict[str, dict]:
    return {r["job_id"]: dict(r) for r in conn.execute("SELECT * FROM candidate_job_prefs WHERE candidate_id = ?",
                                                       (candidate_id,))}


def _set_pref(conn, candidate_id: str, job_id: str, **fields) -> dict:
    cur = prefs(conn, candidate_id).get(job_id) or {"saved": 0, "hidden": 0, "reason": None}
    row = {**cur, **fields}
    with conn:
        conn.execute("INSERT OR REPLACE INTO candidate_job_prefs (candidate_id, job_id, saved, hidden, reason, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (candidate_id, job_id, int(row["saved"]), int(row["hidden"]),
                                                   row["reason"], now_iso()))
    return prefs(conn, candidate_id)[job_id]


def save(conn, candidate_id: str, job_id: str, saved: bool) -> dict:
    return _set_pref(conn, candidate_id, job_id, saved=saved)


def hide(conn, candidate_id: str, job_id: str, reason: str | None) -> dict:
    """Not interested: hides the job (the reason is optional and stored). Changes no score."""
    if reason is not None and reason not in HIDE_REASONS:
        raise ValueError(f"reason must be one of {', '.join(HIDE_REASONS)}")
    return _set_pref(conn, candidate_id, job_id, hidden=True, reason=reason)


def unhide(conn, candidate_id: str, job_id: str) -> dict:
    return _set_pref(conn, candidate_id, job_id, hidden=False, reason=None)


# --- Home list (§4) --------------------------------------------------------------------------------------------------

def salary(details: dict) -> dict | None:
    lo, hi = details.get("salary_min"), details.get("salary_max")
    if not lo and not hi:
        return None
    return {"min": lo, "max": hi, "currency": details.get("salary_currency") or "USD"}


def profile_confirmed(conn, candidate_id: str) -> bool:
    ob = cx.onboarding(conn, candidate_id)
    return ob["step"] == "done" or bool(ob.get("confirmed_at"))


def _row(conn, candidate_id: str, job: dict, targets: set[str], pref: dict, applied: dict, cache: dict,
         now: datetime) -> dict:
    role = job_role(conn, job)
    jm = jobmatch_preview(conn, candidate_id, job, cache)
    d = job["details"]
    return {"id": job["id"], "title": job["title"], "company": job["company"], "location": d.get("location") or "",
            "workplace": d.get("workplace"), "salary": salary(d), "posted_at": job.get("published_at") or job["created_at"],
            "activity": activity(conn, job, now), "jobmatch": jm, "group": group_of(jm["value"] if jm else None),
            "role_baseline_id": role, "target": role in targets, "saved": bool(pref.get("saved")),
            "hidden": bool(pref.get("hidden")), "applied": applied.get(job["id"]), "demo": job.get("data_source") == "demo"}


GROUP_ORDER = {"strong": 0, "close": 1, "stretch": 2, None: 3}


def sort_key(r: dict):
    """Target roles first; then Strong, Close, Stretch; then JobMatch (high first); then newest."""
    posted = _dt(r["posted_at"])
    return (not r["target"], GROUP_ORDER[r["group"]], -(r["jobmatch"]["value"] if r["jobmatch"] else 0),
            -(posted.timestamp() if posted else 0))


def home_jobs(conn, candidate_id: str, now: datetime | None = None) -> dict:
    """Open recruiter jobs on RightStaff (never market postings), ranked for this candidate."""
    now = now or _now()
    targets = {t["baseline_id"] for t in cx.targets(conn, candidate_id)}
    p = prefs(conn, candidate_id)
    applied = {r[0]: {"status": "Applied", "applied_at": r[1]} for r in conn.execute(
        "SELECT job_id, applied_at FROM applications WHERE candidate_id = ?", (candidate_id,))}
    cache: dict = {}
    rows = [_row(conn, candidate_id, jb.get(conn, j["id"]), targets, p.get(j["id"], {}), applied, cache, now)
            for j in jb.listing(conn, "open")]
    visible = sorted([r for r in rows if not r["hidden"]], key=sort_key)
    details = cx.application_details(conn, candidate_id)
    mode = details["questions"].get("work_mode")
    hidden = [{"id": r["id"], "title": r["title"], "company": r["company"], "reason": p.get(r["id"], {}).get("reason")}
              for r in rows if r["hidden"]]
    return {"jobs": visible, "hidden": len(hidden), "hidden_jobs": hidden, "total_open": len(rows),
            "preview": config.JOBMATCH_PREVIEW, "profile_confirmed": profile_confirmed(conn, candidate_id),
            "thresholds": {"strong": config.JOBMATCH_STRONG, "close": config.JOBMATCH_CLOSE},
            "targets": cx.targets(conn, candidate_id),
            "defaults": {"workplace": mode if mode in ("remote", "hybrid", "on_site") else None}}


# --- Job detail (§5) -------------------------------------------------------------------------------------------------

def _keys(name: str, aliases) -> set[str]:
    return {x.strip().casefold() for x in [name, *(aliases or [])] if x and x.strip()}


def closest_evidence(conn, candidate_id: str, role_id: str | None, job_baseline) -> tuple[dict, dict | None]:
    """criterion id of the job → the matching row of the candidate's same-role baseline run (exact name or alias
    match only). Also returns that run (or None)."""
    run = cx.role_run(conn, candidate_id, role_id) if role_id else None
    if run is None:
        return {}, None
    from backend import recruiter as rc
    from graph.pipeline import run_scores
    role_b = db.get_baseline(conn, run["baseline_id"])
    profile = store.load_profile(conn, candidate_id)
    evidence = {e["id"]: e for e in store.list_evidence(conn, candidate_id)}
    rows = rc.requirements(role_b, {"must_haves": []}, run_scores(conn, run["id"]), profile, evidence)
    by_key: dict[str, dict] = {}
    for crit, row in zip(role_b.criteria, rows):
        for k in _keys(crit.name, crit.aliases):
            by_key.setdefault(k, row)
    out = {}
    for c in job_baseline.criteria:
        hit = next((by_key[k] for k in _keys(c.name, c.aliases) if k in by_key), None)
        if hit and hit["label"] is not None and hit["backing"] in BACKING_TEXT:
            out[c.id] = hit
    return out, run


def job_detail(conn, candidate_id: str, job_id: str, now: datetime | None = None) -> dict | None:
    job = jb.get(conn, job_id)
    if job is None or job["status"] == "draft":
        return None
    applied = conn.execute("SELECT applied_at FROM applications WHERE job_id = ? AND candidate_id = ?",
                           (job_id, candidate_id)).fetchone()
    if job["status"] != "open" and not applied:
        return None
    role = job_role(conn, job)
    role_title = cx.role_baselines(conn).get(role) if role else None
    jbase = db.get_baseline(conn, job["baseline_id"])
    matched, run = closest_evidence(conn, candidate_id, role, jbase)
    caption = f"From your {role_title} baseline score" if role_title else None
    reqs = []
    for c in jbase.criteria:
        m = matched.get(c.id)
        reqs.append({"name": c.name, "kind": c.kind, "priority": c.priority, "must_have": c.id in job["must_haves"],
                     "evidence": {"text": BACKING_TEXT[m["backing"]], "quote": m["quote"], "source": m["source"],
                                  "matched": m["name"], "caption": caption} if m else None})
    reqs.sort(key=lambda r: (not r["must_have"], r["priority"] != "required"))
    raise_, plan_status, fallback = [], None, False
    if run is not None:
        from graph.pipeline import current_plan
        plan = current_plan(conn, run["id"])
        plan_status = "none" if plan is None else ("failed" if plan.error else "ready")
        if plan is None and run.get("plan_status"):
            plan_status = run["plan_status"]
        actions = sorted(plan.actions, key=lambda p: -p.points) if plan and not plan.error else []
        names = set().union(*(_keys(c.name, c.aliases) for c in jbase.criteria))
        raise_ = [p for p in actions if {x.casefold() for x in p.action.covers_criteria} & names][:3]
        if not raise_ and actions:
            raise_, fallback = actions[:1], True
        raise_ = [{"title": p.action.title, "what_to_do": p.action.what_to_do, "points": round(p.points, 1),
                   "covers": p.action.covers_criteria} for p in raise_]
    score = baseline_score(conn, candidate_id, role)
    return {"job": {**jb.posting(conn, job), "salary": salary(job["details"]), "status": job["status"],
                    "demo": job.get("data_source") == "demo"},
            "activity": activity(conn, job, now), "jobmatch": jobmatch_preview(conn, candidate_id, job),
            "role": {"baseline_id": role, "title": role_title, "baseline_score": score,
                     "run_id": run["id"] if run else None},
            "requirements": reqs, "evidence_matches": len(matched),
            "raise": raise_, "raise_fallback": fallback, "plan_status": plan_status,
            "applied": {"status": "Applied", "applied_at": applied[0]} if applied else None,
            "profile_confirmed": profile_confirmed(conn, candidate_id),
            "saved": bool(prefs(conn, candidate_id).get(job_id, {}).get("saved")),
            "recruiter_view": recruiter_view(conn, candidate_id, matched)}


def recruiter_view(conn, candidate_id: str, matched: dict) -> dict:
    """"What the recruiter will see" (the apply sheet): a snapshot of the profile. Not the baseline score or the
    JobMatch preview: the recruiter scores the application against their own job."""
    cand = store.get_candidate(conn, candidate_id) or {}
    profile = store.load_profile(conn, candidate_id)
    ev = store.list_evidence(conn, candidate_id)
    names = {"resume": "Resume", "linkedin_pdf": "LinkedIn profile", "portfolio": "Portfolio", "github": "GitHub"}
    sources = sorted({names[e["type"]] for e in ev if e["type"] in names and e["status"] != "failed"})
    links = conn.execute("SELECT COUNT(*) FROM media_links WHERE candidate_id = ?", (candidate_id,)).fetchone()[0]
    if links:
        sources.append(f"{links} project demo video{'s' if links > 1 else ''}")
    if profile.certifications:
        sources.append(f"{len(profile.certifications)} certification{'s' if len(profile.certifications) > 1 else ''}")
    roles = [" — ".join(x for x in (r.title, r.employer) if x) for r in profile.roles][:4]
    top = [{"name": m["name"], "quote": m["quote"], "source": m["source"]} for m in matched.values()
           if m["backing"] in ("artifact", "in a bullet") and m["quote"]][:3]
    return {"name": cand.get("name"), "sources": sources, "roles": roles, "projects": [p.name for p in profile.projects][:4],
            "top_evidence": top}
