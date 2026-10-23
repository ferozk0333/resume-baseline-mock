"""Save and load candidates, evidence items and validated profiles."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict
from datetime import date

from core.db import new_id, now_iso
from extraction.records import (
    BulletRec, CertificationRec, ClaimRec, ExtraItem, Profile, ProjectRec, ReferenceRec, RoleRec,
)

PROFILE_TABLES = ("roles", "projects", "bullets", "claims", "certifications", "references_")


def _iso(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _date(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _list(v: list | None) -> str | None:
    return json.dumps(v) if v else None


def _json(raw: str | None, empty):
    return json.loads(raw) if raw else empty


def _col(row, name: str):
    """A column added later (specs/19): absent in a row read before the migration ran."""
    return row[name] if name in row.keys() else None


def create_candidate(conn: sqlite3.Connection, name: str | None = None, target_role: str | None = None,
                     github_username: str | None = None, portfolio_url: str | None = None) -> str:
    cid = new_id()
    with conn:
        conn.execute(
            "INSERT INTO candidates (id, name, target_role, github_username, portfolio_url, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cid, name, target_role, github_username, portfolio_url, now_iso()),
        )
    return cid


def add_evidence(conn: sqlite3.Connection, candidate_id: str, type: str, source: str, *,
                 raw_text: str | None = None, file_path: str | None = None, file_sha256: str | None = None,
                 status: str = "parsed", error: str | None = None) -> str:
    eid = new_id()
    with conn:
        conn.execute(
            "INSERT INTO evidence_items (id, candidate_id, type, source, file_path, file_sha256, raw_text, "
            "status, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (eid, candidate_id, type, source, file_path, file_sha256, raw_text, status, error, now_iso()),
        )
    return eid


def set_evidence_status(conn: sqlite3.Connection, evidence_id: str, status: str, *,
                        error: str | None = None, extracted: Profile | None = None,
                        items_extracted: int | None = None, items_dropped: int | None = None) -> None:
    payload = json.dumps(asdict(extracted), default=str) if extracted is not None else None
    with conn:
        conn.execute(
            "UPDATE evidence_items SET status = ?, error = ?, extracted_json = COALESCE(?, extracted_json), "
            "items_extracted = COALESCE(?, items_extracted), items_dropped = COALESCE(?, items_dropped) "
            "WHERE id = ?",
            (status, error, payload, items_extracted, items_dropped, evidence_id),
        )


def list_evidence(conn: sqlite3.Connection, candidate_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM evidence_items WHERE candidate_id = ? ORDER BY rowid", (candidate_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# --- Single-row inserts (no transaction; callers wrap them) -----------------------------

def _insert_role(conn, candidate_id: str, r: RoleRec) -> None:
    conn.execute(
        "INSERT INTO roles (id, candidate_id, evidence_id, title, employer, industry, start_date, end_date, "
        "is_current, team_size, quote, source, needs_review, review_reasons, method, location, sources) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (r.id, candidate_id, r.evidence_id, r.title, r.employer, r.industry, _iso(r.start_date),
         _iso(r.end_date), int(r.is_current), r.team_size, r.quote, r.source, int(r.needs_review),
         json.dumps(r.review_reasons), r.method, r.location, _list(r.sources)),
    )
    _insert_bullets(conn, candidate_id, r.bullets, role_id=r.id)


def _insert_project(conn, candidate_id: str, p: ProjectRec) -> None:
    conn.execute(
        "INSERT INTO projects (id, candidate_id, evidence_id, role_id, kind, name, description, url, "
        "start_date, end_date, is_fork, own_commits, quote, source, needs_review, review_reasons, method, details, "
        "sources) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (p.id, candidate_id, p.evidence_id, p.role_id, p.kind, p.name, p.description, p.url,
         _iso(p.start_date), _iso(p.end_date), None if p.is_fork is None else int(p.is_fork),
         p.own_commits, p.quote, p.source, int(p.needs_review), json.dumps(p.review_reasons), p.method,
         json.dumps(p.details) if p.details else None, _list(p.sources)),
    )
    _insert_bullets(conn, candidate_id, p.bullets, project_id=p.id)


def _insert_bullets(conn, candidate_id: str, bullets: list[BulletRec], role_id=None, project_id=None) -> None:
    conn.executemany(
        "INSERT INTO bullets (id, candidate_id, evidence_id, role_id, project_id, text, location, source, method) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(b.id, candidate_id, b.evidence_id, role_id, project_id, b.text, b.location, b.source, b.method)
         for b in bullets],
    )


def _insert_claim(conn, candidate_id: str, c: ClaimRec) -> None:
    conn.execute(
        "INSERT INTO claims (id, candidate_id, evidence_id, bullet_id, kind, name_raw, name_norm, quote, source, method, "
        "details, skill_group, sources) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (c.id, candidate_id, c.evidence_id, c.bullet_id, c.kind, c.name_raw, c.name_norm, c.quote, c.source,
         c.method, json.dumps(c.details) if c.details else None, c.skill_group, _list(c.sources)),
    )


def _insert_certification(conn, candidate_id: str, c: CertificationRec) -> None:
    conn.execute(
        "INSERT INTO certifications (id, candidate_id, evidence_id, name, issuer, issued_date, credential_id, "
        "verify_url, verified, source, method, sources) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (c.id, candidate_id, c.evidence_id, c.name, c.issuer, _iso(c.issued_date), c.credential_id,
         c.verify_url, int(c.verified), c.source, c.method, _list(c.sources)),
    )


def _insert_reference(conn, candidate_id: str, r: ReferenceRec) -> None:
    conn.execute("INSERT INTO references_ (id, candidate_id, name, relationship, contact, statement) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (r.id, candidate_id, r.name, r.relationship, r.contact, r.statement))


def save_profile(conn: sqlite3.Connection, candidate_id: str, profile: Profile) -> None:
    """Replace the candidate's whole profile in one transaction."""
    with conn:
        for table in PROFILE_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE candidate_id = ?", (candidate_id,))
        for r in profile.roles:
            _insert_role(conn, candidate_id, r)
        for p in profile.projects:
            _insert_project(conn, candidate_id, p)
        for c in profile.claims:
            _insert_claim(conn, candidate_id, c)
        for c in profile.certifications:
            _insert_certification(conn, candidate_id, c)
        for r in profile.references:
            _insert_reference(conn, candidate_id, r)


# --- Dropped items ("Add back" in the review step) ---------------------------------------

def append_dropped(conn: sqlite3.Connection, candidate_id: str, items: list) -> None:
    """Add dropped items without touching earlier ones (a source read while Review is open; specs/18 §4)."""
    with conn:
        conn.executemany(
            "INSERT INTO dropped_items (id, candidate_id, evidence_id, item_type, text, payload, parent_type, "
            "parent_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(d.id, candidate_id, d.evidence_id, d.item_type, d.text, json.dumps(d.payload), d.parent_type,
              d.parent_id, d.reason, now_iso()) for d in items],
        )


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def candidate_lock(candidate_id: str) -> threading.RLock:
    """Serializes writes to one candidate's profile: a source finishing while the candidate edits Review."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(candidate_id, threading.RLock())


def save_dropped(conn: sqlite3.Connection, candidate_id: str, items: list) -> None:
    """Replace the candidate's dropped items (a re-run of extraction starts over)."""
    with conn:
        conn.execute("DELETE FROM dropped_items WHERE candidate_id = ?", (candidate_id,))
        conn.executemany(
            "INSERT INTO dropped_items (id, candidate_id, evidence_id, item_type, text, payload, parent_type, "
            "parent_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(d.id, candidate_id, d.evidence_id, d.item_type, d.text, json.dumps(d.payload), d.parent_type,
              d.parent_id, d.reason, now_iso()) for d in items],
        )


def list_dropped(conn: sqlite3.Connection, candidate_id: str, include_restored: bool = False) -> list[dict]:
    sql = "SELECT * FROM dropped_items WHERE candidate_id = ?"
    if not include_restored:
        sql += " AND restored_id IS NULL"
    rows = conn.execute(sql + " ORDER BY rowid", (candidate_id,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


def restore_dropped(conn: sqlite3.Connection, dropped_id: str, today: date | None = None) -> str:
    """Add a dropped item back to the profile with source = user_edit. Returns the new record id."""
    from core import config
    from extraction import schemas
    from extraction.validate import bullet_rec, certification_rec, claim_rec, project_rec, role_rec

    row = conn.execute("SELECT * FROM dropped_items WHERE id = ?", (dropped_id,)).fetchone()
    if row is None:
        raise KeyError(f"dropped item {dropped_id!r} not found")
    if row["restored_id"]:
        raise ValueError("This item was already added back.")
    today = today or config.today()
    cid, eid, payload, kind = row["candidate_id"], row["evidence_id"], json.loads(row["payload"]), row["item_type"]
    edit = "user_edit"

    with conn:
        if kind == "role":
            raw = schemas.Role.model_validate(payload)
            rec = role_rec(raw, eid, today, source=edit)
            rec.bullets = [bullet_rec(b, eid, source=edit) for b in raw.bullets]
            _insert_role(conn, cid, rec)
        elif kind == "project":
            raw = schemas.Project.model_validate(payload)
            rec = project_rec(raw, eid, today, source=edit)
            rec.bullets = [bullet_rec(b, eid, source=edit) for b in raw.bullets]
            _insert_project(conn, cid, rec)
        elif kind == "bullet":
            table = "roles" if row["parent_type"] == "role" else "projects"
            if conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row["parent_id"],)).fetchone() is None:
                raise ValueError(f"Its {row['parent_type']} is not in the profile. Add that back first.")
            rec = bullet_rec(schemas.Bullet.model_validate(payload), eid, source=edit)
            parent = {"role_id": row["parent_id"]} if row["parent_type"] == "role" else {"project_id": row["parent_id"]}
            _insert_bullets(conn, cid, [rec], **parent)
        elif kind == "claim":
            rec = claim_rec(schemas.Claim.model_validate(payload), eid, source=edit)
            _insert_claim(conn, cid, rec)
        elif kind == "certification":
            rec = certification_rec(schemas.Certification.model_validate(payload), eid, source=edit)
            _insert_certification(conn, cid, rec)
        else:
            raise ValueError(f"unknown dropped item type {kind!r}")
        conn.execute("UPDATE dropped_items SET restored_id = ? WHERE id = ?", (rec.id, dropped_id))
    return rec.id


def extraction_stats(conn: sqlite3.Connection, candidate_id: str | None = None) -> dict:
    """Items kept vs dropped by the quote check, for tests/extraction_report.md."""
    sql = "SELECT COALESCE(SUM(items_extracted), 0), COALESCE(SUM(items_dropped), 0) FROM evidence_items"
    params: tuple = ()
    if candidate_id is not None:
        sql += " WHERE candidate_id = ?"
        params = (candidate_id,)
    kept, dropped = conn.execute(sql, params).fetchone()
    total = kept + dropped
    return {"kept": kept, "dropped": dropped, "drop_rate": dropped / total if total else 0.0}


def load_profile(conn: sqlite3.Connection, candidate_id: str) -> Profile:
    q = lambda sql: conn.execute(sql, (candidate_id,)).fetchall()  # noqa: E731
    bullets: dict[str, list[BulletRec]] = {}
    for b in q("SELECT * FROM bullets WHERE candidate_id = ? ORDER BY rowid"):
        owner = b["role_id"] or b["project_id"]
        bullets.setdefault(owner, []).append(
            BulletRec(id=b["id"], text=b["text"], location=b["location"], evidence_id=b["evidence_id"],
                      source=b["source"] or "extracted", method=b["method"]))
    roles = [
        RoleRec(id=r["id"], title=r["title"], employer=r["employer"], industry=r["industry"],
                start_date=_date(r["start_date"]), end_date=_date(r["end_date"]), is_current=bool(r["is_current"]),
                team_size=r["team_size"], quote=r["quote"], evidence_id=r["evidence_id"],
                bullets=bullets.get(r["id"], []), review_reasons=json.loads(r["review_reasons"] or "[]"),
                source=r["source"], method=r["method"], location=_col(r, "location"),
                sources=_json(_col(r, "sources"), []))
        for r in q("SELECT * FROM roles WHERE candidate_id = ? ORDER BY rowid")
    ]
    projects = [
        ProjectRec(id=p["id"], kind=p["kind"], name=p["name"], description=p["description"], url=p["url"],
                   start_date=_date(p["start_date"]), end_date=_date(p["end_date"]), quote=p["quote"],
                   evidence_id=p["evidence_id"], role_id=p["role_id"],
                   is_fork=None if p["is_fork"] is None else bool(p["is_fork"]), own_commits=p["own_commits"],
                   bullets=bullets.get(p["id"], []), review_reasons=json.loads(p["review_reasons"] or "[]"),
                   source=p["source"], method=p["method"], details=_json(_col(p, "details"), {}),
                   sources=_json(_col(p, "sources"), []))
        for p in q("SELECT * FROM projects WHERE candidate_id = ? ORDER BY rowid")
    ]
    claims = [
        ClaimRec(id=c["id"], kind=c["kind"], name_raw=c["name_raw"], name_norm=c["name_norm"], quote=c["quote"],
                 evidence_id=c["evidence_id"], bullet_id=c["bullet_id"], source=c["source"] or "extracted",
                 method=c["method"], details=_json(_col(c, "details"), {}), skill_group=_col(c, "skill_group"),
                 sources=_json(_col(c, "sources"), []))
        for c in q("SELECT * FROM claims WHERE candidate_id = ? ORDER BY rowid")
    ]
    certs = [
        CertificationRec(id=c["id"], name=c["name"], issuer=c["issuer"], issued_date=_date(c["issued_date"]),
                         credential_id=c["credential_id"], verify_url=c["verify_url"], evidence_id=c["evidence_id"],
                         source=c["source"] or "extracted", method=c["method"], sources=_json(_col(c, "sources"), []))
        for c in q("SELECT * FROM certifications WHERE candidate_id = ? ORDER BY rowid")
    ]
    refs = [
        ReferenceRec(id=r["id"], name=r["name"], relationship=r["relationship"], contact=r["contact"],
                     statement=r["statement"])
        for r in q("SELECT * FROM references_ WHERE candidate_id = ? ORDER BY rowid")
    ]
    return Profile(roles, projects, claims, certs, refs)


# --- Candidates --------------------------------------------------------------------------------------

def list_candidates(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM candidates ORDER BY created_at DESC, rowid DESC").fetchall()
    return [dict(r) for r in rows]


def get_candidate(conn: sqlite3.Connection, candidate_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    return dict(row) if row else None


def reset_candidate_evidence(conn: sqlite3.Connection, candidate_id: str) -> None:
    """Before re-running extraction: old evidence items and dropped items go (the profile is replaced on save)."""
    with conn:
        conn.execute("DELETE FROM evidence_items WHERE candidate_id = ?", (candidate_id,))
        conn.execute("DELETE FROM dropped_items WHERE candidate_id = ?", (candidate_id,))
        conn.execute("DELETE FROM deleted_items WHERE candidate_id = ?", (candidate_id,))       # a new read starts over
        conn.execute("DELETE FROM suppressed_items WHERE candidate_id = ?", (candidate_id,))
        conn.execute("DELETE FROM extra_items WHERE candidate_id = ? AND source = 'extracted'", (candidate_id,))


# --- Review-step edits (every edited or added row is source = user_edit) ---------------------------

def _month(d: date | None) -> str | None:
    return d.strftime("%Y-%m") if d else None


def _dates_and_reasons(start: date | None, end: date | None, is_current: bool, is_role: bool,
                       today: date | None) -> tuple[date | None, date | None, bool, list[str]]:
    from core import config
    from extraction.validate import resolve_dates
    return resolve_dates(_month(start), _month(end), is_current, today=today or config.today(), is_role=is_role)


def set_bullets(conn: sqlite3.Connection, candidate_id: str, texts: list[str], *, role_id: str | None = None,
                project_id: str | None = None) -> None:
    """Replace an item's bullets. Unchanged texts keep their id (and saved judgments); new ones are user_edit."""
    owner_col, owner_id = ("role_id", role_id) if role_id else ("project_id", project_id)
    existing = conn.execute(f"SELECT * FROM bullets WHERE {owner_col} = ? ORDER BY rowid", (owner_id,)).fetchall()
    by_text = {}
    for b in existing:
        by_text.setdefault(b["text"], b)
    keep_ids = set()
    rows = []
    for text in (t.strip() for t in texts):
        if not text:
            continue
        old = by_text.get(text)
        if old is not None and old["id"] not in keep_ids:
            keep_ids.add(old["id"])
            rows.append((old["id"], old["evidence_id"], old["location"], old["source"] or "extracted", text))
        else:
            rows.append((str(uuid.uuid4()), None, None, "user_edit", text))
    removed = [b["id"] for b in existing if b["id"] not in keep_ids]
    with conn:
        conn.execute(f"DELETE FROM bullets WHERE {owner_col} = ?", (owner_id,))
        conn.executemany(
            "INSERT INTO bullets (id, candidate_id, evidence_id, role_id, project_id, text, location, source) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(bid, candidate_id, eid, role_id, project_id, text, loc, src) for bid, eid, loc, src, text in rows],
        )
        if removed:
            conn.execute(f"UPDATE claims SET bullet_id = NULL WHERE bullet_id IN ({','.join('?' * len(removed))})",
                         removed)


def save_role(conn: sqlite3.Connection, candidate_id: str, *, role_id: str | None = None, title: str,
              employer: str | None, industry: str | None, start_date: date | None, end_date: date | None,
              is_current: bool, team_size: int | None, bullets: list[str], today: date | None = None,
              location: str | None = None) -> str:
    """Add (role_id=None) or update a role from the review step."""
    if not title.strip():
        raise ValueError("A role needs a title.")
    s, e, current, reasons = _dates_and_reasons(start_date, end_date, is_current, True, today)
    role_id = role_id or str(uuid.uuid4())
    with conn:
        conn.execute(
            "INSERT INTO roles (id, candidate_id, title, employer, industry, start_date, end_date, is_current, "
            "team_size, quote, source, needs_review, review_reasons, location) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, "
            "'user_edit', ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, employer = excluded.employer, "
            "industry = excluded.industry, start_date = excluded.start_date, end_date = excluded.end_date, "
            "is_current = excluded.is_current, team_size = excluded.team_size, source = 'user_edit', "
            "needs_review = excluded.needs_review, review_reasons = excluded.review_reasons, "
            "location = excluded.location",
            (role_id, candidate_id, title.strip(), employer or None, industry or None, _iso(s), _iso(e), int(current),
             team_size, int(bool(reasons)), json.dumps(reasons), (location or "").strip() or None),
        )
    set_bullets(conn, candidate_id, bullets, role_id=role_id)
    return role_id


PROJECT_KINDS = ("project", "repo", "work_sample", "hackathon", "publication", "case_study", "presentation", "portfolio")


def save_project(conn: sqlite3.Connection, candidate_id: str, *, project_id: str | None = None, kind: str, name: str,
                 description: str | None, url: str | None, start_date: date | None, end_date: date | None,
                 bullets: list[str], today: date | None = None, role_id: str | None = None,
                 details: dict | None = None) -> str:
    if not name.strip():
        raise ValueError("A project needs a name.")
    if kind not in PROJECT_KINDS:
        raise ValueError(f"Unknown project type {kind!r}.")
    s, e, _, reasons = _dates_and_reasons(start_date, end_date, False, False, today)
    project_id = project_id or str(uuid.uuid4())
    with conn:
        conn.execute(
            "INSERT INTO projects (id, candidate_id, kind, name, description, url, start_date, end_date, quote, source, "
            "needs_review, review_reasons, role_id, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'user_edit', ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, name = excluded.name, "
            "description = excluded.description, url = excluded.url, start_date = excluded.start_date, "
            "end_date = excluded.end_date, source = 'user_edit', needs_review = excluded.needs_review, "
            "review_reasons = excluded.review_reasons, role_id = excluded.role_id, details = excluded.details",
            (project_id, candidate_id, kind, name.strip(), description or None, url or None, _iso(s), _iso(e),
             int(bool(reasons)), json.dumps(reasons), role_id or None, _clean_details(details)),
        )
    set_bullets(conn, candidate_id, bullets, project_id=project_id)
    return project_id


def delete_item(conn: sqlite3.Connection, table: str, item_id: str) -> None:
    """Delete a role, project, claim, certification (with its bullets) or reference."""
    if table not in ("roles", "projects", "claims", "certifications", "references_"):
        raise ValueError(table)
    with conn:
        if table in ("roles", "projects"):
            col = "role_id" if table == "roles" else "project_id"
            ids = [r[0] for r in conn.execute(f"SELECT id FROM bullets WHERE {col} = ?", (item_id,))]
            if ids:
                conn.execute(f"UPDATE claims SET bullet_id = NULL WHERE bullet_id IN ({','.join('?' * len(ids))})", ids)
            conn.execute(f"DELETE FROM bullets WHERE {col} = ?", (item_id,))
            if table == "roles":
                conn.execute("UPDATE projects SET role_id = NULL WHERE role_id = ?", (item_id,))
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))


def _clean_details(details: dict | None) -> str | None:
    out = {k: str(v).strip() for k, v in (details or {}).items() if v is not None and str(v).strip()}
    return json.dumps(out) if out else None


def add_claim(conn: sqlite3.Connection, candidate_id: str, kind: str, name: str, *, skill_group: str | None = None,
              details: dict | None = None, claim_id: str | None = None) -> str:
    """Add (or, with claim_id, edit) a skill, tool or award typed in Review."""
    from extraction.validate import normalize_claim
    if kind not in ("skill", "tool", "award") or not name.strip():
        raise ValueError("A skill, tool or award needs a name.")
    cid = claim_id or str(uuid.uuid4())
    with conn:
        conn.execute("INSERT INTO claims (id, candidate_id, kind, name_raw, name_norm, source, skill_group, details) "
                     "VALUES (?, ?, ?, ?, ?, 'user_edit', ?, ?) ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, "
                     "name_raw = excluded.name_raw, name_norm = excluded.name_norm, source = 'user_edit', quote = NULL, "
                     "skill_group = COALESCE(excluded.skill_group, skill_group), details = excluded.details",
                     (cid, candidate_id, kind, name.strip(), normalize_claim(name), skill_group or None,
                      _clean_details(details)))
    return cid


def save_certification(conn: sqlite3.Connection, candidate_id: str, *, cert_id: str | None = None, name: str,
                       issuer: str | None, issued_date: date | None, credential_id: str | None,
                       verify_url: str | None) -> str:
    if not name.strip():
        raise ValueError("A certification needs a name.")
    old = conn.execute("SELECT evidence_id, sources FROM certifications WHERE id = ?", (cert_id,)).fetchone() \
        if cert_id else None                       # an edit keeps where it came from (source chips)
    rec = CertificationRec(name=name.strip(), issuer=issuer or None, issued_date=issued_date,
                           credential_id=credential_id or None, verify_url=verify_url or None,
                           evidence_id=old["evidence_id"] if old else None, source="user_edit",
                           id=cert_id or str(uuid.uuid4()), sources=_json(old["sources"], []) if old else [])
    with conn:
        conn.execute("DELETE FROM certifications WHERE id = ?", (rec.id,))
        _insert_certification(conn, candidate_id, rec)
    return rec.id


REFERENCE_RELATIONSHIPS = ("manager", "supervisor", "peer", "other")


def save_reference(conn: sqlite3.Connection, candidate_id: str, *, ref_id: str | None = None, name: str,
                   relationship: str | None, contact: str | None, statement: str | None) -> str:
    """Add (or, with ref_id, edit) a reference typed in Review. An edit keeps the id; a changed relationship or
    statement changes the judge's input, so it is judged again when the profile is confirmed. Never verified."""
    if not name.strip():
        raise ValueError("A reference needs a name.")
    relationship = (relationship or "").strip().lower() or None
    if relationship is not None and relationship not in REFERENCE_RELATIONSHIPS:
        raise ValueError(f"Unknown relationship {relationship!r}.")
    rec = ReferenceRec(name.strip(), relationship, (contact or "").strip() or None, (statement or "").strip() or None,
                       id=ref_id or str(uuid.uuid4()))
    with conn:
        conn.execute("DELETE FROM references_ WHERE id = ? AND candidate_id = ?", (rec.id, candidate_id))
        _insert_reference(conn, candidate_id, rec)
    return rec.id


def confirm_dates(conn: sqlite3.Connection, table: str, item_id: str) -> None:
    """ "The months are right": clears the year-only flags without changing the dates or the item's source."""
    if table not in ("roles", "projects"):
        raise ValueError(table)
    row = conn.execute(f"SELECT review_reasons FROM {table} WHERE id = ?", (item_id,)).fetchone()
    if row is None:
        raise ValueError("Item not found.")
    reasons = [r for r in json.loads(row[0] or "[]") if r not in ("year_only_start", "year_only_end")
               and not r.startswith("sources_disagree_dates")]
    with conn:
        conn.execute(f"UPDATE {table} SET review_reasons = ?, needs_review = ? WHERE id = ?",
                     (json.dumps(reasons), int(bool(reasons)), item_id))


# --- Display-only proof types (specs/19 Phase D) -------------------------------------------------------------------

EXTRA_TYPES = ("education", "patent", "volunteering", "course", "language")


def list_extra(conn: sqlite3.Connection, candidate_id: str) -> list[ExtraItem]:
    rows = conn.execute("SELECT * FROM extra_items WHERE candidate_id = ? ORDER BY position, rowid",
                        (candidate_id,)).fetchall()
    return [ExtraItem(type=r["type"], fields=json.loads(r["fields_json"] or "{}"), evidence_id=r["evidence_id"],
                      quote=r["quote"], source=r["source"] or "extracted", id=r["id"]) for r in rows]


def save_extra(conn: sqlite3.Connection, candidate_id: str, type: str, fields: dict, item_id: str | None = None) -> str:
    """Add or edit one item typed in Review (source = user_edit)."""
    if type not in EXTRA_TYPES:
        raise ValueError(f"unknown proof type {type!r}")
    clean = {k: str(v).strip() for k, v in fields.items() if v is not None and str(v).strip() and k != "_also"}
    if not clean:
        raise ValueError("Fill in at least one field.")
    iid = item_id or str(uuid.uuid4())
    if item_id:                                  # an edit keeps where it came from
        old = conn.execute("SELECT fields_json FROM extra_items WHERE id = ?", (item_id,)).fetchone()
        if old and json.loads(old[0] or "{}").get("_also"):
            clean["_also"] = json.loads(old[0])["_also"]
    pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM extra_items WHERE candidate_id = ?",
                       (candidate_id,)).fetchone()[0]
    with conn:
        conn.execute("INSERT INTO extra_items (id, candidate_id, type, fields_json, source, position, created_at) "
                     "VALUES (?, ?, ?, ?, 'user_edit', ?, ?) ON CONFLICT(id) DO UPDATE SET "
                     "fields_json = excluded.fields_json, source = 'user_edit'",
                     (iid, candidate_id, type, json.dumps(clean), pos, now_iso()))
    return iid


EXTRA_MAIN = {"education": ("school", "degree"), "language": ("language",), "course": ("name",),
              "patent": ("title", "number"), "volunteering": ("organization", "role")}


def extra_key(type: str, fields: dict) -> str:
    from core.text import normalize_name
    return type + ":" + "|".join(normalize_name(fields.get(f)) for f in EXTRA_MAIN.get(type, ()))


def merge_extracted_extra(conn: sqlite3.Connection, candidate_id: str, evidence_id: str,
                          items: list[ExtraItem]) -> None:
    """A document's display-only items: earlier ones read from the same document go; one another document already
    named gets this document as a source (and any field it lacked); one the candidate deleted stays deleted."""
    with conn:
        conn.execute("DELETE FROM extra_items WHERE candidate_id = ? AND evidence_id = ? AND source = 'extracted'",
                     (candidate_id, evidence_id))
    gone = {r[0] for r in conn.execute("SELECT key FROM deleted_items WHERE candidate_id = ? AND item_type = 'extra'",
                                       (candidate_id,))}
    have = {extra_key(x.type, x.fields): x for x in list_extra(conn, candidate_id)}
    base = conn.execute("SELECT COALESCE(MAX(position), 0) FROM extra_items WHERE candidate_id = ?",
                        (candidate_id,)).fetchone()[0]
    with conn:
        for i, x in enumerate(items):
            k = extra_key(x.type, x.fields)
            if k in gone:
                continue
            if k in have:
                old = have[k]
                fields = {**x.fields, **old.fields} if old.source == "extracted" else dict(old.fields)
                fields["_also"] = list(dict.fromkeys([*old.fields.get("_also", []), evidence_id]))
                conn.execute("UPDATE extra_items SET fields_json = ? WHERE id = ?", (json.dumps(fields), old.id))
                continue
            have[k] = x
            conn.execute("INSERT INTO extra_items (id, candidate_id, evidence_id, type, fields_json, quote, source, "
                         "position, created_at) VALUES (?, ?, ?, ?, ?, ?, 'extracted', ?, ?)",
                         (x.id, candidate_id, evidence_id, x.type, json.dumps(x.fields), x.quote, base + i + 1,
                          now_iso()))


def delete_extra(conn: sqlite3.Connection, candidate_id: str, item_id: str) -> dict | None:
    """Delete one item; returns its row so Undo can put it back."""
    row = conn.execute("SELECT * FROM extra_items WHERE id = ? AND candidate_id = ?", (item_id, candidate_id)).fetchone()
    if row is None:
        return None
    with conn:
        conn.execute("DELETE FROM extra_items WHERE id = ?", (item_id,))
    return dict(row)


def restore_extra(conn: sqlite3.Connection, row: dict) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO extra_items (id, candidate_id, evidence_id, type, fields_json, quote, "
                     "source, position, created_at) VALUES (:id, :candidate_id, :evidence_id, :type, :fields_json, "
                     ":quote, :source, :position, :created_at)", row)


SOURCE_ORDER = ["resume", "linkedin_pdf", "portfolio", "github_repo"]


def remerge_roles(conn: sqlite3.Connection, candidate_id: str) -> int:
    """Re-run the cross-source role merge on a saved profile (no extraction, no model calls).

    Roles are regrouped by source (resume first, user-added last) and merged with the same rules as
    onboarding. Returns how many roles were merged away. Dropped bullets pointing at a merged role
    are re-pointed at the role that remains.
    """
    from extraction.validate import merge_profiles
    profile = load_profile(conn, candidate_id)
    types = {e["id"]: e["type"] for e in list_evidence(conn, candidate_id)}
    rank = lambda r: SOURCE_ORDER.index(types[r.evidence_id]) if types.get(r.evidence_id) in SOURCE_ORDER \
        else len(SOURCE_ORDER)  # noqa: E731
    groups: dict[object, list] = {}
    for r in sorted(profile.roles, key=rank):
        groups.setdefault(r.evidence_id, []).append(r)
    parts = [Profile(roles=roles) for roles in groups.values()] or [Profile()]
    rest = parts[0]
    rest.projects, rest.claims = profile.projects, profile.claims
    rest.certifications, rest.references = profile.certifications, profile.references
    merged, report = merge_profiles(parts)
    save_profile(conn, candidate_id, merged)
    with conn:
        for old, new in report.role_remap.items():
            conn.execute("UPDATE dropped_items SET parent_id = ? WHERE candidate_id = ? AND parent_id = ?",
                         (new, candidate_id, old))
    return len(report.role_remap)


def remerge(conn: sqlite3.Connection, candidate_id: str) -> dict:
    """Re-run the whole cross-source merge on a saved profile (specs/19 Phase C; no extraction, no model calls).
    Items are regrouped by the document they came from (resume, LinkedIn, portfolio, repos; typed ones last) and
    merged with today's rules, so duplicates the old merge kept become one. Returns what merged: {type: [names]}."""
    from extraction.validate import merge_profiles
    profile = load_profile(conn, candidate_id)
    types = {e["id"]: e["type"] for e in list_evidence(conn, candidate_id)}

    def rank(eid):
        t = types.get(eid)
        return SOURCE_ORDER.index(t) if t in SOURCE_ORDER else len(SOURCE_ORDER)
    groups: dict[object, Profile] = {}
    for table in ("roles", "projects", "claims", "certifications"):
        for rec in getattr(profile, table):
            getattr(groups.setdefault(rec.evidence_id, Profile()), table).append(rec)
    parts = [groups[k] for k in sorted(groups, key=lambda e: (rank(e), str(e)))] or [Profile()]
    parts[0].references = profile.references
    before = {t: [_name(x) for x in getattr(profile, t)] for t in ("roles", "projects", "claims", "certifications")}
    merged, report = merge_profiles(parts, types)
    _keep_order(profile, merged, report)
    after_ids = {x.id for t in ("roles", "projects", "claims", "certifications") for x in getattr(merged, t)}
    gone = {t: [_name(x) for x in getattr(profile, t) if x.id not in after_ids]
            for t in ("roles", "projects", "claims", "certifications")}
    save_profile(conn, candidate_id, merged)
    with conn:
        for old, new in {**report.role_remap, **report.project_remap}.items():
            conn.execute("UPDATE dropped_items SET parent_id = ? WHERE candidate_id = ? AND parent_id = ?",
                         (new, candidate_id, old))
    return {"merged_away": {t: v for t, v in gone.items() if v}, "before": before}


def _keep_order(before: Profile, merged: Profile, report) -> None:
    """Items stay in their saved order (a merged item takes its group's first place): judge inputs that list items
    (the skills list, artifacts) change only where something merged."""
    from extraction.validate import _same_certification, _same_project, claim_key
    remap = {**report.role_remap, **report.project_remap}
    same = {"projects": _same_project, "certifications": _same_certification,
            "claims": lambda a, b: claim_key(a) == claim_key(b)}
    for table in ("roles", "projects", "claims", "certifications"):
        old = getattr(before, table)
        pos = {x.id: i for i, x in enumerate(old)}

        def first(x, table=table, old=old, pos=pos):
            group = [i for o, i in pos.items() if remap.get(o) == x.id]
            if table in same:
                group += [i for i, o in enumerate(old) if same[table](o, x)]
            return min([pos.get(x.id, len(old)), *group])
        getattr(merged, table).sort(key=first)


def _name(x) -> str:
    return getattr(x, "name_raw", None) or getattr(x, "name", None) or f"{x.title} @ {x.employer}"


def remove_profile_repos(conn: sqlite3.Connection, candidate_id: str) -> list[str]:
    """Remove saved GitHub projects that are the profile README or personal-site repo (see
    ingest.github.is_profile_repo), with their bullets and evidence items. Returns the removed names."""
    from ingest.github import is_profile_repo
    cand = get_candidate(conn, candidate_id)
    user = (cand or {}).get("github_username")
    if not user:
        return []
    removed = []
    for p in load_profile(conn, candidate_id).projects:
        if p.is_github and is_profile_repo(p.name, user):
            delete_item(conn, "projects", p.id)
            if p.evidence_id:
                with conn:
                    conn.execute("DELETE FROM evidence_items WHERE id = ? AND type = 'github_repo'", (p.evidence_id,))
            removed.append(p.name)
    return removed
