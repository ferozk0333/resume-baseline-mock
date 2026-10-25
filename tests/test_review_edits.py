"""Phase 7: review-step edits in extraction/store.py (all edits are source = user_edit)."""
from datetime import date

import pytest

from core import db
from extraction import store
from extraction.validate import SourceDoc, merge_profiles, validate_extraction
from tests.helpers import SAMPLE_RESUME_LINES, sample_resume_extract

TODAY = date(2026, 6, 15)


@pytest.fixture
def setup(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    cid = store.create_candidate(conn, "Jane")
    profile, _ = validate_extraction(SourceDoc("ev", "resume", "\n".join(SAMPLE_RESUME_LINES), "Resume"),
                                     sample_resume_extract(), TODAY)
    store.save_profile(conn, cid, merge_profiles([profile])[0])
    yield conn, cid
    conn.close()


def test_set_bullets_keeps_ids_of_unchanged_text(setup):
    conn, cid = setup
    role = store.load_profile(conn, cid).roles[0]
    kept = role.bullets[0]
    store.set_bullets(conn, cid, [kept.text, "A new bullet."], role_id=role.id)
    bullets = store.load_profile(conn, cid).roles[0].bullets
    assert bullets[0].id == kept.id and bullets[0].source == "extracted"
    assert bullets[1].text == "A new bullet." and bullets[1].source == "user_edit"
    # the removed bullet's claim link is cleared, not left dangling
    claims = store.load_profile(conn, cid).claims
    removed = role.bullets[1].id
    assert all(c.bullet_id != removed for c in claims)


def test_save_role_recomputes_review_flags(setup):
    conn, cid = setup
    intern = store.load_profile(conn, cid).roles[1]
    assert intern.review_reasons == ["year_only_start", "year_only_end"]
    store.save_role(conn, cid, role_id=intern.id, title=intern.title, employer=intern.employer, industry=None,
                    start_date=date(2022, 6, 1), end_date=date(2022, 8, 31), is_current=False, team_size=None,
                    bullets=[b.text for b in intern.bullets], today=TODAY)
    edited = store.load_profile(conn, cid).roles[1]
    assert edited.review_reasons == [] and not edited.needs_review and edited.source == "user_edit"
    assert (edited.start_date, edited.end_date) == (date(2022, 6, 1), date(2022, 8, 1))
    future = store.save_role(conn, cid, title="Future", employer=None, industry=None, start_date=date(2027, 1, 1),
                             end_date=None, is_current=True, team_size=None, bullets=[], today=TODAY)
    assert "future_date" in next(r for r in store.load_profile(conn, cid).roles if r.id == future).review_reasons
    with pytest.raises(ValueError):
        store.save_role(conn, cid, title=" ", employer=None, industry=None, start_date=None, end_date=None,
                        is_current=False, team_size=None, bullets=[])


def test_delete_role_removes_bullets_and_links(setup):
    conn, cid = setup
    role = store.load_profile(conn, cid).roles[0]
    store.delete_item(conn, "roles", role.id)
    p = store.load_profile(conn, cid)
    assert role.id not in {r.id for r in p.roles}
    assert conn.execute("SELECT COUNT(*) FROM bullets WHERE role_id = ?", (role.id,)).fetchone()[0] == 0
    assert all(c.bullet_id is None or c.bullet_id not in {b.id for b in role.bullets} for c in p.claims)
    with pytest.raises(ValueError):
        store.delete_item(conn, "candidates", cid)


def test_add_project_claim_and_certification(setup):
    conn, cid = setup
    store.save_project(conn, cid, kind="hackathon", name="Datathon", description="2nd of 30", url=None,
                       start_date=None, end_date=date(2025, 11, 1), bullets=["Led the SQL analysis."], today=TODAY)
    store.add_claim(conn, cid, "tool", "Scikit-Learn")
    cert = store.save_certification(conn, cid, name="AWS ML", issuer="AWS", issued_date=None, credential_id="X1",
                                    verify_url=None)
    p = store.load_profile(conn, cid)
    hack = next(x for x in p.projects if x.name == "Datathon")
    assert hack.source == "user_edit" and hack.bullets[0].source == "user_edit"
    assert any(c.name_norm == "scikit learn" and c.source == "user_edit" for c in p.claims)
    c = next(x for x in p.certifications if x.id == cert)
    assert c.verified and c.source == "user_edit"
    store.save_certification(conn, cid, cert_id=cert, name="AWS ML", issuer="AWS", issued_date=None,
                             credential_id=None, verify_url=None)
    assert not next(x for x in store.load_profile(conn, cid).certifications if x.id == cert).verified


def test_remerge_roles_fixes_a_saved_profile_without_extraction(tmp_path):
    from extraction.records import Profile, RoleRec
    conn = db.connect(tmp_path / "r.db")
    cid = store.create_candidate(conn, "Jane")
    resume = store.add_evidence(conn, cid, "resume", "upload", status="validated")
    linkedin = store.add_evidence(conn, cid, "linkedin_pdf", "upload", status="validated")
    role = lambda t, e, ev: RoleRec(title=t, employer=e, industry=None, start_date=date(2022, 7, 1),  # noqa: E731
                                    end_date=date(2023, 6, 1), is_current=False, team_size=None, quote=t,
                                    evidence_id=ev)
    li = role("Software Engineer", "Quarry", linkedin)       # saved first, as the old merge left it
    kept = role("Software Engineer, Machine Learning", "Quarry Financial Services Software", resume)
    store.save_profile(conn, cid, Profile(roles=[li, kept]))
    assert store.remerge_roles(conn, cid) == 1
    roles = store.load_profile(conn, cid).roles
    assert [(r.id, r.title) for r in roles] == [(kept.id, "Software Engineer, Machine Learning")]
    assert store.remerge_roles(conn, cid) == 0               # idempotent


def test_remove_profile_repos_from_saved_profile(tmp_path):
    from extraction.records import Profile, ProjectRec
    conn = db.connect(tmp_path / "g.db")
    cid = store.create_candidate(conn, "Jane", github_username="jdoe-dev")
    def repo(name):
        eid = store.add_evidence(conn, cid, "github_repo", "api", raw_text=f"Repository: jdoe-dev/{name}")
        return ProjectRec(kind="repo", name=name, description=None, url=None, start_date=None, end_date=None,
                          quote=name, evidence_id=eid, is_fork=False, own_commits=3)
    store.save_profile(conn, cid, Profile(projects=[repo("jdoe-dev"), repo("jdoe-dev.github.io"),
                                                    repo("attention-heatmaps")]))
    assert sorted(store.remove_profile_repos(conn, cid)) == ["jdoe-dev", "jdoe-dev.github.io"]
    assert [p.name for p in store.load_profile(conn, cid).projects] == ["attention-heatmaps"]
    assert len([e for e in store.list_evidence(conn, cid) if e["type"] == "github_repo"]) == 1
