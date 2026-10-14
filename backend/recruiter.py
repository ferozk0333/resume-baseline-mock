"""Recruiter view (specs/11): ranked applicants, must-haves, evidence confidence, profile panel, compare.

Everything here is code over saved scores and labels: no model calls. Re-ranking with other weights
re-aggregates saved subcategory scores. The "why" points and phone-screen questions are code templates
until model generation is available; they are labelled "Demo text" (model = "template-v1").

Fairness (specs/11 §8): ranking uses only the scores. Names, schools, locations and dates of birth never
enter ranking, tags, "why" or questions; the templates only quote role/project bullets and criterion names.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date

from core import config, db
from core.db import new_id, now_iso
from core.models import Baseline
from extraction import store
from extraction.records import Profile
from graph.nodes.expertise import mentions
from graph.pipeline import rescore, run_scores
from scoring.matching import match_certification

TAG_MIN_SCORE = 0.8               # proposal (specs/11 §5): subcategory scores at or above this become strength tags
MAX_TAGS = 3
STALE_YEARS = 3                   # proposal (specs/11 §6.2): best evidence older than this → a question
TEMPLATE_MODEL = "template-v1"
DEMO_LABELS = {"demo_parsed": "Demo data: parsed from structured profile",
               "archetype": "Test archetype"}
STATUSES = ("new", "shortlisted", "in_loop", "not_moving_forward")
# [OPEN] presets: storage and UI exist, but every preset equals the team table until the team sets values.
PRESETS = ("Startup", "Enterprise", "Proof-first")

BACKED = {"bullet", "used", "certified"}          # met and backed: shown in work
ON_PAPER = {"keyword", "listed"}                  # met on paper: listed only


def preset_weights(name: str) -> dict:
    if name not in PRESETS:
        raise ValueError(f"unknown preset {name!r}")
    return config.default_weights()


# --- Jobs and applications -------------------------------------------------------------------------

def create_job(conn, baseline_id: str, title: str, company: str, must_haves: list[str] | None = None) -> str:
    job_id = new_id()
    with conn:
        conn.execute("INSERT INTO jobs (id, baseline_id, title, company, weights_json, must_haves_json, preset, "
                     "created_at) VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                     (job_id, baseline_id, title, company, json.dumps(config.default_weights()),
                      json.dumps(must_haves or []), now_iso()))
    return job_id


def get_job(conn, job_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        return None
    job = dict(row)
    job["weights"] = json.loads(job.pop("weights_json"))
    job["must_haves"] = json.loads(job.pop("must_haves_json") or "[]")
    return job


def list_jobs(conn) -> list[dict]:
    rows = conn.execute("SELECT j.id FROM jobs j ORDER BY j.created_at, j.rowid").fetchall()
    out = []
    for r in rows:
        job = get_job(conn, r[0])
        job["applicants"] = conn.execute("SELECT COUNT(*) FROM applications WHERE job_id = ?", (job["id"],)).fetchone()[0]
        out.append(job)
    return out


def update_job(conn, job_id: str, *, weights: dict | None = None, must_haves: list[str] | None = None,
               preset: str | None = None) -> dict:
    job = get_job(conn, job_id)
    if preset:
        weights = preset_weights(preset)
    if must_haves is not None:
        valid = {c.id for c in db.get_baseline(conn, job["baseline_id"]).criteria}
        unknown = [m for m in must_haves if m not in valid]
        if unknown:
            raise ValueError(f"not criteria of this job's baseline: {unknown}")
    with conn:
        conn.execute("UPDATE jobs SET weights_json = COALESCE(?, weights_json), must_haves_json = COALESCE(?, "
                     "must_haves_json), preset = ? WHERE id = ?",
                     (json.dumps(weights) if weights else None, json.dumps(must_haves) if must_haves is not None
                      else None, preset if preset else None if weights else job["preset"], job_id))
    return get_job(conn, job_id)


def add_application(conn, job_id: str, candidate_id: str, run_id: str | None = None) -> str:
    app_id = new_id()
    with conn:
        conn.execute("INSERT INTO applications (id, job_id, candidate_id, run_id, status, applied_at) "
                     "VALUES (?, ?, ?, ?, 'new', ?)", (app_id, job_id, candidate_id, run_id, now_iso()))
    return app_id


def set_status(conn, app_id: str, status: str) -> None:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    with conn:
        conn.execute("UPDATE applications SET status = ? WHERE id = ?", (status, app_id))


# --- Scores, must-haves, tags ------------------------------------------------------------------------------

def criterion_labels(scores: dict) -> dict[str, str | None]:
    """criterion id → the skill_evidence / tool_evidence label the score used (one source for chips and table)."""
    return {cid: j["label"] for cid, j in _judged(scores).items()}


def must_have_status(label: str | None, kind: str, cert_match: bool = False) -> str:
    """met_backed | met_on_paper | not_met | unknown (not scored yet or the judgment failed)."""
    if kind == "certification":
        return "met_backed" if cert_match else "not_met"
    if label is None:
        return "unknown"
    if label in BACKED:
        return "met_backed"
    if label in ON_PAPER:
        return "met_on_paper"
    return "not_met"


def must_haves(conn, job: dict, baseline: Baseline, scores: dict | None, profile: Profile,
               evidence: dict | None = None) -> list[dict]:
    """Must-have chips: the job's must-have rows of the requirements table (one source, specs/13 §0.3)."""
    return [{"criterion_id": r["criterion_id"], "name": r["name"], "kind": r["kind"], "status": r["status"]}
            for r in requirements(baseline, job, scores, profile, evidence) if r["must_have"]]


def tags(scores: dict, effective_weights: dict[str, float]) -> list[str]:
    strong = [s for s, v in scores.items() if v["status"] == "scored" and (v["score"] or 0) >= TAG_MIN_SCORE]
    strong.sort(key=lambda s: -effective_weights.get(s, 0))
    return [config.SUBCATEGORY_LABELS[s] for s in strong[:MAX_TAGS]]


def weights_hash(weights: dict) -> str:
    return hashlib.sha256(json.dumps(weights, sort_keys=True).encode()).hexdigest()[:16]


# --- Evidence helpers --------------------------------------------------------------------------------------

def _bullet_owner(profile: Profile) -> dict[str, tuple[str, str]]:
    owners = {}
    for r in profile.roles:
        for b in r.bullets:
            owners[b.id] = ("role", r.title + (f" — {r.employer}" if r.employer else ""))
    for p in profile.projects:
        for b in p.bullets:
            owners[b.id] = ("repo" if p.is_github else "project", p.name)
    return owners


def evidence_ids(profile: Profile) -> set[str]:
    ids = {b.id for b in profile.all_bullets()} | {p.id for p in profile.projects} | {c.id for c in profile.claims}
    return ids | {r.id for r in profile.roles} | {c.id for c in profile.certifications}


def _labels_by(scores: dict, sub: str, question: str) -> dict[str, str]:
    return {j["subject_id"]: j["label"] for j in (scores.get(sub, {}).get("details") or {}).get("judgments", [])
            if j["question"] == question}


BACKING_STATUS = {"artifact": "met_backed", "in a bullet": "met_backed", "certification": "met_backed",
                  "listed only": "met_on_paper", "not shown": "not_met", "not judged": "unknown", "not scored yet": "unknown"}


def _judged(scores: dict) -> dict[str, dict]:
    """criterion id → the skill_evidence / tool_evidence judgment whose label the score used. A criterion found only
    in the skills list has two (alias route and "<id>#all"): the better label wins, with its quote (specs/13 §0.4)."""
    from graph.nodes.expertise import ALL_BULLETS, best_label
    out = {}
    for sub in ("role_skills", "tools"):
        for j in (scores.get(sub, {}).get("details") or {}).get("judgments", []):
            if j["question"] not in ("skill_evidence", "tool_evidence"):
                continue
            cid = j["subject_id"].removesuffix(ALL_BULLETS)
            have = out.get(cid)
            if have is None or (j["label"] is not None and best_label(j["question"], have["label"], j["label"]) == j["label"]
                                and j["label"] != have["label"]):
                out[cid] = {**j, "subject_id": cid}
    return out


def _evidence_items(profile: Profile, evidence: dict) -> list[dict]:
    """Every line a skill/tool judge can see, with the evidence item it comes from (same text as the judge input)."""
    from graph.nodes.expertise import repo_lines
    owners = _bullet_owner(profile)
    items = []
    ctx = _plan_ctx(None, profile, evidence)
    for p, line in zip([p for p in ctx.counted_projects() if p.is_github], repo_lines(ctx)):
        items.append({"text": line, "kind": "repo", "id": p.id, "source": f"repo: {p.name}", "link": p.url,
                      "quote": p.description or p.name})
    for b in profile.all_bullets():
        kind, name = owners.get(b.id, ("project", ""))
        items.append({"text": b.text, "kind": "bullet", "id": b.id, "source": f"{kind}: {name}", "link": None,
                      "quote": b.text})
    for p in ctx.counted_projects():
        if p.description and not p.is_github:
            items.append({"text": f"{p.name}: {p.description}", "kind": "bullet", "id": p.id,
                          "source": f"project: {p.name}", "link": p.url, "quote": p.description})
    for c in profile.certifications:
        items.append({"text": c.name, "kind": "certification", "id": c.id, "source": "certification", "link": None,
                      "quote": c.name})
    for c in profile.claims:
        if c.bullet_id is None:
            items.append({"text": c.name_raw, "kind": "listed", "id": c.id, "source": "skills list", "link": None,
                          "quote": c.name_raw})
    return items


def _plan_ctx(baseline, profile: Profile, evidence: dict):
    from graph.context import Ctx
    return Ctx(conn=None, deps=None, svc=None, candidate_id="", run_id=None, baseline=baseline, profile=profile,
               today=date.today(), evidence=evidence)


def requirements(baseline: Baseline, job: dict, scores: dict | None, profile: Profile,
                 evidence: dict | None = None) -> list[dict]:
    """Requirements vs evidence (specs/11 §5.3, specs/13 §0.3/§0.5). One source: the saved skill/tool evidence
    judgment. Its label sets the backing (and the must-have chip and the score); the source is the evidence
    item that earned that level: the one holding the judge's quote, else the first matching line the judge saw."""
    from core.text import normalize_name
    from graph.nodes.expertise import skills_plan, tools_plan
    judged = _judged(scores or {})
    evidence = evidence or {}
    items = _evidence_items(profile, evidence)
    asks = {a.subject_id: a for a in [*skills_plan(_plan_ctx(baseline, profile, evidence)),
                                       *tools_plan(_plan_ctx(baseline, profile, evidence))]}

    def holding(quote: str | None, kinds: tuple[str, ...]):
        q = normalize_name(quote)
        return next((i for i in items if q and i["kind"] in kinds and q in normalize_name(i["text"])), None) \
            if q else None

    def first_seen(c, field: str, kinds: tuple[str, ...]):
        seen = [ln for ln in (asks[c.id].context.get(field) or [])] if c.id in asks else []
        return next((i for ln in seen for i in items if i["kind"] in kinds and i["text"] == ln
                     and (field != "skills_list" or mentions(ln, c))), None)

    rows = []
    for c in baseline.criteria:
        j = judged.get(c.id)
        label = j["label"] if j else None
        quote = j.get("quote") if j else None
        item, backing = None, "not scored yet" if not scores else "not judged"
        if c.kind == "certification":
            item = next((i for i in items if i["kind"] == "certification"
                         and any(match_certification(x.name, [c]) for x in profile.certifications if x.id == i["id"])),
                        None)
            backing = "certification" if item else ("not shown" if scores else backing)
        elif label in ("bullet", "used"):
            item = holding(quote, ("repo", "bullet")) or (label == "used" and first_seen(c, "repositories", ("repo",))) \
                or first_seen(c, "bullets", ("bullet",))
            backing = "artifact" if item and item["kind"] == "repo" else "in a bullet"
        elif label == "certified":
            item = holding(quote, ("certification",)) or first_seen(c, "certifications", ("certification",))
            backing = "certification"
        elif label in ("keyword", "listed"):
            item = holding(quote, ("listed",)) or first_seen(c, "skills_list", ("listed",))
            backing = "listed only"
        elif label is not None:
            backing = "not shown"
        rows.append({"criterion_id": c.id, "name": c.name, "kind": c.kind, "priority": c.priority,
                     "must_have": c.id in job["must_haves"], "label": label, "status": BACKING_STATUS[backing],
                     "backing": backing, "quote": item["quote"] if item else None,
                     "source": item["source"] if item else None, "link": item["link"] if item else None,
                     "evidence_id": item["id"] if item else None})
    return rows


# --- Generated text (code templates until model generation is available) ---------------------------------

def why_points(baseline: Baseline, job: dict, scores: dict, profile: Profile) -> list[dict]:
    """Three short points, each citing evidence ids of this candidate (specs/11 §6.1)."""
    points = []
    metric = _labels_by(scores, "outcomes", "outcome_metric")
    direction = _labels_by(scores, "outcomes", "outcome_direction")
    ownership = _labels_by(scores, "ownership", "ownership")
    bullets = {b.id: b for b in profile.all_bullets()}
    results = [bullets[i] for i in metric if i in bullets and metric[i] == "yes" and direction.get(i) == "yes"]
    if results:
        points.append({"text": f"Shows measured results: “{results[0].text}”", "evidence_ids": [results[0].id]})
    led = [bullets[i] for i in ownership if i in bullets and ownership[i] == "led" and bullets[i] not in results[:1]]
    if led:
        points.append({"text": f"Led the work themselves: “{led[0].text}”", "evidence_ids": [led[0].id]})
    reqs = [r for r in requirements(baseline, job, scores, profile)
            if r["priority"] == "required" and r["backing"] in ("artifact", "in a bullet") and r["evidence_id"]]
    if reqs:
        names = ", ".join(r["name"] for r in reqs[:3])
        points.append({"text": f"Backs {len(reqs)} required skill{'s' if len(reqs) != 1 else ''} with work, "
                               f"including {names}.", "evidence_ids": [r["evidence_id"] for r in reqs[:3]]})
    repos = [p for p in profile.projects if p.is_github and p.own_commits]
    if len(points) < 3 and repos:
        best = max(repos, key=lambda p: p.own_commits or 0)
        points.append({"text": f"Has their own code to look at: {best.name} ({best.own_commits} commits).",
                       "evidence_ids": [best.id]})
    return check_citations(points[:3], profile)


def check_citations(points: list[dict], profile: Profile) -> list[dict]:
    """Drop any point citing an evidence id that is not this candidate's (specs/11 §6.1)."""
    known = evidence_ids(profile)
    return [p for p in points if p["evidence_ids"] and all(e in known for e in p["evidence_ids"])]


def questions(baseline: Baseline, job: dict, scores: dict, profile: Profile, today: date) -> list[dict]:
    """3–5 phone-screen questions; topics chosen in code (specs/11 §6.2), phrased by template."""
    out = []
    labels = criterion_labels(scores)
    for c in baseline.criteria:
        status = must_have_status(labels.get(c.id), c.kind)
        if c.id in job["must_haves"] and status == "met_on_paper":
            out.append({"topic": f"must-have on paper: {c.name}", "reason": "Listed, but no work shows it.",
                        "question": f"You list {c.name}. Walk me through a recent piece of work where you used it."})
    for c in baseline.criteria:
        if c.priority == "required" and labels.get(c.id) in ("missing", "mention_or_missing"):
            out.append({"topic": f"required, missing: {c.name}", "reason": "Not found in the profile.",
                        "question": f"The role needs {c.name}. Where have you used it, or how would you get up to speed?"})
    proof = _labels_by(scores, "proof_density", "proof_support")
    claimed = [x for x in profile.claims if proof.get(x.id) == "claimed_only"]
    for x in claimed[:2]:
        out.append({"topic": f"claimed only: {x.name_raw}", "reason": "Named, but not shown in any bullet or artifact.",
                    "question": f"Your profile names {x.name_raw}. Can you describe a time you used it and what it produced?"})
    recency = (scores.get("recency") or {}).get("details") or {}
    gaps = [a.get("gap_years") for a in recency.get("activities", []) if a.get("relevance")]
    if gaps and min(gaps) > STALE_YEARS:
        out.append({"topic": "older evidence", "reason": f"The most recent relevant work ended over {STALE_YEARS} years ago.",
                    "question": "What have you done in this area recently, and what would you need to get current?"})
    return out[:5]


def summaries(conn, run_id: str, weights: dict, baseline: Baseline, job: dict, scores: dict, profile: Profile,
              today: date) -> dict:
    """Cached per run and weights (generated_summaries)."""
    h = weights_hash({**weights, "must_haves": job["must_haves"]})
    out = {}
    for kind, make in (("why", lambda: why_points(baseline, job, scores, profile)),
                       ("questions", lambda: questions(baseline, job, scores, profile, today))):
        row = conn.execute("SELECT content_json FROM generated_summaries WHERE run_id = ? AND kind = ? AND weights_hash = ?",
                           (run_id, kind, h)).fetchone()
        if row:
            out[kind] = json.loads(row[0])
            continue
        out[kind] = make()
        with conn:
            conn.execute("INSERT OR REPLACE INTO generated_summaries (run_id, kind, weights_hash, content_json, model, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?)", (run_id, kind, h, json.dumps(out[kind]),
                                                                   TEMPLATE_MODEL, now_iso()))
    return out


# --- Rows, panel, compare ------------------------------------------------------------------------------------

def _context(conn, app: dict, job: dict):
    baseline = db.get_baseline(conn, job["baseline_id"])
    profile = store.load_profile(conn, app["candidate_id"])
    scores = run_scores(conn, app["run_id"]) if app["run_id"] else None
    agg = rescore(conn, app["run_id"], job["weights"]) if app["run_id"] else None
    evidence = {e["id"]: e for e in store.list_evidence(conn, app["candidate_id"])}
    return baseline, profile, scores, agg, evidence


def applicant_row(conn, app: dict, job: dict) -> dict:
    baseline, profile, scores, agg, evidence = _context(conn, app, job)
    cand = store.get_candidate(conn, app["candidate_id"])
    mh = must_haves(conn, job, baseline, scores, profile, evidence)
    why = summaries(conn, app["run_id"], job["weights"], baseline, job, scores, profile, date.today())["why"] \
        if scores else []
    current = next((r for r in profile.roles if r.is_current), profile.roles[0] if profile.roles else None)
    return {
        "application_id": app["id"], "candidate_id": app["candidate_id"], "name": cand["name"],
        "current_role": current.title if current else None, "status": app["status"], "run_id": app["run_id"],
        "scored": agg is not None,
        "match": agg.final if agg else None,
        "confidence": round(100 * agg.dimensions["trust"], 1) if agg and agg.dimensions.get("trust") is not None else None,
        "must_haves_met": sum(1 for m in mh if m["status"] == "met_backed"), "must_haves_total": len(mh),
        "why": why[0]["text"] if why else None,
        "tags": tags(scores, agg.effective_weights) if scores else [],
        "demo": DEMO_LABELS.get(cand.get("data_source")),
        "stage_id": app.get("stage_id"),
        "stage": _stage_name(conn, app.get("stage_id")),
    }


def _stage_name(conn, stage_id: str | None) -> str | None:
    row = conn.execute("SELECT name FROM interview_stages WHERE id = ?", (stage_id,)).fetchone() if stage_id else None
    return row[0] if row else None


def applicants(conn, job: dict, *, sort: str = "match", status: str | None = None, all_must_haves: bool = False,
               min_score: float | None = None) -> list[dict]:
    from scoring import benchmarks
    rows = [applicant_row(conn, dict(a), job) for a in
            conn.execute("SELECT * FROM applications WHERE job_id = ?", (job["id"],)).fetchall()]
    pool = benchmarks.job_pool(conn, job)                 # percentile within this job's applicants (specs/12 §3)
    for r in rows:
        p = benchmarks.percentiles(pool, r["candidate_id"]) if r["scored"] else {"shown": False}
        r["percentile"] = p["final"]["percentile"] if p.get("shown") and p.get("final") else None
        r["percentile_n"] = p["final"]["n"] if p.get("shown") and p.get("final") else None
    if status:
        rows = [r for r in rows if r["status"] == status]
    if all_must_haves:
        rows = [r for r in rows if r["must_haves_total"] and r["must_haves_met"] == r["must_haves_total"]]
    if min_score is not None:
        rows = [r for r in rows if r["match"] is not None and r["match"] >= min_score]
    key = {"match": lambda r: r["match"], "confidence": lambda r: r["confidence"],
           "must_haves": lambda r: r["must_haves_met"]}.get(sort)
    if key is None:
        raise ValueError("sort must be match, confidence or must_haves")
    # Unscored applicants last; ties keep a stable order by application id (never by name).
    rows.sort(key=lambda r: (key(r) is None, -(key(r) or 0), r["application_id"]))
    return rows


def panel(conn, app: dict, job: dict) -> dict:
    """Everything the profile panel shows for one applicant."""
    from backend import media
    baseline, profile, scores, agg, evidence_by_id = _context(conn, app, job)
    cand = store.get_candidate(conn, app["candidate_id"])
    evidence = list(evidence_by_id.values())
    gen = summaries(conn, app["run_id"], job["weights"], baseline, job, scores, profile, date.today()) if scores else {}
    repos = []
    for p in profile.projects:
        if not p.is_github:
            continue
        # A repo merged with a resume project keeps the resume's evidence id: find its GitHub source by name.
        ev = next((e for e in evidence if e["type"] == "github_repo" and
                   (e["raw_text"] or "").split("\n", 1)[0].endswith(f"/{p.name}")),
                  next((e for e in evidence if e["id"] == p.evidence_id), {}))
        text = ev.get("raw_text") or ""
        readme = text.split("README:", 1)[1].strip() if "README:" in text else ""
        langs = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("Languages:")), "")
        pushed = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("Last push:")), "")
        repos.append({"id": p.id, "name": p.name, "description": p.description, "url": p.url, "languages": langs,
                      "own_commits": p.own_commits, "fork": bool(p.is_fork), "counted": not (p.is_fork and not p.own_commits),
                      "last_update": pushed[:7] or None, "readme_excerpt": readme[:280] or None})
    linkedin = next((e for e in evidence if e["type"] == "linkedin_pdf"), None)
    portfolio = [p for p in profile.projects if any(e["id"] == p.evidence_id and e["type"] == "portfolio" for e in evidence)]
    resume = next((e for e in evidence if e["type"] == "resume"), None)
    row = applicant_row(conn, app, job)
    from scoring import benchmarks
    pct = benchmarks.percentiles(benchmarks.job_pool(conn, job), app["candidate_id"]) if scores else {"shown": False}
    row["percentile"] = pct["final"]["percentile"] if pct.get("shown") and pct.get("final") else None
    row["percentile_n"] = pct["final"]["n"] if pct.get("shown") and pct.get("final") else None
    reqs = requirements(baseline, job, scores, profile, evidence_by_id)
    return {
        **row,
        "job": {"id": job["id"], "title": job["title"], "company": job["company"]},
        "must_haves": must_haves(conn, job, baseline, scores, profile, evidence_by_id),
        "why_points": gen.get("why", []), "questions": gen.get("questions", []),
        "generated_label": "Demo text" if scores else None,
        "requirements": reqs,
        "dimensions": agg.dimensions if agg else None,
        "subcategories": [{"key": s, "label": config.SUBCATEGORY_LABELS[s], "dimension": config.SUBCATEGORY_DIMENSION[s],
                           **{k: v for k, v in scores[s].items() if k != "details"},
                           "effective_weight": agg.effective_weights.get(s, 0.0),
                           "evidence": subcategory_evidence(scores[s])} for s in scores] if scores else [],
        "differentiators": differentiators(scores, agg, reqs) if scores else {"tags": [], "evidence": []},
        "work": {
            "videos": media.list_links(conn, app["candidate_id"]),
            "repos": repos,
            "portfolio": [{"name": p.name, "description": p.description, "url": p.url} for p in portfolio],
            "portfolio_url": cand.get("portfolio_url"),
            "linkedin": json.loads(linkedin["extracted_json"]) if linkedin and linkedin["extracted_json"] else None,
            "resume_file": bool(resume and resume["file_path"]),
            "resume_file_kind": (resume["file_path"].rsplit(".", 1)[-1] if resume and resume["file_path"] else None),
        },
    }


NONE_LABELS = {"missing", "no", "none", "not_stated", "claimed_only", "unrelated", "below", "mention_or_missing",
               "does_not", "not_aligned", "keyword", "listed"}


def subcategory_evidence(score: dict, limit: int = 4) -> list[dict]:
    """The judgments behind a subcategory, strongest first: label plus the quote (or what was judged)."""
    trail = (score.get("details") or {}).get("judgments") or []
    rows = [{"question": t["question"], "label": t["label"], "text": t.get("quote") or t.get("subject"),
             "supported": t["label"] not in NONE_LABELS} for t in trail if t.get("label")]
    rows.sort(key=lambda r: not r["supported"])
    return rows[:limit]


def differentiators(scores: dict, agg, reqs: list[dict]) -> dict:
    """Strength tags plus the 3 strongest evidence items with their source (must-haves and artifacts first)."""
    backed = [r for r in reqs if r["status"] == "met_backed" and r["quote"]]
    backed.sort(key=lambda r: (not r["must_have"], r["backing"] != "artifact", r["priority"] != "required"))
    seen, items = set(), []
    for r in backed:
        if r["evidence_id"] in seen:
            continue
        seen.add(r["evidence_id"])
        items.append({k: r[k] for k in ("name", "quote", "source", "link", "backing", "evidence_id")})
    return {"tags": tags(scores, agg.effective_weights), "evidence": items[:3]}


def compare(conn, job: dict, app_ids: list[str]) -> list[dict]:
    if not 2 <= len(app_ids) <= 3:
        raise ValueError("Compare 2 or 3 applicants.")
    out = []
    for app_id in app_ids:
        app = conn.execute("SELECT * FROM applications WHERE id = ? AND job_id = ?", (app_id, job["id"])).fetchone()
        if app is None:
            raise ValueError(f"application {app_id} is not in this job")
        p = panel(conn, dict(app), job)
        top = [r for r in p["requirements"] if r["backing"] in ("artifact", "in a bullet")][:3]
        out.append({k: p[k] for k in ("application_id", "name", "match", "confidence", "dimensions", "must_haves_met",
                                      "must_haves_total", "scored", "demo")} | {"top_evidence": top})
    return out
