"""Hiring workflow data (specs/16 §6): workflows, events, the message thread, interviews, scorecards, Tasks.

The LangGraph graph (backend/hiring_flow.py) decides what happens next; this module stores and shows it.
Model calls here: `draft_message` (EMAIL_MODEL: nudge, answer, offer note) and `summarize` (FEEDBACK_MODEL).
Both are checked in code and fall back to a template / a code summary. Nothing here sends email: sending is
`workflows.send_email`, reached only from the graph's approve step.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from backend import recruiter as rc
from backend import replies
from backend import scheduling as sch
from backend import workflows as wf
from core import config, db
from core.db import new_id, now_iso
from providers.llm import LLMError
from core.text import wrap_tag
from extraction import store
from extraction.extract import load_prompt

log = logging.getLogger(__name__)

SPECIAL_STAGES = {"Shortlisted", "Scheduling", "Offer", "Hired"}
PHASES = ["scheduling", "scheduled", "interview", "feedback", "decision"]
PHASE_LABEL = {"scheduling": "Scheduling", "scheduled": "Scheduled", "interview": "Interview",
               "feedback": "Feedback", "decision": "Decision"}
PURPOSE = {"scheduling": "scheduling email", "nudge": "nudge", "answer": "answer to the candidate",
           "confirmation": "confirmation with calendar invite", "offer": "offer note",
           "not_moving_forward": "not-moving-forward email"}
RECOMMENDATIONS = {"strong_yes": "Strong yes", "yes": "Yes", "no": "No", "strong_no": "Strong no"}
STATUS_LABEL = {"needs_action": "Needs your action", "waiting_candidate": "Waiting for candidate",
                "scheduled": "Scheduled", "paused": "Paused", "closed": "Closed"}


def token_for(application_id: str) -> str:
    return hashlib.sha1(application_id.encode()).hexdigest()[:6]


def rounds(conn, job_id: str) -> list[dict]:
    """The interview stages of the job's plan, in order (each repeats Scheduling → … → Decision)."""
    return [s for s in wf.stages(conn, job_id) if not s["terminal"] and s["name"] not in SPECIAL_STAGES]


def app_row(conn, app_id: str) -> dict:
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown application.")
    return dict(row)


def candidate_name(conn, app: dict) -> str | None:
    return store.get_candidate(conn, app["candidate_id"])["name"]


# --- Workflow row and events ------------------------------------------------------------------------------------

def get(conn, app_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM workflows WHERE application_id = ?", (app_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["payload"] = json.loads(out.pop("payload_json") or "{}")
    return out


def create(conn, app: dict, thread_id: str) -> dict:
    with conn:
        conn.execute("INSERT OR REPLACE INTO workflows (application_id, job_id, thread_id, token, stage, round, phase, "
                     "step, title, payload_json, status, created_at, updated_at, step_since) "
                     "VALUES (?, ?, ?, ?, 'Shortlisted', 0, 'shortlisted', 'starting', NULL, '{}', 'needs_action', "
                     "?, ?, ?)", (app["id"], app["job_id"], thread_id, token_for(app["id"]), now_iso(), now_iso(),
                                  now_iso()))
    event(conn, app["id"], "started", {"thread_id": thread_id}, "recruiter")
    event(conn, app["id"], "phase", {"key": "shortlisted"}, "agent")
    return get(conn, app["id"])


def event(conn, app_id: str, type_: str, payload: dict | None = None, actor: str = "agent",
          at: str | None = None) -> None:
    with conn:
        conn.execute("INSERT INTO workflow_events (id, application_id, type, payload_json, actor, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (new_id(), app_id, type_, json.dumps(payload or {}), actor,
                                                   at or now_iso()))


def events(conn, app_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM workflow_events WHERE application_id = ? ORDER BY created_at, rowid", (app_id,))
    return [{k: r[k] for k in ("id", "type", "actor", "created_at")} | {"payload": json.loads(r["payload_json"] or "{}")}
            for r in rows]


def phase_key(round_: int | None, phase: str) -> str:
    return phase if phase in ("shortlisted", "offer", "hired", "closed") else f"{round_}:{phase}"


def stage_text(conn, job_id: str, round_: int | None, phase: str) -> str:
    if phase in ("shortlisted", "offer", "hired", "closed"):
        return {"shortlisted": "Shortlisted", "offer": "Offer", "hired": "Hired", "closed": "Closed"}[phase]
    rs = rounds(conn, job_id)
    name = rs[round_]["name"] if rs and round_ is not None and round_ < len(rs) else "Interview"
    return f"{name} · {PHASE_LABEL[phase]}"


def set_step(conn, app_id: str, *, step: str, status: str, title: str, payload: dict | None = None,
             round_: int | None = None, phase: str | None = None, due: datetime | None = None) -> dict:
    """What the workflow waits for now. Runs again when the graph resumes the step: writing it is idempotent.
    A paused workflow stays paused (the new status is restored on resume). A new phase is logged once."""
    row = get(conn, app_id)
    round_ = row["round"] if round_ is None else round_
    phase = phase or row["phase"]
    key, old_key = phase_key(round_, phase), phase_key(row["round"], row["phase"])
    paused = row["status"] == "paused"
    changed = (step, json.dumps(payload or {}, sort_keys=True)) != (row["step"], json.dumps(row["payload"], sort_keys=True))
    with conn:
        conn.execute("UPDATE workflows SET step = ?, title = ?, payload_json = ?, status = ?, paused_status = ?, "
                     "round = ?, phase = ?, stage = ?, next_due = ?, step_since = ?, updated_at = ? "
                     "WHERE application_id = ?",
                     (step, title, json.dumps(payload or {}), "paused" if paused else status,
                      status if paused else None, round_, phase, stage_text(conn, row["job_id"], round_, phase),
                      due.isoformat() if due else None, now_iso() if changed else row["step_since"], now_iso(), app_id))
    if key != old_key:
        event(conn, app_id, "phase", {"key": key}, "agent")
    return get(conn, app_id)


def close(conn, app_id: str, outcome: str) -> None:
    row = get(conn, app_id)
    if row is None or row["status"] == "closed":
        return
    phase = {"hired": "hired"}.get(outcome, "closed")
    with conn:
        conn.execute("UPDATE workflows SET status = 'closed', step = 'closed', title = NULL, payload_json = '{}', "
                     "outcome = ?, phase = ?, stage = ?, next_due = NULL, paused_status = NULL, updated_at = ? "
                     "WHERE application_id = ?", (outcome, phase, {"hired": "Hired"}.get(outcome, "Closed"),
                                                  now_iso(), app_id))
    event(conn, app_id, "phase", {"key": phase}, "agent")
    event(conn, app_id, "closed", {"outcome": outcome}, "agent")


def pause(conn, app_id: str, actor: str = "recruiter", why: str = "paused") -> dict:
    row = get(conn, app_id)
    if row is None or row["status"] in ("closed", "paused"):
        raise ValueError("Only an open, running workflow can be paused.")
    with conn:
        conn.execute("UPDATE workflows SET paused_status = status, status = 'paused', updated_at = ? "
                     "WHERE application_id = ?", (now_iso(), app_id))
    event(conn, app_id, why, {}, actor)
    return get(conn, app_id)


def unpause(conn, app_id: str, actor: str = "recruiter") -> dict:
    row = get(conn, app_id)
    if row is None or row["status"] != "paused":
        raise ValueError("This workflow is not paused.")
    with conn:
        conn.execute("UPDATE workflows SET status = COALESCE(paused_status, 'needs_action'), paused_status = NULL, "
                     "step_since = ?, updated_at = ? WHERE application_id = ?", (now_iso(), now_iso(), app_id))
    event(conn, app_id, "resumed", {}, actor)
    return get(conn, app_id)


# --- Messages (the thread) --------------------------------------------------------------------------------------

def _message(r) -> dict:
    out = dict(r)
    out["reading"] = json.loads(out.pop("reading_json") or "null")
    return out


def messages(conn, app_id: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM messages WHERE application_id = ? ORDER BY COALESCE(sent_at, created_at), rowid",
                        (app_id,))
    return [_message(r) for r in rows]


def get_message(conn, message_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return _message(r) if r else None


def unread(conn, app_id: str) -> list[dict]:
    return [m for m in messages(conn, app_id) if m["direction"] == "in" and m["status"] == "new"]


def last_out(conn, app_id: str) -> dict | None:
    out = [m for m in messages(conn, app_id) if m["direction"] == "out"]
    return out[-1] if out else None


def record_inbound(conn, app_id: str, text: str, *, source: str, subject: str | None = None,
                   message_id: str | None = None, in_reply_to: str | None = None, from_addr: str | None = None,
                   at: str | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("The reply is empty.")
    last = last_out(conn, app_id)
    mid = new_id()
    with conn:
        conn.execute("INSERT INTO messages (id, application_id, direction, subject, body, token, message_id, "
                     "in_reply_to, from_addr, status, source, sent_at, created_at) "
                     "VALUES (?, ?, 'in', ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)",
                     (mid, app_id, subject or (f"Re: {last['subject']}" if last else "Reply"), text,
                      token_for(app_id), message_id, in_reply_to or (last or {}).get("message_id"), from_addr,
                      source, at or now_iso(), now_iso()))
    event(conn, app_id, "reply_received", {"message_id": mid, "source": source}, "candidate")
    return get_message(conn, mid)


def save_reading(conn, message_id: str, reading: dict) -> None:
    with conn:
        conn.execute("UPDATE messages SET reading_json = ?, status = 'read' WHERE id = ?",
                     (json.dumps(reading), message_id))


# --- Interviews -------------------------------------------------------------------------------------------------

def get_interview(conn, interview_id: str | None) -> dict | None:
    if not interview_id:
        return None
    r = conn.execute("SELECT * FROM interviews WHERE id = ?", (interview_id,)).fetchone()
    return dict(r) if r else None


def interviews(conn, app_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM interviews WHERE application_id = ? ORDER BY start",
                                          (app_id,))]


def booked(conn, job_id: str, exclude_app: str | None = None) -> list[tuple[datetime, datetime]]:
    rows = conn.execute("SELECT start, end FROM interviews WHERE job_id = ? AND status IN ('tentative', 'confirmed') "
                        "AND application_id != ?", (job_id, exclude_app or ""))
    return [(datetime.fromisoformat(r[0]), datetime.fromisoformat(r[1])) for r in rows]


def create_interview(conn, app: dict, stage: str, start: datetime, end: datetime) -> dict:
    with conn:
        conn.execute("UPDATE interviews SET status = 'cancelled' WHERE application_id = ? AND status = 'tentative'",
                     (app["id"],))
        iid = new_id()
        conn.execute("INSERT INTO interviews (id, application_id, job_id, stage, start, end, timezone, ics_uid, status, "
                     "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'tentative', ?)",
                     (iid, app["id"], app["job_id"], stage, start.isoformat(), end.isoformat(), sch.tz_name(start),
                      sch.new_uid(), now_iso()))
    return get_interview(conn, iid)


def set_interview(conn, interview_id: str, status: str) -> None:
    with conn:
        conn.execute("UPDATE interviews SET status = ? WHERE id = ?", (status, interview_id))


def interview_label(iv: dict | None) -> str | None:
    if not iv or not iv.get("start"):
        return None
    start = datetime.fromisoformat(iv["start"]).astimezone(sch.zone(iv["timezone"]))
    return sch.label(start, datetime.fromisoformat(iv["end"]).astimezone(sch.zone(iv["timezone"])))


# --- Scorecards and the feedback summary ---------------------------------------------------------------------------

def add_scorecard(conn, app_id: str, *, interview_id: str | None, stage: str, interviewer: str, ratings: list[dict],
                  notes: str | None, recommendation: str) -> dict:
    interviewer = (interviewer or "").strip()
    if not interviewer:
        raise ValueError("Name the interviewer.")
    if recommendation not in RECOMMENDATIONS:
        raise ValueError(f"recommendation must be one of {list(RECOMMENDATIONS)}")
    clean = []
    for r in ratings:
        rating = int(r.get("rating") or 0)
        if not 1 <= rating <= 4:
            raise ValueError(f"Rate {r.get('area')!r} from 1 to 4.")
        clean.append({"area": str(r["area"]), "rating": rating, "notes": (r.get("notes") or "").strip()})
    if not clean:
        raise ValueError("Rate at least one focus area.")
    sid = new_id()
    with conn:
        conn.execute("INSERT INTO scorecards (id, application_id, interview_id, stage, interviewer, ratings_json, notes, "
                     "recommendation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (sid, app_id, interview_id, stage, interviewer, json.dumps(clean), (notes or "").strip(),
                      recommendation, now_iso()))
    event(conn, app_id, "feedback_entered", {"scorecard_id": sid, "interviewer": interviewer, "stage": stage},
          "recruiter")
    return next(c for c in scorecards(conn, app_id) if c["id"] == sid)


def scorecards(conn, app_id: str, stage: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM scorecards WHERE application_id = ?", [app_id]
    if stage:
        q += " AND stage = ?"
        args.append(stage)
    out = []
    for r in conn.execute(q + " ORDER BY created_at, rowid", args):
        c = dict(r)
        c["ratings"] = json.loads(c.pop("ratings_json") or "[]")
        out.append(c)
    return out


class Cite(BaseModel):
    scorecard_id: str
    quote: str = Field(description="Words copied exactly from that scorecard's notes.")


class Point(BaseModel):
    text: str
    quotes: list[Cite]


class FeedbackSummary(BaseModel):
    agreements: list[Point] = Field(default_factory=list)
    disagreements: list[Point] = Field(default_factory=list)
    open_questions: list[Point] = Field(default_factory=list)


RECOMMENDS = re.compile(r"\b(recommend\w*|should (?:be )?(?:hire|hired|advance|advanced|reject|rejected|move|moved)|"
                        r"(?:hire|advance|reject) (?:her|him|them|the candidate)|move (?:her|him|them) forward|"
                        r"not move forward|strong hire|no hire)\b", re.I)


def _card_text(card: dict) -> str:
    return "\n".join([card.get("notes") or "", *[r["notes"] for r in card["ratings"]]])


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def check_summary(summary: FeedbackSummary, cards: list[dict]) -> tuple[dict, list[str]]:
    """Keep quotes found in their scorecard; drop points without one, and points that recommend a decision."""
    texts = {c["id"]: _norm(_card_text(c)) for c in cards}
    out, dropped = {}, []
    for key in ("agreements", "disagreements", "open_questions"):
        keep = []
        for p in getattr(summary, key)[:4]:
            quotes = [q for q in p.quotes if q.scorecard_id in texts and q.quote.strip()
                      and _norm(q.quote) in texts[q.scorecard_id]]
            if RECOMMENDS.search(p.text) or wf.banned_issues(p.text):
                dropped.append(f"{key}: dropped (recommends a decision or mentions a personal characteristic): {p.text}")
            elif not quotes:
                dropped.append(f"{key}: dropped (no quote found in the scorecards): {p.text}")
            else:
                keep.append({"text": p.text, "quotes": [q.model_dump() for q in quotes]})
        out[key] = keep
    return out, dropped


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text or "") if s.strip()]


def code_summary(cards: list[dict]) -> dict:
    """Per focus area: ratings within 1 of each other agree, 2+ apart disagree. Open questions are note
    sentences with a question mark or "follow up / probe / unclear / not sure / next round"."""
    areas: dict[str, list[tuple[dict, dict]]] = {}
    for c in cards:
        for r in c["ratings"]:
            areas.setdefault(r["area"], []).append((c, r))
    out = {"agreements": [], "disagreements": [], "open_questions": []}
    for area, pairs in areas.items():
        vals = [r["rating"] for _, r in pairs]
        quotes = [{"scorecard_id": c["id"], "quote": _sentences(r["notes"])[0]} for c, r in pairs if r["notes"]]
        rated = f"{min(vals)}" if min(vals) == max(vals) else f"{min(vals)}–{max(vals)}"
        if len(pairs) == 1:
            out["agreements"].append({"text": f"{area}: rated {rated} of 4 (one interviewer).", "quotes": quotes})
        elif max(vals) - min(vals) <= 1:
            out["agreements"].append({"text": f"{area}: interviewers agree, rated {rated} of 4.", "quotes": quotes})
        else:
            out["disagreements"].append({"text": f"{area}: ratings differ ({rated} of 4).", "quotes": quotes})
    for c in cards:
        for s in _sentences(_card_text(c)):
            if "?" in s or re.search(r"\b(follow up|follow-up|probe|unclear|not sure|next round|verify)\b", s, re.I):
                out["open_questions"].append({"text": s, "quotes": [{"scorecard_id": c["id"], "quote": s}]})
    out["open_questions"] = out["open_questions"][:4]
    return out


def summarize(writer, stage: str, focus: list[str], cards: list[dict]) -> dict:
    """FEEDBACK_MODEL summary of the scorecards (quote-checked, no recommendation); code summary otherwise.
    The interviewers' own recommendations are listed as data, never combined into one."""
    tally = {k: sum(1 for c in cards if c["recommendation"] == k) for k in RECOMMENDATIONS}
    base = {"stage": stage, "scorecards": len(cards), "recommendations": tally}
    if writer is not None:
        blocks = "\n\n".join(
            f"<scorecard id=\"{c['id']}\">\ninterviewer: {c['interviewer']}\n" +
            "\n".join(f"- {r['area']}: {r['rating']}/4. {r['notes']}" for r in c["ratings"]) +
            f"\noverall notes: {c['notes']}\nrecommendation: {RECOMMENDATIONS[c['recommendation']]}\n</scorecard>"
            for c in cards)
        user = "\n\n".join([wrap_tag("stage", f"{stage}. Focus areas: {', '.join(focus) or 'none set'}"),
                            wrap_tag("scorecards", blocks)])
        try:
            s = writer.parse(model=config.FEEDBACK_MODEL, system=load_prompt("feedback_summary_system"), user=user,
                             output_type=FeedbackSummary)
            points, dropped = check_summary(s, cards)
            return base | points | {"method": "model", "model": config.FEEDBACK_MODEL, "notes": dropped}
        except LLMError as exc:
            log.warning("feedback model unavailable, code summary: %s", exc)
            return base | code_summary(cards) | {"method": "code", "model": None,
                                                 "notes": ["Check this summary."]}
    return base | code_summary(cards) | {"method": "code", "model": None,
                                         "notes": []}


# --- Drafts: nudge, answer, offer note (model), confirmation (code) ---------------------------------------------------

class MessageDraft(BaseModel):
    subject: str
    body: str = Field(description="1-3 short paragraphs. No greeting line, no sign-off.")


OFFER_TERMS = re.compile(r"\$|€|£|\b\d{2,3}[,.]?\d{3}\b|\b(salary|equity|bonus|stock|compensation|start date|"
                         r"per (year|annum|hour))\b", re.I)


def _facts(conn, job: dict, stage: str) -> tuple[str, dict]:
    s = wf.settings(conn, job["id"])
    focus = next((x["focus"] for x in wf.stages(conn, job["id"]) if x["name"] == stage), [])
    when = f"scheduling link: {s['scheduling_link']}" if s.get("scheduling_link") else \
        f"availability windows: {s['availability']}"
    facts = f"stage: {stage}\nduration: {s['duration_minutes']} minutes\n{when}\n" \
            f"focus areas: {', '.join(focus) or 'not set'}"
    return facts, s


def template_message(purpose: str, job: dict, s: dict, stage: str) -> MessageDraft:
    when = (f"You can pick a time here: {s['scheduling_link']}" if s.get("scheduling_link")
            else f"Would a time within {s['availability']} work for you?")
    if purpose == "nudge":
        return MessageDraft(subject=f"Following up: {stage} for {job['title']}",
                            body=f"I wanted to follow up on my note about the {s['duration_minutes']}-minute "
                                 f"{stage.lower()} for the {job['title']} role at {job['company']}. {when}")
    if purpose == "answer":
        return MessageDraft(subject=f"Re: {stage} for {job['title']}",
                            body="Thank you for your question. I'll check and follow up shortly. In the meantime, "
                                 f"the {stage.lower()} is a {s['duration_minutes']}-minute conversation. {when}")
    return MessageDraft(subject=f"{job['title']} at {job['company']}: next steps",
                        body=f"Thank you for your time throughout the process. I'm glad to let you know the team "
                             f"would like to make you an offer for the {job['title']} role. I'll follow up shortly "
                             f"with the details.")


def message_body(name: str | None, job: dict, s: dict, d: MessageDraft) -> str:
    return f"{wf._greeting(name)}\n\n{d.body.strip()}\n\n{wf._signature(job, s)}"


def check_message(purpose: str, d: MessageDraft, body: str, others: list[str]) -> list[str]:
    errors = []
    if wf.word_count(body) > config.MESSAGE_MAX_WORDS:
        errors.append(f"at most {config.MESSAGE_MAX_WORDS} words (got {wf.word_count(body)})")
    if purpose == "offer" and OFFER_TERMS.search(d.body):
        errors.append("the offer note must not state terms (pay, equity, start date)")
    errors += wf.banned_issues(body, others)
    return errors


def draft_message(conn, app: dict, job: dict, purpose: str, stage: str, writer, *, token: str,
                  candidate_message: str | None = None, in_reply_to: str | None = None) -> dict:
    """Nudge, answer or offer note: model draft (checked, one redraft) or the template."""
    facts, s = _facts(conn, job, stage)
    name = candidate_name(conn, app)
    others = wf._other_names(conn, app)
    model, d, first_errors = wf.TEMPLATE_MODEL, None, []
    if writer is not None:
        parts = [wrap_tag("purpose", "offer_note" if purpose == "offer" else purpose),
                 wrap_tag("job", f"{job['title']} at {job['company']}"), wrap_tag("facts", facts)]
        if candidate_message:
            parts.append(wrap_tag("candidate_message", candidate_message))
        user = "\n\n".join(parts)
        try:
            for _ in range(2):
                d = writer.parse(model=config.EMAIL_MODEL, system=load_prompt("email_message_system"), user=user,
                                 output_type=MessageDraft)
                errors = check_message(purpose, d, message_body(name, job, s, d), others)
                if not errors:
                    model = config.EMAIL_MODEL
                    break
                first_errors = first_errors or errors
                user += "\n\nYour previous draft failed these checks:\n" + "\n".join(f"- {e}" for e in errors) + \
                        "\nWrite it again with these fixed."
        except LLMError as exc:
            first_errors = [f"model unavailable: {exc}"]
    if model == wf.TEMPLATE_MODEL:
        d = template_message(purpose, job, s, stage)
    body = message_body(name, job, s, d)
    checks = {"errors": check_message(purpose, d, body, others), "model_errors": first_errors,
              "words": wf.word_count(body)}
    return wf.save_draft(conn, app, job, purpose, None, None, d.subject, body, model, None, None, checks,
                         token=token, in_reply_to=in_reply_to)


def draft_confirmation(conn, app: dict, job: dict, iv: dict, token: str) -> dict:
    """Confirmation of the chosen slot, with the .ics invite attached (code template, no model)."""
    s = wf.settings(conn, job["id"])
    cand = store.get_candidate(conn, app["candidate_id"])
    start = datetime.fromisoformat(iv["start"]).astimezone(sch.zone(iv["timezone"]))
    end = datetime.fromisoformat(iv["end"]).astimezone(sch.zone(iv["timezone"]))
    where = f"Join here: {s['scheduling_link']}" if s.get("scheduling_link") else \
        "We'll share the meeting link before the call."
    body = (f"{wf._greeting(cand['name'])}\n\nThank you for confirming. This confirms your {s['duration_minutes']}-"
            f"minute {iv['stage'].lower()} for the {job['title']} role at {job['company']} on {sch.label(start, end)}. "
            f"A calendar invite is attached. {where}\n\nIf the time no longer works, just reply to this email.\n\n"
            f"{wf._signature(job, s)}")
    redirect = config.DEMO_EMAIL_TO
    invite = sch.Invite(uid=iv["ics_uid"], start=start, end=end,
                        summary=f"{iv['stage']}: {job['title']} at {job['company']}",
                        description=f"{s['duration_minutes']}-minute {iv['stage'].lower()} with {s['recruiter_name']}.",
                        organizer=config.EMAIL_FROM or None, organizer_name=s["recruiter_name"],
                        attendee=redirect or cand.get("email") or "candidate@example.com",
                        attendee_name=cand["name"])
    attachment = sch.CALENDAR.attachment(invite)
    checks = {"errors": wf.banned_issues(body, wf._other_names(conn, app)), "model_errors": [],
              "words": wf.word_count(body)}
    return wf.save_draft(conn, app, job, "confirmation", None, None,
                         f"Confirmed: {iv['stage']} for {job['title']}, {sch.label(start)}", body, wf.TEMPLATE_MODEL,
                         None, None, checks, token=token, attachments=[attachment] if attachment else None,
                         interview_id=iv["id"])


# --- Tasks and board badges ----------------------------------------------------------------------------------------

def round_label(conn, row: dict) -> str | None:
    """"Round 2 · Interview" while a candidate is in an interview round (the board reuses one Scheduling column)."""
    if row["phase"] in ("shortlisted", "offer", "hired", "closed") or row["round"] is None:
        return None
    rs = rounds(conn, row["job_id"])
    return f"Round {row['round'] + 1} · {rs[row['round']]['name']}" if row["round"] < len(rs) else None


def card_status(conn, row: dict | None) -> dict | None:
    out = _card_status(conn, row)
    return out | {"round": round_label(conn, row)} if out else None


def _card_status(conn, row: dict | None) -> dict | None:
    if row is None or row["status"] == "closed":
        return None
    if row["status"] == "scheduled":
        iv = get_interview(conn, row["payload"].get("interview_id"))
        when = interview_label(iv)
        return {"status": "scheduled", "label": f"Scheduled {when.split('–')[0]}" if when else "Scheduled (outside RightStaff)"}
    if row["step"] == "enter_feedback" and row["status"] != "paused":
        return {"status": "feedback_due", "label": "Feedback due"}
    return {"status": row["status"], "label": STATUS_LABEL[row["status"]]}


def tasks(conn) -> list[dict]:
    """Everything waiting for the recruiter across jobs, oldest first."""
    out = []
    rows = conn.execute("SELECT w.application_id FROM workflows w WHERE w.status != 'closed'").fetchall()
    for (app_id,) in rows:
        row = get(conn, app_id)
        app = app_row(conn, app_id)
        job = rc.get_job(conn, app["job_id"])
        name = candidate_name(conn, app) or "Applicant"
        base = {"application_id": app_id, "candidate": name, "job_id": job["id"], "job_title": job["title"],
                "company": job["company"], "stage": row["stage"], "step": row["step"]}
        if row["status"] == "needs_action":
            out.append(base | {"title": f"{row['title']} – {name}", "since": row["step_since"], "kind": row["step"]})
        new = unread(conn, app_id)
        if new and row["step"] != "await_reply":
            out.append(base | {"title": f"Reply received – {name}", "since": new[0]["created_at"], "kind": "reply"})
    return sorted(out, key=lambda t: t["since"] or "")


# --- Page view ------------------------------------------------------------------------------------------------------

def timeline(conn, job_id: str, row: dict | None, evs: list[dict]) -> list[dict]:
    rs = rounds(conn, job_id)
    items = [{"key": "shortlisted", "name": "Shortlisted", "group": None}]
    for i, r in enumerate(rs):
        items += [{"key": f"{i}:{p}", "name": PHASE_LABEL[p], "group": r["name"]} for p in PHASES]
    items += [{"key": "offer", "name": "Offer", "group": None}, {"key": "hired", "name": "Hired", "group": None}]
    dates = {e["payload"]["key"]: e["created_at"] for e in evs if e["type"] == "phase"}
    skipped = {k: e["payload"].get("note") for e in evs if e["type"] == "skipped" for k in e["payload"]["keys"]}
    keys = [i["key"] for i in items]
    if row is None:
        cur = -1
    else:
        key = phase_key(row["round"], row["phase"])
        cur = keys.index(key) if key in keys else -1
        if row["phase"] == "closed":                 # closed early: everything reached is done
            cur = max((keys.index(k) for k in dates if k in keys), default=0)
    for n, item in enumerate(items):
        if item["key"] in skipped and n < cur:
            item |= {"status": "skipped", "note": skipped[item["key"]]}
        elif row is not None and row["status"] == "closed" and row["phase"] == "closed" and n >= cur:
            item["status"] = "done" if n == cur else "not_reached"
        else:
            item["status"] = "done" if n < cur else "current" if n == cur else "upcoming"
        item["date"] = dates.get(item["key"]) if item["status"] in ("done", "current", "skipped") else None
    if row is not None and row["phase"] == "closed":
        label = {"withdrew": "Withdrew", "declined": "Declined the offer"}.get(row["outcome"], "Not moving forward")
        items.append({"key": "closed", "name": label, "group": None, "status": "closed", "date": dates.get("closed")})
    return items


EVENT_TEXT = {
    "started": "Workflow started", "drafted": "Draft ready: {purpose}", "email_sent": "Sent: {subject}",
    "email_discarded": "Draft discarded: {purpose}", "send_failed": "Send failed: {error}",
    "reply_received": "Reply received ({source})", "reply_read": "Reply read as {label} ({method})",
    "slots_proposed": "{n} slots proposed", "slot_confirmed": "Slot chosen: {label}",
    "interview_done": "Interview marked done", "feedback_entered": "Scorecard from {interviewer}",
    "feedback_summarized": "Feedback summarized ({method})", "decision": "Decision: {decision}",
    "stage_moved": "Moved on the board to {stage} (nothing sent)", "skipped": "Skipped: {note}",
    "closed": "Closed: {outcome}", "paused": "Paused", "on_hold": "Put on hold", "resumed": "Resumed",
    "chosen": "Next step chosen: {option}", "withdrawal_confirmed": "Withdrawal confirmed",
}


def _event_text(e: dict) -> str | None:
    if e["type"] == "phase":
        return None
    t = EVENT_TEXT.get(e["type"], e["type"])
    try:
        return t.format(**{k: (PURPOSE.get(v, v) if k == "purpose" else v) for k, v in e["payload"].items()})
    except (KeyError, IndexError):
        return t.split(":")[0]


def view(conn, app_id: str) -> dict:
    app = app_row(conn, app_id)
    job = rc.get_job(conn, app["job_id"])
    row = get(conn, app_id)
    applicant = rc.applicant_row(conn, app, job)
    evs = events(conn, app_id)
    step = None
    if row is not None and row["status"] != "closed":
        p = row["payload"]
        step = {"step": row["step"], "title": row["title"], "status": row["status"], "since": row["step_since"],
                "due": row["next_due"], **p}
        if p.get("email_id"):
            step["email"] = wf.get_email(conn, p["email_id"])
        if p.get("reply_id"):
            step["reply"] = get_message(conn, p["reply_id"])
        if p.get("interview_id"):
            iv = get_interview(conn, p["interview_id"])
            step["interview"] = iv | {"label": interview_label(iv)} if iv else None
        if row["step"] in ("enter_feedback", "decide"):
            step["scorecards"] = scorecards(conn, app_id, p.get("stage"))
        step["unread"] = len(unread(conn, app_id))
    rs = rounds(conn, job["id"])
    return {
        "application": {"id": app_id, "name": applicant["name"], "job_id": job["id"], "job_title": job["title"],
                        "company": job["company"], "board_stage": applicant.get("stage"), "match": applicant["match"],
                        "status": app["status"], "current_role": applicant["current_role"]},
        "workflow": None if row is None else {k: row[k] for k in ("status", "step", "stage", "round", "phase", "outcome",
                                                                 "next_due", "token", "created_at", "paused_status")}
        | {"status_label": STATUS_LABEL[row["status"]], "badge": card_status(conn, row)},
        "step": step,
        "timeline": timeline(conn, job["id"], row, evs),
        "rounds": [{"name": r["name"], "focus": r["focus"]} for r in rs],
        "thread": messages(conn, app_id),
        "drafts": [e for e in wf.list_emails(conn, application_id=app_id) if e["status"] == "draft"],
        "history": [{"type": e["type"], "actor": e["actor"], "at": e["created_at"], "text": _event_text(e),
                     "payload": e["payload"]} for e in evs if _event_text(e)],
        "interviews": [iv | {"label": interview_label(iv)} for iv in interviews(conn, app_id)],
        "scorecards": scorecards(conn, app_id),
        "settings": wf.settings(conn, job["id"]),
        "canned_replies": [{"key": k, "text": v} for k, v in replies.CANNED.items()],
    }
