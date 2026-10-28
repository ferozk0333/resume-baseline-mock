"""Recruiter decision workflows (specs/13 §2–§4): interview plan, stage moves, activity, and emails.

Model calls only in `draft_*` (EMAIL_MODEL). Every draft is checked in code; if the model is unavailable, over
quota or fails the checks twice, a code template built from the same inputs is used ("Template draft").
Nothing is sent without the recruiter's Send action: only `send_email` delivers.

Fairness: the drafting model never sees the candidate's name, scores, percentiles, rank or other candidates.
It sees the job, the reasons the recruiter selected (or the evidence it may cite) and the logistics. Code adds
the greeting and signature, and rejects drafts that mention scores, ranking, other candidates, personal
characteristics or promises about future roles.
"""
from __future__ import annotations

import json
import logging
import re

from pydantic import BaseModel, Field

from workflows import mailer
from services import recruiter as rc
from core import config, db
from core.db import new_id, now_iso
from providers.llm import LLMError
from core.text import wrap_tag
from extraction import store
from extraction.extract import load_prompt
from scoring.formulas import GUIDANCE
from scoring.gaps import rank_gaps
from scoring.plan import TYPE_LABELS, type_fit

log = logging.getLogger(__name__)

TEMPLATE_MODEL = "template"
DEFAULT_STAGES = ["Shortlisted", "Scheduling", "Phone screen", "Interview", "Hiring manager review", "Offer", "Hired"]
TERMINAL_STAGE = "Not moving forward"
FOCUS_STAGES = {"Phone screen", "Interview", "Hiring manager review"}     # focus areas pre-filled from must-haves
DEFAULT_SETTINGS = {"recruiter_name": "The hiring team", "duration_minutes": 30,
                    "availability": "Mon–Wed 10:00–14:00 ET", "scheduling_link": ""}


# --- Interview plan and job settings ------------------------------------------------------------------

def settings(conn, job_id: str) -> dict:
    row = conn.execute("SELECT settings_json FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return {**DEFAULT_SETTINGS, **json.loads((row and row[0]) or "{}")}


def update_settings(conn, job_id: str, values: dict) -> dict:
    current = settings(conn, job_id)
    current.update({k: v for k, v in values.items() if k in DEFAULT_SETTINGS and v is not None})
    current["duration_minutes"] = int(current["duration_minutes"])
    if not 5 <= current["duration_minutes"] <= 240:
        raise ValueError("Interview duration must be 5–240 minutes.")
    with conn:
        conn.execute("UPDATE jobs SET settings_json = ? WHERE id = ?", (json.dumps(current), job_id))
    return current


def stages(conn, job_id: str) -> list[dict]:
    """The job's interview plan in order (default stages created on first use); the terminal stage last."""
    if not conn.execute("SELECT 1 FROM interview_stages WHERE job_id = ? LIMIT 1", (job_id,)).fetchone():
        job = rc.get_job(conn, job_id)
        names = {c.id: c.name for c in db.get_baseline(conn, job["baseline_id"]).criteria}
        focus = [names[m] for m in job["must_haves"] if m in names]
        with conn:
            for i, name in enumerate([*DEFAULT_STAGES, TERMINAL_STAGE]):
                conn.execute("INSERT INTO interview_stages (id, job_id, name, position, focus_json, terminal) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (new_id(), job_id, name, i,
                                                           json.dumps(focus if name in FOCUS_STAGES else []),
                                                           int(name == TERMINAL_STAGE)))
    rows = conn.execute("SELECT * FROM interview_stages WHERE job_id = ? ORDER BY terminal, position",
                        (job_id,)).fetchall()
    out = []
    for r in rows:
        n = conn.execute("SELECT COUNT(*) FROM applications WHERE stage_id = ?", (r["id"],)).fetchone()[0]
        out.append({"id": r["id"], "name": r["name"], "position": r["position"], "terminal": bool(r["terminal"]),
                    "focus": json.loads(r["focus_json"] or "[]"), "applicants": n})
    return out


def _stage(conn, stage_id: str) -> dict:
    row = conn.execute("SELECT * FROM interview_stages WHERE id = ?", (stage_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown stage.")
    return dict(row)


def stage_named(conn, job_id: str, name: str) -> dict:
    return next(s for s in stages(conn, job_id) if s["name"] == name)


def add_stage(conn, job_id: str, name: str, focus: list[str] | None = None) -> list[dict]:
    name = name.strip()
    if not name:
        raise ValueError("A stage needs a name.")
    current = stages(conn, job_id)
    if any(s["name"].lower() == name.lower() for s in current):
        raise ValueError(f"A stage called {name!r} already exists.")
    last = max((s["position"] for s in current if not s["terminal"]), default=-1)
    with conn:
        conn.execute("INSERT INTO interview_stages (id, job_id, name, position, focus_json, terminal) "
                     "VALUES (?, ?, ?, ?, ?, 0)", (new_id(), job_id, name, last + 1, json.dumps(focus or [])))
    return stages(conn, job_id)


def update_stage(conn, job_id: str, stage_id: str, name: str | None = None, focus: list[str] | None = None) -> list[dict]:
    st = _stage(conn, stage_id)
    if name is not None:
        name = name.strip()
        if not name:
            raise ValueError("A stage needs a name.")
        if st["terminal"]:
            raise ValueError("The terminal stage cannot be renamed.")
        if any(s["name"].lower() == name.lower() and s["id"] != stage_id for s in stages(conn, job_id)):
            raise ValueError(f"A stage called {name!r} already exists.")
    with conn:
        conn.execute("UPDATE interview_stages SET name = COALESCE(?, name), focus_json = COALESCE(?, focus_json) "
                     "WHERE id = ? AND job_id = ?", (name, json.dumps(focus) if focus is not None else None,
                                                     stage_id, job_id))
    return stages(conn, job_id)


def reorder_stages(conn, job_id: str, ids: list[str]) -> list[dict]:
    movable = [s["id"] for s in stages(conn, job_id) if not s["terminal"]]
    if sorted(ids) != sorted(movable):
        raise ValueError("Send every non-terminal stage id exactly once.")
    with conn:
        for i, sid in enumerate(ids):
            conn.execute("UPDATE interview_stages SET position = ? WHERE id = ?", (i, sid))
    return stages(conn, job_id)


def delete_stage(conn, job_id: str, stage_id: str) -> list[dict]:
    st = _stage(conn, stage_id)
    if st["terminal"]:
        raise ValueError("The terminal stage cannot be deleted.")
    if conn.execute("SELECT 1 FROM applications WHERE stage_id = ? LIMIT 1", (stage_id,)).fetchone():
        raise ValueError(f"Move the candidates out of {st['name']!r} first.")
    if len([s for s in stages(conn, job_id) if not s["terminal"]]) <= 1:
        raise ValueError("Keep at least one stage.")
    with conn:
        conn.execute("DELETE FROM interview_stages WHERE id = ?", (stage_id,))
    return stages(conn, job_id)


# --- Moves and activity -------------------------------------------------------------------------------------

def log_activity(conn, app: dict, kind: str, summary: str, detail: dict | None = None) -> None:
    with conn:
        conn.execute("INSERT INTO activity (id, application_id, job_id, kind, summary, detail_json, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (new_id(), app["id"], app["job_id"], kind, summary,
                                                      json.dumps(detail or {}), now_iso()))


def activity(conn, app_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM activity WHERE application_id = ? ORDER BY created_at, rowid", (app_id,))
    out = []
    for r in rows:
        item = {k: r[k] for k in ("id", "kind", "summary", "created_at")} | {"detail": json.loads(r["detail_json"] or "{}")}
        if item["detail"].get("email_id"):
            item["email"] = get_email(conn, item["detail"]["email_id"])
        out.append(item)
    return out


def _status_for(conn, job_id: str, stage_id: str | None) -> str:
    if stage_id is None:
        return "new"
    st = _stage(conn, stage_id)
    if st["terminal"]:
        return "not_moving_forward"
    first = next(s for s in stages(conn, job_id) if not s["terminal"])
    return "shortlisted" if first["id"] == stage_id else "in_loop"


def move(conn, app: dict, stage_id: str | None, *, reason: str | None = None) -> dict:
    """Move an application to a stage (None = back to new); status follows; the move is logged."""
    if stage_id is not None and _stage(conn, stage_id)["job_id"] != app["job_id"]:
        raise ValueError("That stage belongs to another job.")
    before = _stage(conn, app["stage_id"])["name"] if app.get("stage_id") else "New"
    after = _stage(conn, stage_id)["name"] if stage_id else "New"
    status = _status_for(conn, app["job_id"], stage_id)
    with conn:
        conn.execute("UPDATE applications SET stage_id = ?, status = ? WHERE id = ?", (stage_id, status, app["id"]))
    if before != after:
        log_activity(conn, app, "stage", f"Moved from {before} to {after}" + (f" ({reason})" if reason else ""),
                     {"from": before, "to": after})
    return {**app, "stage_id": stage_id, "status": status}


def set_status(conn, app: dict, status: str) -> dict:
    """The list's Shortlist / Not moving forward / back-to-new buttons, mapped onto stages."""
    if status not in rc.STATUSES:
        raise ValueError(f"status must be one of {rc.STATUSES}")
    target = {"new": None, "shortlisted": stage_named(conn, app["job_id"], DEFAULT_STAGES[0])["id"]
              if any(s["name"] == DEFAULT_STAGES[0] for s in stages(conn, app["job_id"]))
              else next(s for s in stages(conn, app["job_id"]) if not s["terminal"])["id"],
              "not_moving_forward": next(s for s in stages(conn, app["job_id"]) if s["terminal"])["id"]}
    if status == "in_loop":
        raise ValueError("Move the candidate to an interview stage instead.")
    return move(conn, app, target[status])


def pipeline(conn, job: dict) -> dict:
    """Board: one column per stage (the terminal stage last), cards in ranked order."""
    rows = {r["application_id"]: r for r in rc.applicants(conn, job)}
    cols = []
    apps = conn.execute("SELECT id, stage_id FROM applications WHERE job_id = ?", (job["id"],)).fetchall()
    for st in stages(conn, job["id"]):
        ids = [a["id"] for a in apps if a["stage_id"] == st["id"]]
        cards = [rows[i] for i in rows if i in ids]
        cols.append({**st, "cards": [{k: c[k] for k in ("application_id", "name", "current_role", "match",
                                                         "confidence", "demo", "status")} for c in cards]})
    return {"stages": cols, "unstaged": sum(1 for a in apps if a["stage_id"] is None)}


# --- Reasons for "Not moving forward" (code) ----------------------------------------------------------------

def _first_type(sub: str, baseline) -> str:
    fit = type_fit(baseline)
    return next((t for t in TYPE_LABELS if sub in fit[t]), "build_project")


def build_reasons(conn, app: dict, job: dict) -> list[dict]:
    """Unmet or on-paper must-haves, the top 3 gaps, and missing key evidence. The first 3 are pre-selected.
    Texts are job-related and never mention scores."""
    baseline = db.get_baseline(conn, job["baseline_id"])
    profile = store.load_profile(conn, app["candidate_id"])
    scores = rc.run_scores(conn, app["run_id"]) if app["run_id"] else {}
    evidence = {e["id"]: e for e in store.list_evidence(conn, app["candidate_id"])}
    out = []
    for r in rc.requirements(baseline, job, scores, profile, evidence):
        if not r["must_have"] or r["status"] not in ("not_met", "met_on_paper"):
            continue
        on_paper = r["status"] == "met_on_paper"
        kind = {"tool": "tool", "certification": "certification"}.get(r["kind"], "skill")
        out.append({"id": f"must:{r['criterion_id']}", "source": "must-have", "title": r["name"],
                    "detail": f"{r['name']} is listed, but no work shows it." if on_paper
                    else f"{r['name']} does not appear in the profile.",
                    "advice": f"Show {r['name']} in a project or role, with what the work produced."
                    if kind != "certification" else f"Earn a recognized {r['name'].lower()}.",
                    "action_type": "earn_certification" if kind == "certification" else "build_project"})
    seen = {r["title"] for r in out}
    gaps = rank_gaps(scores, job["weights"], baseline)[:3] if scores else []
    for g in gaps:
        unmet = [u["criterion"] for u in g.details.get("unmet_criteria", []) if u["priority"] == "required"]
        unmet = [u for u in unmet if u not in seen][:2]
        advice = (f"Show {' and '.join(unmet)} applied in your work, with the result." if unmet
                  else GUIDANCE.get(g.subcategory, ""))
        out.append({"id": f"gap:{g.subcategory}", "source": "gap", "title": g.label,
                    "detail": "Missing from the profile." if g.status == "missing" else "Shown only weakly so far.",
                    "advice": advice, "action_type": _first_type(g.subcategory, baseline)})
    gap_keys = {g.subcategory for g in gaps}
    key_missing = [s for s in ("work_samples", "outcomes", "validation_strength")
                   if scores.get(s, {}).get("status") == "missing" and s not in gap_keys]
    for s in key_missing[:2]:
        out.append({"id": f"missing:{s}", "source": "missing evidence", "title": config.SUBCATEGORY_LABELS[s],
                    "detail": "No evidence of this yet.", "advice": GUIDANCE[s], "action_type": _first_type(s, baseline)})
    for i, r in enumerate(out):
        r["selected"] = i < 3
    return out


# --- Drafting (model, code-checked) and templates -----------------------------------------------------------

class Area(BaseModel):
    reason_id: str = Field(description="The id of the selected reason this area comes from.")
    text: str = Field(description="One sentence: a specific, job-related area to strengthen.")


class RejectionDraft(BaseModel):
    subject: str
    opening: str = Field(description="Thank them and state the decision clearly. 1-2 sentences. No greeting.")
    areas: list[Area] = Field(description="2-3 areas, each from a different selected reason.")
    next_step_type: str = Field(description="One action type key from <action_types>.")
    next_step: str = Field(description="One concrete next step of that type, e.g. a project showing X with a "
                                       "measured result. One sentence.")
    closing: str = Field(description="One short, respectful closing sentence. No sign-off, no name.")


class SchedulingDraft(BaseModel):
    subject: str
    opening: str = Field(description="Thank them; invite them to the next stage. No greeting.")
    personal_line: str = Field(description="One sentence referencing their most relevant work, from <evidence>.")
    evidence_id: str = Field(description="The id of the <evidence> item the personal line refers to.")
    closing: str = Field(description="One short closing sentence. No sign-off, no name.")


BANNED = [
    (r"\bscor(e|es|ed|ing)\b", "mentions a score"), (r"\bpercentile", "mentions a percentile"),
    (r"\b(rank|ranked|ranking)\b", "mentions ranking"), (r"\d+\s*%|/\s*100\b", "contains a percentage or score"),
    (r"\bother (candidates|applicants)\b|\banother (candidate|applicant)\b|\bcompared (to|with) (other|the other)",
     "mentions other candidates"),
    (r"\b(age|gender|race|religion|nationality|ethnic\w*|disabilit\w*|pregnan\w*|marital|married|citizenship)\b",
     "mentions a personal characteristic"),
    (r"\bfuture (role|roles|opening|openings|position|positions|opportunit\w+)\b|\bkeep (your|you)\b.*\b(file|mind)\b|"
     r"\bwe will (contact|reach out)\b|\bwill be in touch about\b", "promises future roles"),
]


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def banned_issues(text: str, other_names: list[str] = ()) -> list[str]:
    issues = [why for pattern, why in BANNED if re.search(pattern, text, re.I)]
    for n in other_names:
        if n and len(n) >= 4 and re.search(rf"\b{re.escape(n)}\b", text):
            issues.append("names another candidate")
            break
    return issues


def _greeting(name: str | None) -> str:
    parts = (name or "").split()
    return f"Hi {parts[0]}," if len(parts) in (2, 3) and all(p[:1].isupper() for p in parts) else "Hello,"


def _signature(job: dict, s: dict) -> str:
    return f"{s['recruiter_name']}\n{job['company']}"


def _other_names(conn, app: dict) -> list[str]:
    rows = conn.execute("SELECT c.name FROM applications a JOIN candidates c ON c.id = a.candidate_id "
                        "WHERE a.job_id = ? AND a.id != ?", (app["job_id"], app["id"])).fetchall()
    return [p for r in rows for p in (r[0] or "").split() if len(p) >= 4 and p[:1].isupper()]


def rejection_body(app_name: str | None, job: dict, s: dict, d: RejectionDraft) -> str:
    areas = "\n".join(f"- {a.text}" for a in d.areas)
    return (f"{_greeting(app_name)}\n\n{d.opening}\n\nAreas that would strengthen an application for a role like "
            f"this:\n{areas}\n\nOne concrete next step: {d.next_step}\n\n{d.closing}\n\n{_signature(job, s)}")


def check_rejection(d: RejectionDraft, body: str, selected: list[dict], other_names: list[str]) -> list[str]:
    ids = {r["id"] for r in selected}
    errors = []
    want = min(3, max(1, len(selected)))
    if not min(2, want) <= len(d.areas) <= 3:
        errors.append(f"give {min(2, want)}–3 areas (got {len(d.areas)})")
    unmapped = [a.text for a in d.areas if a.reason_id not in ids]
    if unmapped:
        errors.append(f"every area must map to a selected reason id; not mapped: {unmapped}")
    if len({a.reason_id for a in d.areas}) != len(d.areas):
        errors.append("each area must come from a different reason")
    if d.next_step_type not in TYPE_LABELS:
        errors.append(f"next_step_type must be one of {sorted(TYPE_LABELS)}")
    if not re.search(r"\bnot (be )?(moving|move|going|proceed\w*) forward\b|\bdecided not to\b|\bwill not be\b",
                     d.opening, re.I):
        errors.append("the opening must state the decision clearly (not moving forward)")
    if word_count(body) > config.REJECTION_MAX_WORDS:
        errors.append(f"at most {config.REJECTION_MAX_WORDS} words (got {word_count(body)})")
    errors += banned_issues(body, other_names)
    return errors


NEXT_STEP_TEMPLATES = {
    "build_project": "Build a small project that uses {topic}, publish it, and describe a measured result.",
    "earn_certification": "Earn a recognized certification in {topic} with a credential ID or verification link.",
    "get_reference": "Ask a manager or supervisor for a reference that describes your work on {topic}.",
    "publish_work": "Publish work you already have on {topic} (a repository or portfolio page) and link it.",
    "contribute_or_compete": "Contribute to an open-source project or enter a competition involving {topic}.",
}


def rejection_template(job: dict, selected: list[dict]) -> RejectionDraft:
    picked = selected[:3] or [{"id": "none", "advice": "Add work that shows the role's required skills.",
                               "title": job["title"], "action_type": "build_project"}]
    first = picked[0]
    return RejectionDraft(
        subject=f"Your application for {job['title']} at {job['company']}",
        opening=f"Thank you for applying for the {job['title']} role at {job['company']} and for the time you put "
                f"into your application. After careful review, we have decided not to move forward with it.",
        areas=[Area(reason_id=r["id"], text=r["advice"]) for r in picked],
        next_step_type=first["action_type"],
        next_step=NEXT_STEP_TEMPLATES[first["action_type"]].format(topic=first["title"].lower()),
        closing="Thank you again for your interest, and best of luck in your search.")


def _rejection_prompt(job: dict, baseline, selected: list[dict], note: str | None) -> str:
    reasons = "\n".join(f"- id: {r['id']} | {r['title']}: {r['detail']} Advice: {r['advice']} "
                        f"(suggested action type: {r['action_type']})" for r in selected)
    types = "\n".join(f"- {k}: {v}" for k, v in TYPE_LABELS.items())
    parts = [wrap_tag("job", f"{job['title']} at {job['company']} ({baseline.level})"),
             wrap_tag("selected_reasons", reasons), wrap_tag("action_types", types)]
    if note:
        parts.append(wrap_tag("recruiter_note", note))
    return "\n\n".join(parts)


def rejection_inputs(conn, app: dict, job: dict) -> dict:
    """What drafting and checking need besides the reasons (no model)."""
    cand = store.get_candidate(conn, app["candidate_id"])
    return {"settings": settings(conn, job["id"]), "name": cand["name"], "others": _other_names(conn, app),
            "baseline": db.get_baseline(conn, job["baseline_id"])}


def ask_rejection(writer, job: dict, baseline, selected: list[dict], note: str | None,
                  errors: list[str] | None = None) -> RejectionDraft:
    """One model call. `errors` from the previous attempt are appended so the redraft fixes them."""
    user = _rejection_prompt(job, baseline, selected, note)
    if errors:
        user += "\n\nYour previous draft failed these checks:\n" + "\n".join(f"- {e}" for e in errors) + \
                "\nWrite it again with these fixed."
    return writer.parse(model=config.EMAIL_MODEL, system=load_prompt("email_rejection_system"), user=user,
                        output_type=RejectionDraft)


def save_rejection(conn, app: dict, job: dict, reasons: list[dict], note: str | None, d: RejectionDraft, model: str,
                   model_errors: list[str]) -> dict:
    inp = rejection_inputs(conn, app, job)
    selected = [r for r in reasons if r.get("selected")]
    body = rejection_body(inp["name"], job, inp["settings"], d)
    checks = {"errors": check_rejection(d, body, selected, inp["others"]) if selected else [],
              "model_errors": model_errors, "words": word_count(body), "areas": [a.model_dump() for a in d.areas],
              "next_step_type": d.next_step_type}
    return _save_draft(conn, app, job, "not_moving_forward", None, None, d.subject, body, model, reasons, note, checks)


def evidence_choices(conn, app: dict, job: dict) -> list[dict]:
    """Items the scheduling email may cite: this candidate's backed requirement evidence and "why" evidence."""
    baseline = db.get_baseline(conn, job["baseline_id"])
    profile = store.load_profile(conn, app["candidate_id"])
    scores = rc.run_scores(conn, app["run_id"]) if app["run_id"] else {}
    evidence = {e["id"]: e for e in store.list_evidence(conn, app["candidate_id"])}
    rows = [r for r in rc.requirements(baseline, job, scores, profile, evidence)
            if r["evidence_id"] and r["status"] == "met_backed" and r["quote"]]
    rows.sort(key=lambda r: (not r["must_have"], r["priority"] != "required", r["backing"] != "artifact"))
    out, seen = [], set()
    for r in rows:
        if r["evidence_id"] in seen:
            continue
        seen.add(r["evidence_id"])
        out.append({"id": r["evidence_id"], "source": r["source"], "text": r["quote"][:300], "shows": r["name"]})
    return out[:5]


def _logistics(job: dict, s: dict, stage: str) -> str:
    when = (f"Please pick a time that suits you here: {s['scheduling_link']}" if s.get("scheduling_link")
            else f"Please reply with a time that works for you within these windows: {s['availability']}.")
    return f"The next step is a {s['duration_minutes']}-minute {stage.lower()} for the {job['title']} role at " \
           f"{job['company']}. {when}"


def scheduling_body(name: str | None, job: dict, s: dict, stage: str, d: SchedulingDraft) -> str:
    return f"{_greeting(name)}\n\n{d.opening} {d.personal_line}\n\n{_logistics(job, s, stage)}\n\n{d.closing}\n\n" \
           f"{_signature(job, s)}"


def _mentions_item(line: str, item: dict) -> bool:
    words = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{3,}", f"{item['source']} {item['text']} {item['shows']}")}
    stop = {"with", "that", "this", "from", "your", "their", "work", "role", "project", "repo", "bullet", "data"}
    return bool({w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{3,}", line)} & (words - stop))


def check_scheduling(d: SchedulingDraft, body: str, choices: list[dict], others: list[str]) -> list[str]:
    errors = []
    item = next((c for c in choices if c["id"] == d.evidence_id), None)
    if item is None:
        errors.append("evidence_id must be one of the <evidence> ids")
    elif not _mentions_item(d.personal_line, item):
        errors.append("the personal line must refer to the cited evidence item")
    if word_count(body) > config.SCHEDULING_MAX_WORDS:
        errors.append(f"at most {config.SCHEDULING_MAX_WORDS} words (got {word_count(body)})")
    errors += banned_issues(body, others)
    return errors


def scheduling_template(job: dict, stage: str, choices: list[dict]) -> SchedulingDraft:
    item = choices[0] if choices else None
    where = (item["source"].split(": ", 1)[-1] if item else None)
    personal = (f"We were glad to see your work on {where}, especially how it shows {item['shows']}." if item
                else "We enjoyed reading about your experience.")
    return SchedulingDraft(subject=f"Next step: {stage} for {job['title']} at {job['company']}",
                           opening=f"Thank you for applying for the {job['title']} role. We'd like to invite you to "
                                   f"the next stage.",
                           personal_line=personal, evidence_id=item["id"] if item else "",
                           closing="We look forward to speaking with you.")


def interview_stage(conn, job_id: str, stage: dict) -> str:
    """The interview an email schedules: for the Scheduling stage, the stage after it; otherwise the stage itself."""
    if stage["name"] != "Scheduling":
        return stage["name"]
    order = [s for s in stages(conn, job_id) if not s["terminal"]]
    later = [s for s in order if s["position"] > stage["position"]]
    return later[0]["name"] if later else "interview"


def draft_scheduling(conn, app: dict, job: dict, stage: dict, writer, batch_id: str | None = None, *,
                     meeting: str | None = None, kind: str | None = None, token: str | None = None) -> dict:
    """A scheduling or stage email. `stage` is where the candidate moves when it is sent; `meeting` is the
    interview it invites to (default: the stage after Scheduling, or the stage itself)."""
    s = settings(conn, job["id"])
    meeting = meeting or interview_stage(conn, job["id"], stage)
    cand = store.get_candidate(conn, app["candidate_id"])
    others = _other_names(conn, app)
    choices = evidence_choices(conn, app, job)
    model, d, errors, first_errors = TEMPLATE_MODEL, None, [], []
    if writer is not None and choices:
        ev = "\n".join(f"- id: {c['id']} | {c['source']} | shows {c['shows']}: {c['text']}" for c in choices)
        user = "\n\n".join([wrap_tag("job", f"{job['title']} at {job['company']}"),
                            wrap_tag("next_stage", f"{meeting} ({s['duration_minutes']} minutes)"),
                            wrap_tag("evidence", ev)])
        try:
            for _ in range(2):
                d = writer.parse(model=config.EMAIL_MODEL, system=load_prompt("email_scheduling_system"), user=user,
                                 output_type=SchedulingDraft)
                errors = check_scheduling(d, scheduling_body(cand["name"], job, s, meeting, d), choices, others)
                if not errors:
                    model = config.EMAIL_MODEL
                    break
                first_errors = first_errors or errors
                user += "\n\nYour previous draft failed these checks:\n" + "\n".join(f"- {e}" for e in errors) + \
                        "\nWrite it again with these fixed."
        except LLMError as exc:
            log.warning("email draft failed, using the template: %s", exc)
            errors = [f"model unavailable: {exc}"]
    if model == TEMPLATE_MODEL:
        d = scheduling_template(job, meeting, choices)
    body = scheduling_body(cand["name"], job, s, meeting, d)
    checks = {"errors": check_scheduling(d, body, choices, others) if choices else [],
              "model_errors": first_errors or errors if model == TEMPLATE_MODEL else first_errors,
              "words": word_count(body), "evidence_id": d.evidence_id or None}
    kind = kind or ("scheduling" if stage["name"] == "Scheduling" or batch_id else "stage")
    return _save_draft(conn, app, job, kind, stage["id"], batch_id, d.subject, body, model, None, None, checks,
                       token=token)


# --- Emails: store, edit, send ------------------------------------------------------------------------------

def _save_draft(conn, app, job, kind, stage_id, batch_id, subject, body, model, reasons, note, checks, *,
                token: str | None = None, in_reply_to: str | None = None, attachments: list[dict] | None = None,
                interview_id: str | None = None) -> dict:
    """A new draft; an older draft of the same kind for this application is cancelled."""
    cand = store.get_candidate(conn, app["candidate_id"])
    eid = new_id()
    with conn:
        conn.execute("UPDATE emails SET status = 'cancelled', updated_at = ? WHERE application_id = ? AND kind = ? "
                     "AND status = 'draft'", (now_iso(), app["id"], kind))
        conn.execute("INSERT INTO emails (id, application_id, job_id, kind, stage_id, batch_id, status, intended_to, "
                     "subject, body, model, reasons_json, checks_json, created_at, updated_at, token, in_reply_to, "
                     "attachments_json, interview_id) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (eid, app["id"], job["id"], kind, stage_id, batch_id, cand.get("email"), subject, body, model,
                      json.dumps({"reasons": reasons, "note": note}) if reasons is not None else None,
                      json.dumps(checks), now_iso(), now_iso(), token, in_reply_to,
                      json.dumps(attachments) if attachments else None, interview_id))
    return get_email(conn, eid)


save_draft = _save_draft


def get_email(conn, email_id: str) -> dict | None:
    r = conn.execute("SELECT e.*, c.name AS candidate_name FROM emails e JOIN applications a ON a.id = e.application_id "
                     "JOIN candidates c ON c.id = a.candidate_id WHERE e.id = ?", (email_id,)).fetchone()
    if r is None:
        return None
    out = dict(r)
    out["reasons"] = json.loads(out.pop("reasons_json") or "null")
    out["checks"] = json.loads(out.pop("checks_json") or "{}")
    out["attachments"] = [{k: a[k] for k in ("filename", "content_type")} | {"size": len(a.get("text", ""))}
                          for a in json.loads(out.pop("attachments_json", None) or "[]")]
    out["template"] = out["model"] == TEMPLATE_MODEL
    out["label"] = "Template draft" if out["template"] else f"Drafted by {out['model']}"
    return out


def list_emails(conn, *, status: str | None = None, job_id: str | None = None, batch_id: str | None = None,
                application_id: str | None = None, kind: str | None = None) -> list[dict]:
    q, args = "SELECT id FROM emails WHERE 1 = 1", []
    if status == "failed":                                 # a failed send stays a draft (with its error) to retry
        q += " AND status = 'draft' AND error IS NOT NULL"
        status = None
    for col, val in (("status", status), ("job_id", job_id), ("batch_id", batch_id),
                     ("application_id", application_id), ("kind", kind)):
        if val:
            q += f" AND {col} = ?"
            args.append(val)
    return [get_email(conn, r[0]) for r in conn.execute(q + " ORDER BY COALESCE(sent_at, created_at) DESC, rowid DESC",
                                                         args)]


def edit_email(conn, email_id: str, subject: str | None, body: str | None) -> dict:
    e = get_email(conn, email_id)
    if e is None or e["status"] != "draft":
        raise ValueError("Only drafts can be edited.")
    app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (e["application_id"],)).fetchone())
    body = body if body is not None else e["body"]
    checks = {**e["checks"], "edited": True, "words": word_count(body),
              "warnings": banned_issues(body, _other_names(conn, app))}
    with conn:
        conn.execute("UPDATE emails SET subject = COALESCE(?, subject), body = ?, checks_json = ?, updated_at = ? "
                     "WHERE id = ?", (subject, body, json.dumps(checks), now_iso(), email_id))
    return get_email(conn, email_id)


def cancel_email(conn, email_id: str) -> dict:
    with conn:
        conn.execute("UPDATE emails SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'draft'",
                     (now_iso(), email_id))
    return get_email(conn, email_id)


def attachments_of(conn, email_id: str) -> list[dict]:
    row = conn.execute("SELECT attachments_json FROM emails WHERE id = ?", (email_id,)).fetchone()
    return json.loads((row and row[0]) or "[]")


def send_email(conn, email_id: str) -> dict:
    """Deliver one draft (outbox or SMTP, demo redirect applied) and log it. Called only by the Send action.

    Idempotent: the draft is claimed first (draft → sending in one UPDATE), so a refresh, a double click or two
    servers can deliver it once at most. A failed send can be retried: it returns to draft."""
    e = get_email(conn, email_id)
    if e is None or e["status"] != "draft":
        raise ValueError("Only drafts can be sent.")
    with conn:
        claimed = conn.execute("UPDATE emails SET status = 'sending', updated_at = ? WHERE id = ? AND status = 'draft'",
                               (now_iso(), email_id)).rowcount
    if claimed != 1:
        raise ValueError("This draft is already being sent or was sent.")
    subject = f"{e['subject']} [RS-{e['token']}]" if e.get("token") and f"[RS-{e['token']}]" not in e["subject"] \
        else e["subject"]
    out = mailer.address(e["intended_to"], e["candidate_name"], subject, e["body"])
    out.message_id = mailer.make_message_id()
    out.in_reply_to = e.get("in_reply_to")
    out.attachments = attachments_of(conn, email_id)
    try:
        mailer.deliver(out)
        status, error = "sent", None
    except mailer.SendRefused as exc:
        status, error = "failed", str(exc)
    except OSError as exc:                                 # SMTP connection or auth failure
        status, error = "failed", f"SMTP error: {exc}"
    with conn:
        conn.execute("UPDATE emails SET status = ?, actual_to = ?, subject = ?, body = ?, provider = ?, error = ?, "
                     "sent_at = ?, updated_at = ?, message_id = ? WHERE id = ?",
                     ("sent" if status == "sent" else "draft", out.actual_to,
                      out.subject if status == "sent" else e["subject"], out.body if status == "sent" else e["body"],
                      out.provider, error, now_iso() if status == "sent" else None, now_iso(),
                      out.message_id if status == "sent" else None, email_id))
        if status == "sent":
            conn.execute("INSERT INTO messages (id, application_id, direction, subject, body, token, message_id, "
                         "in_reply_to, intended_to, actual_to, from_addr, draft_id, status, source, sent_at, created_at) "
                         "VALUES (?, ?, 'out', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?, ?, ?)",
                         (new_id(), e["application_id"], out.subject, out.body, e.get("token"), out.message_id,
                          out.in_reply_to, e["intended_to"], out.actual_to, config.EMAIL_FROM or None, email_id,
                          out.provider, now_iso(), now_iso()))
    sent = get_email(conn, email_id)
    if status == "sent":
        app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (e["application_id"],)).fetchone())
        where = "to the outbox" if out.provider == "outbox" else f"to {out.actual_to}"
        log_activity(conn, app, "email", f"Email sent {where}: {e['subject']}", {"email_id": email_id})
    else:
        sent = {**sent, "status": "failed"}                # shown as failed; the row stays a draft to retry
    return sent


# --- Stage emails ---------------------------------------------------------------------------------------------

def send_scheduling(conn, email_id: str) -> dict:
    """Send one approved scheduling or stage email; the candidate moves to the email's stage."""
    sent = send_email(conn, email_id)
    if sent["status"] == "sent" and sent["stage_id"]:
        app = dict(conn.execute("SELECT * FROM applications WHERE id = ?", (sent["application_id"],)).fetchone())
        move(conn, app, sent["stage_id"], reason="scheduling email sent")
    return sent

