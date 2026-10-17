"""Deletions in Review stick while the other sources are still read (Phase 3 review, item 5).

When the candidate deletes a role, project, skill or certification, it is remembered (normalized name; roles by the
same similarity rule the merge uses). A source that finishes later never re-adds it: the item is kept aside instead
and Review shows "LinkedIn also mentions X · Add back". A new upload starts over (clear).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date

from core import config
from core.db import new_id, now_iso
from core.text import normalize_name
from extraction import store
from extraction.records import BulletRec, CertificationRec, ClaimRec, Profile, ProjectRec, RoleRec

TYPES = {"roles": "role", "projects": "project", "claims": "claim", "certifications": "certification"}
SOURCE_LABELS = {"resume": "Your resume", "linkedin_pdf": "LinkedIn", "github_repo": "GitHub", "portfolio": "Your portfolio"}


def key_of(item_type: str, rec) -> str:
    if item_type == "role":
        return f"{normalize_name(rec.title)} @ {normalize_name(rec.employer)}"
    if item_type == "claim":
        return rec.name_norm or normalize_name(rec.name_raw)
    return normalize_name(rec.name)


def label_of(item_type: str, rec) -> str:
    if item_type == "role":
        return f"{rec.title} at {rec.employer}" if rec.employer else rec.title
    return rec.name_raw if item_type == "claim" else rec.name


def _payload(rec) -> str:
    return json.dumps(asdict(rec), default=str)


def _d(v):
    return date.fromisoformat(v) if v else None


def record(item_type: str, payload: str):
    p = json.loads(payload)

    def bullets(xs):
        return [BulletRec(text=b["text"], location=b.get("location"), evidence_id=b.get("evidence_id"),
                          source=b.get("source") or "extracted", method=b.get("method"), id=b["id"]) for b in xs]
    if item_type == "role":
        return RoleRec(title=p["title"], employer=p["employer"], industry=p["industry"], start_date=_d(p["start_date"]),
                       end_date=_d(p["end_date"]), is_current=p["is_current"], team_size=p["team_size"],
                       quote=p["quote"], evidence_id=p["evidence_id"], bullets=bullets(p["bullets"]),
                       review_reasons=p["review_reasons"], source=p["source"], method=p.get("method"), id=p["id"],
                       location=p.get("location"), sources=p.get("sources") or [])
    if item_type == "project":
        return ProjectRec(kind=p["kind"], name=p["name"], description=p["description"], url=p["url"],
                          start_date=_d(p["start_date"]), end_date=_d(p["end_date"]), quote=p["quote"],
                          evidence_id=p["evidence_id"], role_id=None, is_fork=p.get("is_fork"),
                          own_commits=p.get("own_commits"), bullets=bullets(p["bullets"]),
                          review_reasons=p["review_reasons"], source=p["source"], method=p.get("method"), id=p["id"],
                          details=p.get("details") or {}, sources=p.get("sources") or [])
    if item_type == "claim":
        return ClaimRec(kind=p["kind"], name_raw=p["name_raw"], name_norm=p["name_norm"], quote=p["quote"],
                        evidence_id=p["evidence_id"], bullet_id=None, source=p["source"], method=p.get("method"),
                        id=p["id"], details=p.get("details") or {}, skill_group=p.get("skill_group"),
                        sources=p.get("sources") or [])
    return CertificationRec(name=p["name"], issuer=p["issuer"], issued_date=_d(p["issued_date"]),
                            credential_id=p["credential_id"], verify_url=p["verify_url"], evidence_id=p["evidence_id"],
                            source=p["source"], method=p.get("method"), id=p["id"], sources=p.get("sources") or [])


def remember(conn, candidate_id: str, table: str, item_id: str) -> str | None:
    """Call before deleting the item. Returns the id Undo uses (specs/19 item 7). What pointed at the item (a claim
    named in one of its bullets, a project under a role, a claim's bullet) is kept so Undo restores the links."""
    item_type = TYPES.get(table)
    if item_type is None:
        return None
    profile = store.load_profile(conn, candidate_id)
    rec = next((x for x in getattr(profile, table) if x.id == item_id), None)
    if rec is None:
        return None
    payload = json.loads(_payload(rec))
    if item_type in ("role", "project"):
        ids = {b.id for b in rec.bullets}
        payload["_links"] = {"claims": {c.id: c.bullet_id for c in profile.claims if c.bullet_id in ids},
                             "projects": [p.id for p in profile.projects if item_type == "role" and p.role_id == rec.id]}
    elif item_type == "claim":
        payload["_links"] = {"bullet_id": rec.bullet_id}
    did = new_id()
    with conn:
        conn.execute("INSERT INTO deleted_items (id, candidate_id, item_type, key, label, payload, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (did, candidate_id, item_type, key_of(item_type, rec),
                                                      label_of(item_type, rec), json.dumps(payload), now_iso()))
    return did


def remember_extra(conn, candidate_id: str, row: dict) -> str:
    """A display-only item (education, languages, …): a later document naming it does not bring it back."""
    did = new_id()
    fields = json.loads(row["fields_json"] or "{}")
    with conn:
        conn.execute("INSERT INTO deleted_items (id, candidate_id, item_type, key, label, payload, created_at) "
                     "VALUES (?, ?, 'extra', ?, ?, ?, ?)", (did, candidate_id, store.extra_key(row["type"], fields),
                                                            row["type"], json.dumps(row), now_iso()))
    return did


def undo(conn, candidate_id: str, deleted_id: str) -> None:
    """Put a deleted item back exactly as it was (same ids, so saved judgments still apply) and forget the deletion."""
    row = conn.execute("SELECT item_type, payload FROM deleted_items WHERE id = ? AND candidate_id = ?",
                       (deleted_id, candidate_id)).fetchone()
    if row is None:
        raise ValueError("Nothing to undo.")
    payload = json.loads(row["payload"])
    with store.candidate_lock(candidate_id):
        if row["item_type"] == "extra":
            store.restore_extra(conn, payload)
        else:
            rec = record(row["item_type"], row["payload"])
            links = payload.get("_links") or {}
            if row["item_type"] == "claim" and links.get("bullet_id") and \
                    conn.execute("SELECT 1 FROM bullets WHERE id = ?", (links["bullet_id"],)).fetchone():
                rec.bullet_id = links["bullet_id"]
            insert = {"role": store._insert_role, "project": store._insert_project, "claim": store._insert_claim,
                      "certification": store._insert_certification}[row["item_type"]]
            with conn:
                insert(conn, candidate_id, rec)
                for claim_id, bullet_id in (links.get("claims") or {}).items():
                    conn.execute("UPDATE claims SET bullet_id = ? WHERE id = ? AND bullet_id IS NULL",
                                 (bullet_id, claim_id))
                for pid in links.get("projects") or []:
                    conn.execute("UPDATE projects SET role_id = ? WHERE id = ? AND role_id IS NULL", (rec.id, pid))
        with conn:
            conn.execute("DELETE FROM deleted_items WHERE id = ?", (deleted_id,))


def _same(item_type: str, row, rec) -> bool:
    if item_type != "role":
        return row["key"] == key_of(item_type, rec)
    from extraction.validate import _role_tokens, role_similarity
    gone = record("role", row["payload"])
    if row["key"] == key_of("role", rec):
        return True
    return bool(_role_tokens(gone.title) & _role_tokens(rec.title)) and \
        role_similarity(gone, rec) >= config.ROLE_MATCH_THRESHOLD


def keep_deleted_out(conn, candidate_id: str, profile: Profile) -> Profile:
    """A newly read source's profile without the items the candidate deleted; those are kept aside as mentions."""
    gone = conn.execute("SELECT item_type, key, payload FROM deleted_items WHERE candidate_id = ?",
                        (candidate_id,)).fetchall()
    if not gone:
        return profile
    out = Profile(references=profile.references)
    for table, item_type in TYPES.items():
        kept = []
        for rec in getattr(profile, table):
            if any(row["item_type"] == item_type and _same(item_type, row, rec) for row in gone):
                _mention(conn, candidate_id, item_type, rec)
            else:
                kept.append(rec)
        setattr(out, table, kept)
    alive = {b.id for b in out.all_bullets()}
    for c in out.claims:
        if c.bullet_id and c.bullet_id not in alive:
            c.bullet_id = None
    return out


def _mention(conn, candidate_id: str, item_type: str, rec) -> None:
    key = key_of(item_type, rec)
    if conn.execute("SELECT 1 FROM suppressed_items WHERE candidate_id = ? AND item_type = ? AND key = ? AND "
                    "evidence_id IS ?", (candidate_id, item_type, key, rec.evidence_id)).fetchone():
        return
    with conn:
        conn.execute("INSERT INTO suppressed_items (id, candidate_id, evidence_id, item_type, key, label, payload, "
                     "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (new_id(), candidate_id, rec.evidence_id, item_type, key, label_of(item_type, rec), _payload(rec),
                      now_iso()))


def mentions(conn, candidate_id: str) -> list[dict]:
    rows = conn.execute("SELECT s.id, s.item_type, s.label, e.type FROM suppressed_items s LEFT JOIN evidence_items e "
                        "ON e.id = s.evidence_id WHERE s.candidate_id = ? AND s.restored = 0 ORDER BY s.created_at",
                        (candidate_id,)).fetchall()
    return [{"id": r[0], "item_type": r[1], "label": r[2], "source": SOURCE_LABELS.get(r[3], "Another document")}
            for r in rows]


def add_back(conn, candidate_id: str, mention_id: str) -> None:
    from extraction.validate import merge_profiles
    row = conn.execute("SELECT item_type, key, payload FROM suppressed_items WHERE id = ? AND candidate_id = ?",
                       (mention_id, candidate_id)).fetchone()
    if row is None:
        raise ValueError("Nothing to add back.")
    rec = record(row["item_type"], row["payload"])
    extra = Profile()
    getattr(extra, {v: k for k, v in TYPES.items()}[row["item_type"]]).append(rec)
    with store.candidate_lock(candidate_id):
        merged, _ = merge_profiles([store.load_profile(conn, candidate_id), extra])
        store.save_profile(conn, candidate_id, merged)
        with conn:
            conn.execute("UPDATE suppressed_items SET restored = 1 WHERE candidate_id = ? AND item_type = ? AND key = ?",
                         (candidate_id, row["item_type"], row["key"]))
            conn.execute("DELETE FROM deleted_items WHERE candidate_id = ? AND item_type = ? AND key = ?",
                         (candidate_id, row["item_type"], row["key"]))


def clear(conn, candidate_id: str) -> None:
    with conn:
        conn.execute("DELETE FROM deleted_items WHERE candidate_id = ?", (candidate_id,))
        conn.execute("DELETE FROM suppressed_items WHERE candidate_id = ?", (candidate_id,))
