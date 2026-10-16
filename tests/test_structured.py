"""Demo data parser (phase 11): structured test-profile files → profiles with exact quotes, no model."""
from pathlib import Path

import pytest

from core import config, db
from extraction import store
from extraction.structured import DEMO_SOURCE, VARIANT_SOURCE, load_all, load_profile_file, read_profile_file
from extraction.validate import QuoteIndex

PROFILES = config.ROOT / "rightstaff-test-profiles" / "profiles"


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    conn = db.init_app_db(str(tmp_path_factory.mktemp("s") / "s.db"))
    return conn, load_all(conn, PROFILES)


def test_all_sixteen_profiles_load_with_every_quote_in_its_source(loaded):
    conn, ids = loaded
    assert len(ids) == 16
    for pid, cid in ids.items():
        profile = store.load_profile(conn, cid)
        evidence = {e["id"]: e["raw_text"] or "" for e in store.list_evidence(conn, cid)}
        for item in [*profile.roles, *profile.projects, *profile.claims]:
            if item.quote and item.evidence_id in evidence:
                assert QuoteIndex(evidence[item.evidence_id]).contains(item.quote), (pid, item.quote)
        assert store.get_candidate(conn, cid)["data_source"] == (VARIANT_SOURCE if "_" in pid else DEMO_SOURCE)


def test_lena_park_profile_is_read_exactly(loaded):
    conn, ids = loaded
    p = store.load_profile(conn, ids["AI1"])
    titles = [(r.title, r.employer, r.is_current) for r in p.roles]
    assert ("AI Engineer", "Quillstack", True) in titles and ("Software Engineering Intern", "Pinecrest Software", False) in titles
    ai = next(r for r in p.roles if r.title == "AI Engineer")
    assert ai.start_date.isoformat() == "2024-03-01"
    assert any("38% of support tickets" in b.text for b in ai.bullets)
    repos = {x.name: x for x in p.projects if x.is_github}
    assert set(repos) >= {"rag-eval-kit", "lora-ticket-router"} and repos["rag-eval-kit"].own_commits == 112
    claims = {c.name_raw: c.kind for c in p.claims}
    assert claims["PyTorch"] == "tool" and claims["RAG"] == "skill"
    assert [c.credential_id for c in p.certifications] == ["GCP-PMLE-TEST-3391"]
    assert [(r.name, r.relationship) for r in p.references] == [("Marcus Hale", "manager")]


def test_no_role_profiles_have_projects_only_and_forks_are_kept_but_not_counted(loaded):
    conn, ids = loaded
    for pid in ("AI3", "DS3", "PM3"):
        p = store.load_profile(conn, ids[pid])
        assert p.roles == [] and p.projects, pid
    forks = [x for pid in ("AI2", "DS3") for x in store.load_profile(conn, ids[pid]).projects if x.is_fork]
    assert forks and all(x.own_commits == 0 for x in forks)


def test_design_notes_never_reach_the_data(loaded):
    conn, ids = loaded
    design = (config.ROOT / "rightstaff-test-profiles" / "DESIGN.md").read_text()
    phrase = next(line for line in design.splitlines() if len(line) > 60)[:50]
    for cid in ids.values():
        for e in store.list_evidence(conn, cid):
            assert phrase not in (e["raw_text"] or "")


def test_read_profile_file_sections():
    pf = read_profile_file(PROFILES / "data-scientist" / "DS2.md")
    assert pf.name == "Jordan Blake" and pf.role == "Data Scientist" and pf.github_username is None
    assert pf.sections["GitHub"] is None and pf.sections["Resume"].startswith("**Jordan Blake**")


def test_resume_viewer_file_is_the_rendered_pdf(tmp_path):
    rendered = tmp_path / "rendered"
    rendered.mkdir()
    (rendered / "DS4_resume.docx").write_bytes(b"docx")
    (rendered / "DS4_resume_2col.pdf").write_bytes(b"%PDF")
    conn = db.init_app_db(str(tmp_path / "r.db"))
    cid = load_profile_file(conn, PROFILES / "data-scientist" / "DS4.md", rendered)
    resume = next(e for e in store.list_evidence(conn, cid) if e["type"] == "resume")
    assert Path(resume["file_path"]).name == "DS4_resume_2col.pdf"
