"""Spec 19: onboarding and Review UX. Phase A (uploads, wording, punctuation), B (Review edits: undo, dates,
skill groups), C (dedupe under "one claim per name"), D (more proof types; display-only ones never scored)."""
from datetime import date
from pathlib import Path

import pytest

from core import config, db
from extraction import dedupe, store
from extraction.extra import extra_items
from extraction.records import CertificationRec, ClaimRec, Profile, ProjectRec, RoleRec
from extraction.validate import merge_profiles
from tests.test_api import DS, api, onboard, wait  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent


# --- Phase A ----------------------------------------------------------------------------------------------------

def test_punctuation_spacing():
    from ingest.documents import fix_punctuation_spacing as fix
    assert fix("Python , SQL and pandas .") == "Python, SQL and pandas."
    assert fix("Led a team ( 4 people ) ; shipped !") == "Led a team (4 people); shipped!"
    assert fix("Skills : Python") == "Skills: Python"
    assert fix("C# .NET and e.g. .NET Core") == "C# .NET and e.g. .NET Core"      # never glued to the next word
    assert fix("| :--- | x |") == "| :--- | x |"                                  # Markdown table rows untouched
    assert fix("one\n. two") == "one\n. two"                                      # line breaks kept


def test_punctuation_applies_to_new_extractions_only(tmp_path):
    """The fix is part of the normalizer key: text stored under the old key is not reused or changed."""
    from ingest import documents
    from tests.helpers import SAMPLE_RESUME_LINES, make_pdf
    conn = db.connect(tmp_path / "t.db")
    pdf = make_pdf([*SAMPLE_RESUME_LINES, "Skills : Python , SQL", "Built a churn model in Python and scikit-learn ( 2023 ) ."])
    old = "Skills : Python , SQL\nBuilt a churn model in Python and scikit-learn ( 2023 ) .\n"
    assert documents.normalizer().endswith("+punct-1")
    with conn:                                                    # saved before the fix, under the old key
        conn.execute("INSERT INTO document_texts (file_sha256, normalizer, text, created_at) VALUES (?, ?, ?, 'then')",
                     (documents.sha256(pdf), documents.normalizer().removesuffix("+punct-1"), old))
    new = documents.extract_text("cv.pdf", pdf, conn)
    assert "Skills: Python" in new and "scikit-learn (2023)." in new
    keys = {r[0] for r in conn.execute("SELECT normalizer FROM document_texts")}
    assert len(keys) == 2                                         # the old row is kept as it was
    cid = store.create_candidate(conn, "Jane")
    eid = store.add_evidence(conn, cid, "resume", "upload", raw_text=old)
    assert conn.execute("SELECT raw_text FROM evidence_items WHERE id = ?", (eid,)).fetchone()[0] == old


def test_upload_size_limit_and_settings(api, monkeypatch):  # noqa: F811
    from tests.helpers import SAMPLE_RESUME_LINES, make_pdf
    assert api.get("/settings/uploads").json()["max_mb"] == config.UPLOAD_MAX_MB
    monkeypatch.setattr(config, "UPLOAD_MAX_MB", 0.001)
    cid = api.post("/candidates", json={"name": "Jane"}).json()["id"]
    r = api.post(f"/candidates/{cid}/onboard", data={"baseline_id": DS},
                 files={"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")})
    assert r.status_code == 413 and "too large" in r.json()["detail"] and "MB" in r.json()["detail"]


def test_no_verified_wording_on_candidate_or_recruiter_screens():
    """We never check a credential, so nothing shown says "verified" (specs/19 item 5)."""
    import re
    from services.review import credential_status
    from scoring.explain import FORMULAS
    from scoring.peer_features import FEATURES
    hits = []
    for path in [*(ROOT / "frontend" / "app").rglob("*.tsx"), *(ROOT / "frontend" / "components").rglob("*.tsx")]:
        for i, line in enumerate(path.read_text().splitlines(), 1):
            for s in re.findall(r'"[^"]*"|>[^<{}]+<', line):
                if re.search(r"\bverified\b|\bverifiable\b", s, re.I):
                    hits.append(f"{path.name}:{i}: {s}")
    assert hits == []
    assert not any("verified" in v.lower() for v in FORMULAS.values())
    assert not any("verifiable" in f.text.lower() for f in FEATURES)
    c = CertificationRec("X", None, None, "ABC", None, None)
    assert credential_status(c) == "Credential ID added"
    assert credential_status(CertificationRec("X", None, None, None, "https://x.io", None)) == "Link added"
    assert credential_status(CertificationRec("X", None, None, None, None, None)) is None
    assert c.verified                                              # the scoring rule itself is unchanged


# --- Phase C: dedupe ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [("Hugging Face", "HuggingFace"), ("Chroma", "ChromaDB"), ("AWS", "AWS Services"),
                                 ("Docker", "Dockerfile"), ("Jupyter Notebook", "Jupyter/Google Colab Notebooks")])
def test_seeded_aliases_merge(a, b):
    assert dedupe.canonical(a) == dedupe.canonical(b)


def test_exam_codes_acronyms_and_plurals():
    assert dedupe.key("AWS Certified Machine Learning Engineer – Associate (MLA-C01)") == \
        dedupe.key("AWS Certified Machine Learning Engineer - Associate")
    assert dedupe.exam_code("Solutions Architect (SAA-C03)") == "SAA-C03"
    assert dedupe.key("Model Context Protocol (MCP)") == dedupe.key("Model Context Protocol")
    assert dedupe.key("Large Language Models (LLMs)") == dedupe.key("Large Language Models")
    assert dedupe.key("Python (pandas)") != dedupe.key("Python")      # a bracket that is not the initials stays
    assert dedupe.key("API Gateways") == dedupe.key("API Gateway")
    assert dedupe.key("Technologies") == "technology" and dedupe.key("Kubernetes") == "kubernetes"


def test_slug_title_and_url_match_projects():
    assert dedupe.project_key("lora-ticket-router") == dedupe.project_key("LoRA Ticket Router")
    assert dedupe.project_key("LoraTicketRouter") == dedupe.project_key("Lora Ticket Router")
    assert dedupe.url_key("https://www.github.com/Jane/Repo.git/") == dedupe.url_key("github.com/jane/repo")


def test_no_fuzzy_merges_and_near_misses_are_suggestions():
    for a, b in [("SageMaker", "AWS SageMaker"), ("AWS", "Azure"), ("MySQL", "SQL"), ("Postgres", "PostgreSQL"),
                 ("containers", "Docker"), ("Python", "Pytorch")]:
        assert dedupe.canonical(a) != dedupe.canonical(b), (a, b)
    found = {(a, b) for a, b, _ in dedupe.suggestions(["SageMaker", "AWS SageMaker", "LangChain", "LangChian"])}
    assert ("SageMaker", "AWS SageMaker") in found and ("LangChain", "LangChian") in found
    # market synonyms: only same-name spellings and initials are used, never concept groupings
    assert dedupe.canonical("RAG") == dedupe.canonical("Retrieval-augmented generation")
    assert dedupe.canonical("python3") == dedupe.canonical("Python")


def _claim(name, eid, bullet=None, kind="tool"):
    return ClaimRec(kind=kind, name_raw=name, name_norm=name.lower(), quote=name, evidence_id=eid, bullet_id=bullet)


def test_merge_keeps_one_claim_strongest_link_and_all_sources():
    resume = Profile(claims=[_claim("Hugging Face", "r"), _claim("Docker", "r")])
    linkedin = Profile(claims=[_claim("HuggingFace", "l", bullet="b1"), _claim("Dockerfile", "l")])
    repo = Profile(projects=[ProjectRec("repo", "x", None, None, None, None, None, "g", own_commits=3)],
                   claims=[_claim("Docker", "g")])
    merged, _ = merge_profiles([resume, linkedin, repo], {"r": "resume", "l": "linkedin_pdf", "g": "github_repo"})
    by = {c.name_raw: c for c in merged.claims}
    assert set(by) == {"HuggingFace", "Docker"}
    assert by["HuggingFace"].bullet_id == "b1" and set(by["HuggingFace"].sources) == {"r"}   # bullet beats listed
    assert by["Docker"].evidence_id == "g" and set(by["Docker"].sources) == {"r", "l"}       # artifact beats both


def test_certifications_merge_richest_fields_and_keep_exam_code():
    a = CertificationRec("AWS Certified Machine Learning Engineer - Associate", "Amazon Web Services", None, None,
                         None, "r")
    b = CertificationRec("AWS Certified Machine Learning Engineer – Associate (MLA-C01)", "Amazon Web Services",
                         date(2025, 3, 1), "ABC-123", "https://aws.example/v", "l")
    other_issuer = CertificationRec("AWS Certified Machine Learning Engineer - Associate", "Coursera", None, None,
                                    None, "p")
    merged, _ = merge_profiles([Profile(certifications=[a]), Profile(certifications=[b, other_issuer])])
    assert len(merged.certifications) == 2                     # a different issuer on both sides stays apart
    c = merged.certifications[0]
    assert c.name.endswith("(MLA-C01)") and c.credential_id == "ABC-123" and c.issued_date == date(2025, 3, 1)
    assert c.verify_url and c.sources == ["l"] and c.evidence_id == "r"


def test_projects_merge_by_slug_and_url_and_stay_project():
    resume = Profile(projects=[ProjectRec("project", "LoRA Ticket Router", "Routes tickets", None, None, None,
                                          "LoRA Ticket Router", "r")])
    repo = Profile(projects=[ProjectRec("repo", "lora-ticket-router", None, "https://github.com/j/lora-ticket-router",
                                        None, None, "Repository: j/lora-ticket-router", "g", own_commits=12)])
    by_url = Profile(projects=[ProjectRec("project", "Ticket bot", None, "github.com/j/lora-ticket-router", None, None,
                                          "Ticket bot", "p")])
    merged, _ = merge_profiles([resume, repo, by_url])
    assert len(merged.projects) == 1
    p = merged.projects[0]
    assert p.kind == "project" and p.own_commits == 12 and p.url and set(p.sources) == {"g", "p"}



def test_sources_are_never_read_by_scoring():
    import re
    # One exception (audit round, item 2b): Ctx.from_portfolio, so a resume project merged with its portfolio case
    # study is still a portfolio work sample (as a project merged with its repository keeps its GitHub backing).
    allowed = re.compile(r"    def from_portfolio\(.*?\n\n", re.S)
    for folder in ("graph", "scoring", "judges"):
        for path in (ROOT / folder).rglob("*.py"):
            assert not re.search(r"\.sources\b|\bsources_of\b", allowed.sub("", path.read_text())), path


def test_date_conflict_between_resume_and_linkedin_is_flagged():
    a = RoleRec("Data Analyst", "Acme", None, date(2023, 1, 1), None, True, None, "q", "r")
    b = RoleRec("Data Analyst", "Acme", None, date(2023, 3, 1), None, True, None, "q", "l")
    merged, _ = merge_profiles([Profile(roles=[a]), Profile(roles=[b])])
    assert merged.roles[0].review_reasons == ["sources_disagree_dates|l|2023-03|present"]
    from services.review import conflict_message
    msg = conflict_message(merged.roles[0].review_reasons[0], "resume", {"l": "linkedin_pdf"})
    assert msg == "Your resume and LinkedIn give different dates for this role (LinkedIn: Mar 2023 – Present). Check them."


# --- Phase D: display-only proof read from text --------------------------------------------------------------------

def test_display_only_items_read_from_text():
    text = ("Experience\nData Analyst, Acme\n\n**Education**\nB.S. Statistics, Lakeshore State University · 2023 · GPA 3.7\n"
            "Skills: Python\n\nLanguages\nEnglish (Native), Spanish (Conversational)\n\nVolunteer Experience\n"
            "Code for Good — Mentor, 2021 – 2022\n- Mentored 5 students\n\nRelevant Coursework\nDatabases, Statistics\n"
            "\nPatents\nUS 11,234,567 — Ticket routing method, 2024\n")
    items = {(x.type, tuple(sorted(x.fields.items()))) for x in extra_items(text, "e")}
    assert ("education", (("degree", "B.S."), ("end", "2023"), ("field", "Statistics"), ("notes", "GPA 3.7"),
                          ("school", "Lakeshore State University"))) in items
    assert ("language", (("language", "Spanish"), ("proficiency", "Conversational"))) in items
    assert ("course", (("name", "Databases"),)) in items
    assert any(t == "volunteering" and dict(f)["organization"] == "Code for Good" and dict(f)["role"] == "Mentor"
               for t, f in items)
    assert any(t == "patent" for t, _ in items)
    # a volunteering entry already read as a role is not shown twice
    assert not [x for x in extra_items(text, "e", {"code for good"}) if x.type == "volunteering"]


def _edu_store(tmp_path):
    conn = db.connect(tmp_path / "edu.db")
    cid = store.create_candidate(conn, "Jorden")
    resume = store.add_evidence(conn, cid, "resume", "upload", raw_text="cv")
    linkedin = store.add_evidence(conn, cid, "linkedin_pdf", "upload", raw_text="li")
    return conn, cid, resume, linkedin


def _edu(**fields):
    from extraction.records import ExtraItem
    return ExtraItem("education", fields)


def _education_rows(conn, cid):
    return [x for x in store.list_extra(conn, cid) if x.type == "education"]


def test_degree_forms_match_for_education_only():
    from extraction.extra import degree_key
    same = [("B.S.", "BS", "BSc", "B.Sc.", "Bachelor of Science", "Bachelor's of Science"),
            ("B.A.", "BA", "Bachelor of Arts"), ("M.S.", "MS", "MSc", "Master of Science"), ("M.A.", "MA", "Master of Arts"),
            ("MBA", "Master of Business Administration", "Master of Business"),
            ("Ph.D.", "PhD", "Doctor of Philosophy"), ("B.Tech", "B.E.", "Bachelor of Engineering"),
            ("M.Tech", "M.E.", "Master of Engineering")]
    for forms in same:
        assert len({degree_key(f) for f in forms}) == 1, forms
    assert len({degree_key(forms[0]) for forms in same}) == len(same)          # B.S. ≠ M.S. ≠ B.A. …
    assert degree_key("Associate of Applied Science") == "associate of applied science"   # unknown: as before
    assert store.extra_key("education", {"school": "Lakeshore State University", "degree": "B.S."}) == \
        store.extra_key("education", {"school": "lakeshore state university", "degree": "Bachelor of Science"})


def test_bs_and_bachelor_of_science_merge_with_fields_and_dates(tmp_path):
    conn, cid, resume, linkedin = _edu_store(tmp_path)
    school = "Lakeshore State University"
    store.merge_extracted_extra(conn, cid, resume, [_edu(school=school, degree="B.S.", field="Computer Science", end="2026-05")])
    store.merge_extracted_extra(conn, cid, linkedin, [_edu(school=school, degree="Bachelor of Science",
                                                            field="Computer Science", start="2022-09", end="2026-05")])
    [edu] = _education_rows(conn, cid)
    assert edu.fields == {"school": school, "degree": "B.S.", "field": "Computer Science", "start": "2022-09",
                          "end": "2026-05", "_also": [linkedin]}                      # shown text unchanged


def test_different_degree_or_school_stays_separate(tmp_path):
    conn, cid, resume, linkedin = _edu_store(tmp_path)
    store.merge_extracted_extra(conn, cid, resume, [_edu(school="Lakeshore State University", degree="B.S."),
                                                    _edu(school="Bayview State University", degree="M.S.")])
    store.merge_extracted_extra(conn, cid, linkedin, [_edu(school="Lakeshore State University", degree="Master of Science"),
                                                      _edu(school="Lakeshore Community College", degree="Bachelor of Science")])
    assert sorted((x.fields["school"], x.fields["degree"]) for x in _education_rows(conn, cid)) == [
        ("Bayview State University", "M.S."), ("Lakeshore Community College", "Bachelor of Science"),
        ("Lakeshore State University", "B.S."), ("Lakeshore State University", "Master of Science")]


def test_deleted_education_stays_deleted_in_any_degree_form(tmp_path):
    from extraction import deletions
    conn, cid, resume, linkedin = _edu_store(tmp_path)
    school = "Lakeshore State University"
    store.merge_extracted_extra(conn, cid, resume, [_edu(school=school, degree="B.S.")])
    [edu] = _education_rows(conn, cid)
    deletions.remember_extra(conn, cid, store.delete_extra(conn, cid, edu.id))
    with conn:                                      # a deletion saved by the old extra_key ("…|b s")
        conn.execute("UPDATE deleted_items SET key = 'education:lakeshore state university|b s'")
    store.merge_extracted_extra(conn, cid, linkedin, [_edu(school=school, degree="Bachelor of Science")])
    store.merge_extracted_extra(conn, cid, resume, [_edu(school=school, degree="B.S.")])
    assert _education_rows(conn, cid) == []


def test_education_ignores_pdf_page_footers():
    text = ("## Education\n\nLakeshore State University\nBachelor of Science - BS, Computer Science · "
            "(September 2022 - May 2026)\n\nPage 1\n\nBayview State University\nMaster of Business Administration - MBA"
            "\nPage 2 of 3\n")
    [first, second] = [x for x in extra_items(text, "e") if x.type == "education"]
    assert first.fields == {"school": "Lakeshore State University", "degree": "Bachelor of Science",
                            "field": "Computer Science", "start": "2022-09", "end": "2026-05"}
    assert "Page" not in first.quote
    assert second.fields == {"school": "Bayview State University", "degree": "Master of Business Administration"}


def test_remerge_extra_merges_saved_duplicates(tmp_path):
    conn, cid, resume, linkedin = _edu_store(tmp_path)
    school = "Lakeshore State University"
    store.merge_extracted_extra(conn, cid, resume, [_edu(school=school, degree="B.S.", end="2026-05")])
    with conn:                                      # a duplicate saved before degree forms matched
        conn.execute("INSERT INTO extra_items (id, candidate_id, evidence_id, type, fields_json, source, position) "
                     "VALUES ('li-edu', ?, ?, 'education', ?, 'extracted', 9)",
                     (cid, linkedin, '{"school": "Lakeshore State University", "degree": "Bachelor of Science", '
                                     '"start": "2022-09", "field": "Computer Science"}'))
    assert len(_education_rows(conn, cid)) == 2
    assert store.remerge_extra(conn, cid) == ["education: Lakeshore State University — Bachelor of Science"]
    [edu] = _education_rows(conn, cid)
    assert edu.fields == {"school": school, "degree": "B.S.", "end": "2026-05", "start": "2022-09",
                          "field": "Computer Science", "_also": [linkedin]}
    assert store.remerge_extra(conn, cid) == []                                    # nothing left to merge
    # a card the candidate edited keeps its own fields; the document becomes a source
    store.save_extra(conn, cid, "education", {"school": school, "degree": "BSc"}, item_id=edu.id)
    with conn:
        conn.execute("INSERT INTO extra_items (id, candidate_id, evidence_id, type, fields_json, source, position) "
                     "VALUES ('li-2', ?, ?, 'education', '{\"school\": \"Lakeshore State University\", "
                     "\"degree\": \"Bachelor of Science\", \"start\": \"2022-09\"}', 'extracted', 10)", (cid, linkedin))
    store.remerge_extra(conn, cid)
    [mine] = _education_rows(conn, cid)
    assert mine.source == "user_edit" and mine.fields["degree"] == "BSc" and "start" not in mine.fields


# --- Review API (Phases B and D) -------------------------------------------------------------------------------------

def _profile(api, cid):  # noqa: F811
    return api.get(f"/candidates/{cid}/profile").json()


def _patch(api, cid, **edit):  # noqa: F811
    r = api.patch(f"/candidates/{cid}/profile", json=edit)
    assert r.status_code == 200, r.text
    return r.json()


def test_delete_and_undo_restores_the_same_ids_and_links(api):  # noqa: F811
    cid, _ = onboard(api)
    before = _profile(api, cid)
    role = before["profile"]["roles"][0]
    linked = {c["id"]: c["bullet_id"] for c in before["profile"]["claims"] if c["bullet_id"] in {b["id"] for b in role["bullets"]}}
    out = _patch(api, cid, op="delete", item="role", id=role["id"])
    assert out["undo"] and role["id"] not in {r["id"] for r in out["profile"]["roles"]}
    back = _patch(api, cid, op="undo", item="deleted", id=out["undo"])
    again = next(r for r in back["profile"]["roles"] if r["id"] == role["id"])
    assert [b["id"] for b in again["bullets"]] == [b["id"] for b in role["bullets"]]
    assert {c["id"]: c["bullet_id"] for c in back["profile"]["claims"] if c["id"] in linked} == linked
    assert api.patch(f"/candidates/{cid}/profile", json={"op": "undo", "item": "deleted", "id": out["undo"]}).status_code == 422


def test_reference_add_edit_delete_and_undo_in_review(api):  # noqa: F811
    cid, _ = onboard(api)
    assert _profile(api, cid)["profile"]["references"] == []
    out = _patch(api, cid, op="add", item="reference", data={
        "name": " Priya Shah ", "relationship": "manager", "contact": "priya@example.com",
        "statement": "Jane built our churn model."})
    [ref] = out["profile"]["references"]
    assert {k: ref[k] for k in ("name", "relationship", "contact", "statement")} == {
        "name": "Priya Shah", "relationship": "manager", "contact": "priya@example.com",
        "statement": "Jane built our churn model."}
    assert _profile(api, cid)["profile"]["references"] == [ref]          # GET returns it too

    out = _patch(api, cid, op="edit", item="reference", id=ref["id"], data={
        "name": "Priya Shah", "relationship": "supervisor", "contact": "", "statement": "Jane led the churn model."})
    [edited] = out["profile"]["references"]
    assert edited["id"] == ref["id"] and edited["relationship"] == "supervisor"
    assert edited["contact"] is None and edited["statement"] == "Jane led the churn model."

    out = _patch(api, cid, op="delete", item="reference", id=ref["id"])
    assert out["undo"] and out["profile"]["references"] == []
    back = _patch(api, cid, op="undo", item="deleted", id=out["undo"])
    assert back["profile"]["references"] == [edited]                      # same id and fields

    for data in ({"name": " "}, {"name": "X", "relationship": "friend"}):
        assert api.patch(f"/candidates/{cid}/profile", json={"op": "add", "item": "reference", "data": data}).status_code == 422


def test_edited_reference_is_judged_again_only_when_the_judge_input_changes():
    """reference_confirms is cached by input_hash: relationship and statement are in the input, name and contact
    are not (they can't change the answer)."""
    from types import SimpleNamespace
    from extraction.records import ReferenceRec
    from graph.nodes.trust import validation_plan
    from judges.service import input_hash

    def h(ref):
        [ask] = validation_plan(SimpleNamespace(profile=Profile(references=[ref]), baseline_summary="Data Scientist"))
        return input_hash(ask.key, ask.context)
    base = ReferenceRec("Priya", "manager", "p@x.com", "Built the churn model.", id="r1")
    assert h(base) != h(ReferenceRec("Priya", "manager", "p@x.com", "Led the churn model.", id="r1"))
    assert h(base) != h(ReferenceRec("Priya", "peer", "p@x.com", "Built the churn model.", id="r1"))
    assert h(base) == h(ReferenceRec("Priya S.", "manager", "new@x.com", "Built the churn model.", id="r1"))


def test_year_only_flag_confirm_and_review_fields(api):  # noqa: F811
    cid, _ = onboard(api)
    data = _profile(api, cid)
    intern = next(r for r in data["profile"]["roles"] if r["year_only"])
    assert set(intern["review_codes"]) == {"year_only_start", "year_only_end"} and intern["sources"] == ["Resume"]
    assert data["skill_groups"][-1] == "Other"
    assert {c["name_raw"]: c["group"] for c in data["profile"]["claims"]}["Python"] == "Languages"
    out = _patch(api, cid, op="confirm_dates", item="role", id=intern["id"])
    after = next(r for r in out["profile"]["roles"] if r["id"] == intern["id"])
    assert not after["year_only"] and after["source"] == "extracted" and after["start_date"] == intern["start_date"]


def test_role_location_project_details_and_skill_group(api):  # noqa: F811
    cid, _ = onboard(api)
    role = _profile(api, cid)["profile"]["roles"][0]
    out = _patch(api, cid, op="edit", item="role", id=role["id"], data={
        "title": role["title"], "employer": role["employer"], "location": "Austin, TX", "start_date": "2023-01",
        "is_current": True, "bullets": [b["text"] for b in reversed(role["bullets"])]})
    saved = next(r for r in out["profile"]["roles"] if r["id"] == role["id"])
    assert saved["location"] == "Austin, TX"
    assert [b["text"] for b in saved["bullets"]] == [b["text"] for b in reversed(role["bullets"])]   # reorder kept
    assert {b["id"] for b in saved["bullets"]} == {b["id"] for b in role["bullets"]}               # same ids
    out = _patch(api, cid, op="add", item="claim", data={"kind": "skill", "name": "Weird Tool", "group": "Data"})
    assert {c["name_raw"]: c["group"] for c in out["profile"]["claims"]}["Weird Tool"] == "Data"
    out = _patch(api, cid, op="add", item="claim", data={"kind": "skill", "name": "Unknown Thing"})
    assert {c["name_raw"]: c["group"] for c in out["profile"]["claims"]}["Unknown Thing"] == "Other"
    r = api.patch(f"/candidates/{cid}/profile", json={"op": "add", "item": "project", "data": {"kind": "spaceship", "name": "X"}})
    assert r.status_code == 422


def test_more_proof_maps_to_existing_kinds(api, tmp_path):  # noqa: F811
    cid, _ = onboard(api)
    out = _patch(api, cid, op="add", item="project", data={"kind": "publication", "name": "Churn at scale",
                                                           "url": "https://x.io/p", "end_date": "2024-12",
                                                           "details": {"venue": "KDD", "role": "First author"}})
    pub = next(p for p in out["profile"]["projects"] if p["name"] == "Churn at scale")
    assert pub["kind"] == "publication" and pub["source"] == "user_edit" and pub["details"]["venue"] == "KDD"
    out = _patch(api, cid, op="add", item="project", data={"kind": "project", "name": "pandas",
                                                           "description": "Fixed a groupby bug",
                                                           "details": {"proof_type": "open_source"}})
    assert next(p for p in out["profile"]["projects"] if p["name"] == "pandas")["details"]["proof_type"] == "open_source"
    out = _patch(api, cid, op="add", item="claim", data={"kind": "award", "name": "Best Paper",
                                                         "details": {"issuer": "KDD", "for": "Churn model"}})
    award = next(c for c in out["profile"]["claims"] if c["kind"] == "award")
    assert award["details"] == {"issuer": "KDD", "for": "Churn model"}
    from graph.nodes.authority import award_text
    conn = db.connect(tmp_path / "api.db")
    rec = next(c for c in store.load_profile(conn, cid).claims if c.kind == "award")
    assert award_text(rec) == "Best Paper (KDD): Churn model"


def test_display_only_items_never_reach_scoring(api):  # noqa: F811
    """Education, patents, volunteering, courses and languages are shown, never judged: the judged profile and every
    judge input are the same with or without them."""
    cid, _ = onboard(api)
    first = wait(api, api.post(f"/candidates/{cid}/score", json={"baseline_id": DS}).json()["task_id"])
    for t, fields in [("education", {"school": "MIT", "degree": "B.S."}), ("patent", {"title": "Routing"}),
                      ("volunteering", {"organization": "Code for Good"}), ("course", {"name": "Databases"}),
                      ("language", {"language": "Spanish"})]:
        out = _patch(api, cid, op="add", item="extra", data={"type": t, "fields": fields})
    assert {x["type"] for x in out["extra"]} >= {"education", "patent", "volunteering", "course", "language"}
    edu = next(x for x in out["extra"] if x["type"] == "education")
    assert edu["sources"] == ["Added by you"]
    gone = _patch(api, cid, op="delete", item="extra", id=edu["id"])
    assert edu["id"] not in {x["id"] for x in gone["extra"]}
    back = _patch(api, cid, op="undo", item="deleted", id=gone["undo"])
    assert edu["id"] in {x["id"] for x in back["extra"]}
    second = wait(api, api.post(f"/candidates/{cid}/score", json={"baseline_id": DS}).json()["task_id"])
    a, b = api.get(f"/runs/{first['result']['run_id']}").json(), api.get(f"/runs/{second['result']['run_id']}").json()
    assert a["final"] == b["final"]
    from extraction.records import Profile as P
    assert "extra" not in P.__dataclass_fields__


def test_recruiters_see_display_only_proof_and_honest_credential_wording(tmp_path):
    from services.recruiter import proof_shown
    conn = db.connect(tmp_path / "t.db")
    cid = store.create_candidate(conn, "Jane")
    eid = store.add_evidence(conn, cid, "resume", "upload", raw_text="x")
    store.save_profile(conn, cid, Profile(certifications=[CertificationRec("Cert A", "Org", None, "ID1", None, eid)]))
    store.save_extra(conn, cid, "language", {"language": "Spanish", "proficiency": "Fluent"})
    out = proof_shown(conn, cid, store.load_profile(conn, cid), store.list_evidence(conn, cid))
    assert out["certifications"] == [{"name": "Cert A", "issuer": "Org", "credential_status": "Credential ID added",
                                      "sources": ["Resume"]}]
    assert out["more"] == [{"type": "language", "label": "Languages",
                            "fields": {"language": "Spanish", "proficiency": "Fluent"}}]
    assert "verified" not in str(out).lower()


def test_remerge_keeps_saved_order_and_merges_only_duplicates(tmp_path):
    """Judge inputs list items (skills list, artifacts) in saved order: a re-merge that merges nothing changes
    nothing, and a merged item takes its group's first place."""
    conn = db.connect(tmp_path / "t.db")
    cid = store.create_candidate(conn, "Jane")
    r = store.add_evidence(conn, cid, "resume", "upload", raw_text="x")
    li = store.add_evidence(conn, cid, "linkedin_pdf", "upload", raw_text="x")
    claims = [_claim("SQL", li), _claim("Python", r), _claim("HuggingFace", li), _claim("Docker", r),
              _claim("Hugging Face", r)]
    store.save_profile(conn, cid, Profile(claims=claims))
    before = [c.name_raw for c in store.load_profile(conn, cid).claims]
    out = store.remerge(conn, cid)
    after = [c.name_raw for c in store.load_profile(conn, cid).claims]
    assert out["merged_away"] == {"claims": ["HuggingFace"]} or out["merged_away"] == {"claims": ["Hugging Face"]}
    assert len(after) == 4 and after[:2] == ["SQL", "Python"] and after[3] == "Docker"
    assert after[2] in ("HuggingFace", "Hugging Face")
    store.remerge(conn, cid)
    assert [c.name_raw for c in store.load_profile(conn, cid).claims] == after          # nothing more to merge
    assert before[:2] == after[:2]


def test_consistency_summary_lists_awards_apart_from_skills():
    """Open question 45: the source-alignment input never lists an award under "Skills and tools"."""
    from graph.nodes.trust import _summary
    lines = _summary({"roles": [], "projects": [], "claims": [
        {"kind": "tool", "name_raw": "Python"}, {"kind": "skill", "name_raw": "Forecasting"},
        {"kind": "award", "name_raw": "Dean's Merit Scholarship Award"}]})
    assert lines == ["Skills and tools: Python, Forecasting", "Awards: Dean's Merit Scholarship Award"]
    assert _summary({"claims": [{"kind": "tool", "name_raw": "SQL"}]}) == ["Skills and tools: SQL"]   # unchanged
