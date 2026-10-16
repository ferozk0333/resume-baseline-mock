"""Creating, editing and publishing jobs (specs/14). No model calls here (reading a JD is extraction/job_reader).

A job is a `jobs` row plus its own baseline (same schema as the role baselines, `owner_job_id` set), created when
the job is published. Until then the form lives in `jobs.draft_json`, autosaved on every change, so a recruiter
can leave and resume at the same step. Editing a published job works on a draft too; saving applies it:
- requirement or "What good looks like" changes alter the baseline hash → applicants are re-scored (the caller
  asks first: "N applicants will be re-scored. About M model calls.");
- details, interview plan and weights changes make no model calls.
Scoring formulas, weights and logic do not change: a job-owned baseline is read exactly like a seed baseline.
"""
from __future__ import annotations

import copy
import json

from backend import recruiter as rc
from backend import workflows as wf
from core import config, db
from core.db import new_id, now_iso
from core.models import REACH_LABELS, Baseline, BaselineCriterion, level_years_warning

STATUSES = ("draft", "open", "closed")
LEVELS = ("entry", "mid", "senior", "lead")
WORKPLACES = ("on_site", "hybrid", "remote")
MUST_HAVE_DEFAULT = 4                     # the first four required items are pre-ticked
REQUIRED_WARNING = 12
EMPTY_DETAILS = {"title": "", "company": "", "department": "", "location": "", "workplace": None,
                 "employment_type": "Full-time", "level": "entry", "min_years": 0.0, "short_description": "",
                 "responsibilities": []}


def default_plan() -> dict:
    return {"stages": list(wf.DEFAULT_STAGES), **wf.DEFAULT_SETTINGS}


def default_ranking() -> dict:
    return {"preset": None, "weights": config.default_weights()}


def empty_draft(source: str) -> dict:
    return {"step": 2, "source": source, "details": dict(EMPTY_DETAILS), "detail_quotes": {}, "requirements": [],
            "good": {"scope_expectation": "", "complexity_criteria": "", "expected_reach": None}, "good_quotes": {},
            "ranking": default_ranking(), "plan": default_plan(), "extraction": None}


def _row(r: dict) -> dict:
    """A requirement row of the form, with defaults."""
    return {"key": r.get("key") or new_id()[:8], "criterion_id": r.get("criterion_id"), "name": (r.get("name") or "").strip(),
            "kind": r.get("kind") or "skill", "priority": r.get("priority") or "required",
            "must_have": bool(r.get("must_have")), "aliases": list(r.get("aliases") or []),
            "suggested_aliases": list(r.get("suggested_aliases") or []), "description": r.get("description") or "",
            "from_jd": bool(r.get("from_jd")), "quote": r.get("quote")}


def pre_tick(rows: list[dict]) -> list[dict]:
    required = [r for r in rows if r["priority"] == "required"][:MUST_HAVE_DEFAULT]
    keys = {r["key"] for r in required}
    return [{**r, "must_have": r["key"] in keys} for r in rows]


# --- Rows ------------------------------------------------------------------------------------------------------

def get(conn, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["weights"] = json.loads(job.pop("weights_json") or "null") or config.default_weights()
    job["must_haves"] = json.loads(job.pop("must_haves_json") or "[]")
    job["details"] = {**EMPTY_DETAILS, **json.loads(job.pop("details_json") or "{}")}
    job["draft"] = json.loads(job.pop("draft_json") or "null")
    job["status"] = job.get("status") or "open"
    return job


def summary(conn, job: dict) -> dict:
    """A Jobs-page row."""
    n = conn.execute("SELECT COUNT(*) FROM applications WHERE job_id = ?", (job["id"],)).fetchone()[0]
    d = job["draft"]["details"] if job["status"] == "draft" and job.get("draft") else job["details"]
    return {"id": job["id"], "title": d.get("title") or job.get("title") or "Untitled job",
            "company": d.get("company") or job.get("company") or "", "location": d.get("location") or "",
            "workplace": d.get("workplace"), "applicants": n, "created_at": job["created_at"], "status": job["status"],
            "updated_at": job.get("updated_at"), "has_draft": bool(job.get("draft")) and job["status"] != "draft",
            "step": (job.get("draft") or {}).get("step"), "baseline_id": job.get("baseline_id")}


def listing(conn, status: str | None = None) -> list[dict]:
    rows = conn.execute("SELECT id FROM jobs ORDER BY COALESCE(updated_at, created_at) DESC, rowid DESC").fetchall()
    out = [summary(conn, get(conn, r[0])) for r in rows]
    return [j for j in out if status is None or j["status"] == status]


def _insert_draft(conn, draft: dict, jd_text: str | None = None, jd_file: str | None = None) -> str:
    job_id = new_id()
    d = draft["details"]
    with conn:
        conn.execute("INSERT INTO jobs (id, baseline_id, title, company, weights_json, must_haves_json, preset, created_at, "
                     "settings_json, status, details_json, jd_text, jd_file, draft_json, updated_at) "
                     "VALUES (?, NULL, ?, ?, ?, '[]', NULL, ?, NULL, 'draft', ?, ?, ?, ?, ?)",
                     (job_id, d.get("title") or "", d.get("company") or "", json.dumps(draft["ranking"]["weights"]),
                      now_iso(), json.dumps(d), jd_text, jd_file, json.dumps(draft), now_iso()))
    return job_id


# --- Starting a draft (step 1) ------------------------------------------------------------------------------------

def from_scratch(conn) -> str:
    return _insert_draft(conn, empty_draft("scratch"))


def from_extraction(conn, read: dict, jd_text: str, jd_file: str | None) -> str:
    """A draft from a read JD. When the model was unavailable, the form starts empty next to the JD text."""
    draft = empty_draft("jd")
    if read.get("status") == "ok":
        draft["details"].update({k: v for k, v in read["details"].items() if v not in (None, "")})
        draft["details"]["responsibilities"] = [r["text"] for r in read.get("responsibilities", [])]
        draft["detail_quotes"] = read.get("detail_quotes", {})
        draft["requirements"] = pre_tick([_row(r) for r in read["requirements"]])
        draft["good"].update({k: v for k, v in read["good"].items() if v not in (None, "")})
        draft["good_quotes"] = read.get("good_quotes", {})
    draft["extraction"] = {k: read.get(k) for k in ("status", "model", "notice", "dropped")}
    return _insert_draft(conn, draft, jd_text, jd_file)


def templates(conn) -> list[dict]:
    """What "Copy an existing job" offers: every job (not drafts) and the role templates (seed baselines)."""
    jobs = [{"kind": "job", "id": j["id"], "title": j["title"], "company": j["company"], "status": j["status"]}
            for j in listing(conn) if j["status"] != "draft"]
    roles = [{"kind": "template", "id": b.id, "title": b.title, "company": "Role template", "status": None,
              "level": b.level} for b in db.list_baselines(conn)]
    market = [{"kind": "template", "id": r["baseline_id"], "title": f"{r['title']} (Market v{r['version']})",
               "company": "Market template", "status": None, "level": r["level"]}
              for r in conn.execute("SELECT v.baseline_id, v.version, v.level, b.title FROM market_versions v JOIN "
                                    "baselines b ON b.id = v.baseline_id WHERE v.status = 'published'")]
    return jobs + roles + market


def _draft_from(conn, baseline: Baseline, job: dict | None, source: str) -> dict:
    draft = empty_draft(source)
    if job:
        draft["details"] = {**EMPTY_DETAILS, **job["details"], "title": job["title"], "company": job["company"]}
        draft["ranking"] = {"preset": job.get("preset"), "weights": job["weights"]}
        s = wf.settings(conn, job["id"])
        draft["plan"] = {"stages": [x["name"] for x in wf.stages(conn, job["id"]) if not x["terminal"]],
                         **{k: s[k] for k in wf.DEFAULT_SETTINGS}}
    else:
        draft["details"].update({"title": baseline.title, "level": baseline.level, "min_years": baseline.required_years,
                                 "short_description": baseline.description or ""})
    draft["details"]["level"] = baseline.level if baseline.level in LEVELS else draft["details"]["level"]
    draft["details"]["min_years"] = baseline.required_years
    musts = set(job["must_haves"]) if job else set()
    rows = [_row({"criterion_id": c.id if source == "edit" else None, "name": c.name, "kind": c.kind,
                  "priority": c.priority, "must_have": c.id in musts, "aliases": c.aliases,
                  "description": c.description}) for c in baseline.criteria]
    draft["requirements"] = rows if job else pre_tick(rows)
    draft["good"] = {"scope_expectation": baseline.scope_expectation or "",
                     "complexity_criteria": baseline.complexity_criteria or "", "expected_reach": baseline.expected_reach}
    return draft


def copy_of(conn, kind: str, source_id: str) -> str:
    if kind == "template":
        b = db.get_baseline(conn, source_id)
        if b is None:
            raise ValueError("Unknown template.")
        return _insert_draft(conn, _draft_from(conn, b, None, "copy"))
    job = get(conn, source_id)
    if job is None or not job.get("baseline_id"):
        raise ValueError("Copy a published job (drafts have nothing to copy yet).")
    draft = _draft_from(conn, db.get_baseline(conn, job["baseline_id"]), job, "copy")
    draft["details"]["title"] = f"{draft['details']['title']} (copy)"
    return _insert_draft(conn, draft, job.get("jd_text"), None)


# --- The form: load, autosave ---------------------------------------------------------------------------------------

def load_draft(conn, job_id: str) -> dict:
    """The form for a draft, or for editing a published job (built from it when no edit is in progress)."""
    job = get(conn, job_id)
    if job is None:
        raise KeyError(job_id)
    if job["draft"] is None:
        b = db.get_baseline(conn, job["baseline_id"])
        job["draft"] = {**_draft_from(conn, b, job, "edit"), "step": 2}
    return {"job_id": job_id, "status": job["status"], "draft": job["draft"], "jd_text": job.get("jd_text"),
            "has_jd_file": bool(job.get("jd_file")), "warnings": warnings(job["draft"]),
            "applicants": conn.execute("SELECT COUNT(*) FROM applications WHERE job_id = ?", (job_id,)).fetchone()[0]}


def save_draft(conn, job_id: str, draft: dict) -> dict:
    """Autosave: the whole form as the recruiter sees it. Never touches a published job's live fields."""
    job = get(conn, job_id)
    if job is None:
        raise KeyError(job_id)
    draft = {**empty_draft(draft.get("source", "scratch")), **draft}
    draft["requirements"] = [_row(r) for r in draft.get("requirements", [])]
    d = draft["details"]
    with conn:
        if job["status"] == "draft":                     # the Jobs list shows a draft's title as typed
            conn.execute("UPDATE jobs SET title = ?, company = ?, details_json = ?, draft_json = ?, updated_at = ? "
                         "WHERE id = ?", (d.get("title") or "", d.get("company") or "", json.dumps(d),
                                          json.dumps(draft), now_iso(), job_id))
        else:
            conn.execute("UPDATE jobs SET draft_json = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(draft), now_iso(), job_id))
    return load_draft(conn, job_id)


def discard_edit(conn, job_id: str) -> None:
    job = get(conn, job_id)
    if job and job["status"] != "draft":
        with conn:
            conn.execute("UPDATE jobs SET draft_json = NULL WHERE id = ?", (job_id,))


# --- Checks ------------------------------------------------------------------------------------------------------

def warnings(draft: dict) -> list[str]:
    """Non-blocking warnings shown on the review step (and as the recruiter types)."""
    out = []
    reqs = [r for r in draft.get("requirements", []) if (r.get("name") or "").strip()]
    required = [r for r in reqs if r.get("priority") == "required"]
    if not required:
        out.append("No required items: candidates are ranked on general experience only.")
    if len(required) > REQUIRED_WARNING:
        out.append(f"{len(required)} required items. Job descriptions are often wish lists; consider marking some "
                   f"as preferred (more than {REQUIRED_WARNING} makes few candidates meet them).")
    d = draft.get("details", {})
    try:
        w = level_years_warning(d.get("level") or "entry", float(d.get("min_years") or 0))
    except (TypeError, ValueError):
        w = None
    if w:
        out.append(w.replace("baseline", "job"))
    g = draft.get("good", {})
    if not (g.get("scope_expectation") or "").strip() and not (g.get("complexity_criteria") or "").strip():
        out.append("“What good looks like” is empty: ownership and complexity are judged against general "
                   "expectations for the level.")
    return out


def errors(draft: dict) -> list[str]:
    """What blocks publishing (required fields and a valid requirement list)."""
    d = draft.get("details", {})
    out = [f"{label} is required." for key, label in (("title", "Title"), ("company", "Company"), ("level", "Level"))
           if not str(d.get(key) or "").strip()]
    if d.get("min_years") in (None, ""):
        out.append("Minimum years is required.")
    elif float(d["min_years"]) < 0:
        out.append("Minimum years cannot be negative.")
    if d.get("level") and d["level"] not in LEVELS:
        out.append(f"Level must be one of {', '.join(LEVELS)}.")
    try:
        to_baseline(draft, "check")
    except ValueError as exc:
        out.append(str(exc).split("\n")[-1] if "validation error" in str(exc) else str(exc))
    return out


def to_baseline(draft: dict, baseline_id: str | None) -> Baseline:
    d, g = draft["details"], draft["good"]
    reqs = [r for r in draft["requirements"] if (r.get("name") or "").strip()]
    criteria = [BaselineCriterion(id=r.get("criterion_id"), kind=r["kind"], name=r["name"].strip(),
                                  description=(r.get("description") or "").strip() or None, aliases=r.get("aliases") or [],
                                  priority=r["priority"]) for r in reqs]
    return Baseline(id=baseline_id if baseline_id != "check" else None, title=d.get("title") or "Untitled",
                    level=d.get("level") or "entry", required_years=float(d.get("min_years") or 0),
                    expected_reach=int(g.get("expected_reach") or 2),
                    scope_expectation=(g.get("scope_expectation") or "").strip() or None,
                    complexity_criteria=(g.get("complexity_criteria") or "").strip() or None,
                    description=(d.get("short_description") or "").strip() or None, is_mock=False, criteria=criteria)


# --- Publish and edit -------------------------------------------------------------------------------------------------

def _must_ids(baseline: Baseline, draft: dict) -> list[str]:
    by_name = {(c.kind, c.name.casefold()): c.id for c in baseline.criteria}
    return [by_name[(r["kind"], r["name"].strip().casefold())] for r in draft["requirements"]
            if r.get("must_have") and (r.get("name") or "").strip() and (r["kind"], r["name"].strip().casefold()) in by_name]


def _apply_plan(conn, job_id: str, plan: dict) -> None:
    """Interview plan from the form: stages by name, in order (stages that hold candidates are kept)."""
    wf.update_settings(conn, job_id, {k: plan.get(k) for k in wf.DEFAULT_SETTINGS})
    names = [n.strip() for n in plan.get("stages", []) if n and n.strip()]
    if not names:
        return
    current = wf.stages(conn, job_id)
    by_name = {s["name"].casefold(): s for s in current if not s["terminal"]}
    with conn:
        for i, n in enumerate(names):
            s = by_name.get(n.casefold())
            if s:
                conn.execute("UPDATE interview_stages SET position = ?, name = ? WHERE id = ?", (i, n, s["id"]))
            else:
                conn.execute("INSERT INTO interview_stages (id, job_id, name, position, focus_json, terminal) "
                             "VALUES (?, ?, ?, ?, '[]', 0)", (new_id(), job_id, n, i))
        keep = {n.casefold() for n in names}
        for s in current:
            if not s["terminal"] and s["name"].casefold() not in keep and s["applicants"] == 0:
                conn.execute("DELETE FROM interview_stages WHERE id = ?", (s["id"],))
        last = len(names)
        for s in current:
            if not s["terminal"] and s["name"].casefold() not in keep and s["applicants"] > 0:
                conn.execute("UPDATE interview_stages SET position = ? WHERE id = ?", (last, s["id"]))
                last += 1


def _focus_from_musts(conn, job_id: str) -> None:
    job = rc.get_job(conn, job_id)
    names = {c.id: c.name for c in db.get_baseline(conn, job["baseline_id"]).criteria}
    focus = [names[m] for m in job["must_haves"] if m in names]
    with conn:
        for s in wf.stages(conn, job_id):
            if s["name"] in wf.FOCUS_STAGES and not s["focus"]:
                conn.execute("UPDATE interview_stages SET focus_json = ? WHERE id = ?", (json.dumps(focus), s["id"]))


def publish(conn, job_id: str) -> dict:
    """Draft → Open: the job's own baseline is created and the job appears in Open jobs."""
    job = get(conn, job_id)
    if job is None or job["status"] != "draft":
        raise ValueError("Only a draft can be published.")
    draft = job["draft"]
    problems = errors(draft)
    if problems:
        raise ValueError(" ".join(problems))
    baseline_id = db.create_baseline(conn, to_baseline(draft, None))
    baseline = db.get_baseline(conn, baseline_id)
    ranking = draft.get("ranking") or default_ranking()
    weights = rc.preset_weights(ranking["preset"]) if ranking.get("preset") else ranking["weights"]
    d = draft["details"]
    with conn:
        conn.execute("UPDATE baselines SET owner_job_id = ? WHERE id = ?", (job_id, baseline_id))
        conn.execute("UPDATE jobs SET baseline_id = ?, title = ?, company = ?, weights_json = ?, must_haves_json = ?, "
                     "preset = ?, details_json = ?, status = 'open', draft_json = NULL, updated_at = ? WHERE id = ?",
                     (baseline_id, d["title"].strip(), d["company"].strip(), json.dumps(weights),
                      json.dumps(_must_ids(baseline, draft)), ranking.get("preset"), json.dumps(d), now_iso(), job_id))
    _apply_plan(conn, job_id, draft.get("plan") or default_plan())
    _focus_from_musts(conn, job_id)
    return get(conn, job_id)


def rescore_estimate(conn, job: dict) -> dict:
    """Applicants with a score, and the job-dependent judgments their scoring needed (≈ model calls to redo)."""
    b = db.get_baseline(conn, job["baseline_id"])
    rows = conn.execute("SELECT candidate_id FROM applications WHERE job_id = ? AND run_id IS NOT NULL",
                        (job["id"],)).fetchall()
    calls = 0
    for (cid,) in rows:
        calls += conn.execute("SELECT COUNT(*) FROM judgments WHERE candidate_id = ? AND baseline_id = ? AND "
                              "baseline_hash = ? AND used = 1", (cid, b.id, b.content_hash())).fetchone()[0]
    return {"applicants": len(rows), "calls": calls, "candidates": [r[0] for r in rows]}


def _changes(conn, job: dict) -> tuple[Baseline, Baseline, bool, bool]:
    """(current, edited, scoring changed, judgments changed). Requirements and "What good looks like" change
    the scores; only what judges read (the baseline hash) needs new model calls."""
    current = db.get_baseline(conn, job["baseline_id"])
    new = to_baseline(job["draft"], current.id)
    fields = ("title", "level", "required_years", "expected_reach", "scope_expectation", "complexity_criteria",
              "description")
    scoring = new.content_hash() != current.content_hash() or any(getattr(new, f) != getattr(current, f) for f in fields) \
        or [(c.kind, c.name, c.priority) for c in new.criteria] != [(c.kind, c.name, c.priority) for c in current.criteria]
    return current, new, scoring, new.content_hash() != current.content_hash()


def edit_impact(conn, job_id: str) -> dict:
    """What saving the edit would do: "N applicants will be re-scored. About M model calls." (M = 0 when only
    code-read fields changed: the saved judgments are reused)."""
    job = get(conn, job_id)
    if job is None or job["status"] == "draft" or job["draft"] is None:
        return {"rescore": False, "applicants": 0, "calls": 0}
    _, _, scoring, judged = _changes(conn, job)
    est = rescore_estimate(conn, job) if scoring else {"applicants": 0, "calls": 0}
    return {"rescore": scoring and est["applicants"] > 0, "applicants": est["applicants"],
            "calls": est["calls"] if judged else 0}


def save_edit(conn, job_id: str) -> dict:
    """Apply the edit form to the published job. Returns the job and the applications to re-score (the caller
    starts them)."""
    job = get(conn, job_id)
    if job is None or job["status"] == "draft" or job["draft"] is None:
        raise ValueError("Nothing to save.")
    draft = job["draft"]
    problems = errors(draft)
    if problems:
        raise ValueError(" ".join(problems))
    current, new, scoring, _ = _changes(conn, job)
    if scoring:
        db.update_baseline(conn, new)
    baseline = db.get_baseline(conn, current.id)
    ranking = draft.get("ranking") or default_ranking()
    weights = rc.preset_weights(ranking["preset"]) if ranking.get("preset") else ranking["weights"]
    d = draft["details"]
    with conn:
        conn.execute("UPDATE jobs SET title = ?, company = ?, weights_json = ?, must_haves_json = ?, preset = ?, "
                     "details_json = ?, draft_json = NULL, updated_at = ? WHERE id = ?",
                     (d["title"].strip(), d["company"].strip(), json.dumps(weights),
                      json.dumps(_must_ids(baseline, draft)), ranking.get("preset"), json.dumps(d), now_iso(), job_id))
    _apply_plan(conn, job_id, draft.get("plan") or default_plan())
    rescore = [dict(r) for r in conn.execute("SELECT id, candidate_id FROM applications WHERE job_id = ? AND run_id "
                                             "IS NOT NULL", (job_id,))] if scoring else []
    return {"job": get(conn, job_id), "rescore": rescore}


def set_status(conn, job_id: str, status: str) -> dict:
    job = get(conn, job_id)
    if status not in ("open", "closed") or job is None or job["status"] == "draft":
        raise ValueError("Close an open job or reopen a closed one.")
    with conn:
        conn.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso(), job_id))
    return get(conn, job_id)


def delete_draft(conn, job_id: str) -> None:
    job = get(conn, job_id)
    if job is None or job["status"] != "draft":
        raise ValueError("Only drafts can be deleted.")
    with conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))


# --- Candidate side ----------------------------------------------------------------------------------------------------

def posting(conn, job: dict) -> dict:
    """What candidates see (also the recruiter's preview)."""
    b = db.get_baseline(conn, job["baseline_id"]) if job.get("baseline_id") else None
    d = job["details"]
    return {"id": job["id"], "title": job["title"], "company": job["company"], **{k: d.get(k) for k in (
        "department", "location", "workplace", "employment_type", "level", "min_years", "short_description",
        "responsibilities")},
            "required": [c.name for c in b.criteria if c.priority == "required"] if b else [],
            "preferred": [c.name for c in b.criteria if c.priority == "preferred"] if b else [],
            "reach": REACH_LABELS.get(b.expected_reach) if b else None, "created_at": job["created_at"]}


def open_jobs(conn) -> list[dict]:
    return [posting(conn, get(conn, j["id"])) for j in listing(conn, "open")]


def apply(conn, job_id: str, candidate_id: str) -> str:
    """The candidate's application (created now, scored by the caller). One per candidate and job."""
    job = get(conn, job_id)
    if job is None or job["status"] != "open":
        raise ValueError("This job is not open for applications.")
    row = conn.execute("SELECT id FROM applications WHERE job_id = ? AND candidate_id = ?", (job_id, candidate_id)).fetchone()
    return row[0] if row else rc.add_application(conn, job_id, candidate_id, None)


def link_run(conn, application_id: str, run_id: str) -> None:
    with conn:
        conn.execute("UPDATE applications SET run_id = ? WHERE id = ?", (run_id, application_id))


def clone(d: dict) -> dict:
    return copy.deepcopy(d)
