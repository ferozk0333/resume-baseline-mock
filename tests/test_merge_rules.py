"""Merging and dedupe (audit round, item 2): one source's bullets per item, project names, education with the
reviewed degree groups, awards and hackathon placements. Offline, no model calls."""
from __future__ import annotations

from datetime import date

from core import db
from extraction import dedupe, store
from extraction.records import BulletRec, ClaimRec, ExtraItem, Profile, ProjectRec, RoleRec
from extraction.validate import merge_profiles


def _role(eid, bullets, title="Data Analyst", employer="Acme"):
    return RoleRec(title=title, employer=employer, industry=None, start_date=date(2023, 1, 1), end_date=None,
                   is_current=True, team_size=None, quote=title, evidence_id=eid,
                   bullets=[BulletRec(t, None, eid) for t in bullets])


def _project(eid, name, kind="project", bullets=(), url=None, details=None):
    return ProjectRec(kind=kind, name=name, description=None, url=url, start_date=None, end_date=None, quote=name,
                      evidence_id=eid, bullets=[BulletRec(t, None, eid) for t in bullets], details=details or {})


def _award(eid, name):
    return ClaimRec(kind="award", name_raw=name, name_norm=name.lower(), quote=name, evidence_id=eid)


TYPES = {"r": "resume", "l": "linkedin_pdf", "p": "portfolio"}


# --- 2a bullets ------------------------------------------------------------------------------------------------

def test_resume_bullets_win_and_linkedin_bullets_are_never_appended():
    merged, report = merge_profiles([Profile(roles=[_role("r", ["Built a churn model.", "Cut costs 10%."])]),
                                     Profile(roles=[_role("l", ["Cut costs 10%.", "Presented to leadership."])])],
                                    TYPES)
    role = merged.roles[0]
    assert [b.text for b in role.bullets] == ["Built a churn model.", "Cut costs 10%."]
    assert role.sources == ["l"]                                          # LinkedIn stays a source chip
    assert [b.text for b in report.unused_bullets] == ["Presented to leadership."]


def test_linkedin_bullets_fill_a_resume_item_that_has_none():
    merged, _ = merge_profiles([Profile(roles=[_role("r", [])]), Profile(roles=[_role("l", ["A.", "B.", "A."])])],
                               TYPES)
    assert [b.text for b in merged.roles[0].bullets] == ["A.", "B."]       # duplicates within the item dropped


def test_a_saved_item_with_appended_bullets_keeps_only_its_own_documents():
    role = _role("r", ["Own bullet."])
    role.bullets += [BulletRec("Appended from LinkedIn.", None, "l"), BulletRec("Typed by me.", None, "r",
                                                                                   source="user_edit")]
    merged, report = merge_profiles([Profile(roles=[role])], TYPES)
    assert [b.text for b in merged.roles[0].bullets] == ["Own bullet.", "Typed by me."]
    assert [b.text for b in report.unused_bullets] == ["Appended from LinkedIn."]


def test_a_claim_read_from_an_unused_bullet_becomes_a_listed_claim():
    li = _role("l", ["Shipped a React app."])
    claim = ClaimRec(kind="tool", name_raw="React", name_norm="react", quote="React", evidence_id="l",
                     bullet_id=li.bullets[0].id)
    merged, _ = merge_profiles([Profile(roles=[_role("r", ["Own bullet."])]), Profile(roles=[li], claims=[claim])],
                               TYPES)
    assert merged.claims[0].bullet_id is None


# --- 2b projects -----------------------------------------------------------------------------------------------

def test_project_names_match_on_subtitle_descriptor_slug_url_and_reviewed_groups():
    assert dedupe.same_project_name("StudySpot — finding open study rooms on campus", "StudySpot")
    assert dedupe.same_project_name("Campus dining app redesign (case study)", "Campus dining redesign")
    assert dedupe.same_project_name("Product teardown: how a language app keeps streaks going",
                                    "Language-app teardown (the published analysis).")
    assert dedupe.same_project_name("lora-ticket-router", "LoRA Ticket Router")
    assert not dedupe.same_project_name("Churn: a study", "Churn: v2")              # two subtitles never match
    assert not dedupe.same_project_name("Churn model (internship)", "Churn model")   # not a reviewed descriptor
    assert not dedupe.same_project_name("City Data Hackathon 2025", "transit-delay-hackathon")


def test_a_resume_project_and_its_portfolio_case_study_become_one():
    merged, report = merge_profiles([Profile(projects=[_project("r", "StudySpot — finding open study rooms")]),
                                     Profile(projects=[_project("p", "StudySpot", kind="case_study")])], TYPES)
    assert [p.name for p in merged.projects] == ["StudySpot — finding open study rooms"]
    assert merged.projects[0].sources == ["p"] and report.merged == 1


# --- 2c education ----------------------------------------------------------------------------------------------

def test_degree_groups_from_the_reviewed_file():
    for a, b in [("MS", "Master of Science"), ("M.S. in Data Science", "Masters"), ("MSc", "Master's"),
                 ("BS", "Bachelor of Science"), ("B.Tech", "Bachelor of Engineering"), ("B.E.", "Bachelor of Technology"),
                 ("PhD", "Doctor of Philosophy"), ("Ph.D.", "PhD"), ("MBA", "Master of Business Administration")]:
        assert dedupe.degree_group(a) == dedupe.degree_group(b) is not None, (a, b)
    assert dedupe.degree_group("MS") != dedupe.degree_group("BS")
    assert dedupe.degree_group("MBA") != dedupe.degree_group("MS")
    assert dedupe.degree_group("Diploma in Design") is None


def test_education_merges_same_school_same_degree_group_overlapping_or_missing_dates():
    a = {"school": "University of Washington", "degree": "MS", "field": "Information Management", "start": "2024-09",
         "end": "2026-06"}
    assert store.same_education(a, {"school": "University of Washington", "degree": "Master of Science"})
    assert store.same_education(a, {"school": "the University of Washington", "degree": "Master's",
                                    "start": "2025-01", "end": "2026-06"})
    assert not store.same_education(a, {"school": "University of Washington", "degree": "BS"})
    assert not store.same_education(a, {"school": "University of Washington", "degree": "MS", "start": "2018-09",
                                        "end": "2020-06"})                          # an earlier, separate degree
    assert not store.same_education(a, {"school": "Washington State University", "degree": "MS"})


def test_remerge_merges_education_entries_with_the_richest_fields_and_both_sources(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    cid = store.create_candidate(conn, "Jane")
    r = store.add_evidence(conn, cid, "resume", "upload", raw_text="x")
    li = store.add_evidence(conn, cid, "linkedin_pdf", "upload", raw_text="x")
    store.merge_extracted_extra(conn, cid, r, [ExtraItem("education", {"school": "Lakeshore State University",
                                                                        "degree": "MS", "start": "2024-09"})])
    store.merge_extracted_extra(conn, cid, li, [ExtraItem("education", {
        "school": "Lakeshore State University", "degree": "Master of Science", "field": "Data Science",
        "start": "2024-09", "end": "2026-06"})])
    [edu] = store.list_extra(conn, cid)
    assert edu.fields["degree"] == "Master of Science" and edu.fields["field"] == "Data Science"
    assert edu.fields["_also"] == [li] and edu.evidence_id == r


# --- 2d awards ---------------------------------------------------------------------------------------------------

HOROWITZ = ["Donald & Lynda Horowitz Endowed Merit Fellowship ($17,000)",
            "Donald and Lynda Horowitz Endowed Fellowship",
            "Recognized with the Donald & Lynda Horowitz Endowed Fellowship for work in responsible AI systems"]


def test_the_three_horowitz_awards_are_one_with_the_amount_and_every_source():
    merged, report = merge_profiles([Profile(claims=[_award("r", HOROWITZ[0])]),
                                     Profile(claims=[_award("l", HOROWITZ[1]), _award("l", HOROWITZ[2])])], TYPES)
    [award] = merged.claims
    assert award.name_raw == HOROWITZ[0] and award.evidence_id == "r" and award.sources == ["l"]
    assert report.merged == 2


def test_a_more_complete_later_award_name_is_kept():
    merged, _ = merge_profiles([Profile(claims=[_award("r", HOROWITZ[1])]), Profile(claims=[_award("l", HOROWITZ[0])])],
                               TYPES)
    assert merged.claims[0].name_raw == HOROWITZ[0] and merged.claims[0].sources == ["r"]


def test_award_containment_needs_two_documents_and_whole_words():
    a, b = _award("r", "Dean's List"), _award("r", "Dean's List for Excellence in Research Methods")
    assert len(merge_profiles([Profile(claims=[a, b])], TYPES)[0].claims) == 1     # equal key after "for …"
    c, d = _award("r", "Best Paper"), _award("r", "Best Paper Runner-up, ACM KDD 2025")
    assert len(merge_profiles([Profile(claims=[c, d])], TYPES)[0].claims) == 2     # same document: kept apart
    e, f = _award("r", "Best Paper"), _award("l", "Best Paper Runner-up, ACM KDD 2025")
    assert len(merge_profiles([Profile(claims=[e]), Profile(claims=[f])], TYPES)[0].claims) == 1
    assert dedupe.award_key("Chancellor's Merit Scholarship Award") == dedupe.award_key("Chancellor's Scholarship")


# --- 2e hackathon placements -------------------------------------------------------------------------------------

def test_a_hackathon_award_becomes_that_hackathons_placement():
    award = _award("r", "Top 10 Finalist from 600+ teams in CSI National Hackathon")
    merged, report = merge_profiles([Profile(projects=[_project("r", "CSI National Hackathon", kind="hackathon")],
                                             claims=[award, _award("r", "Chancellor's Merit Scholarship Award")])],
                                    TYPES)
    assert [c.name_raw for c in merged.claims] == ["Chancellor's Merit Scholarship Award"]
    assert merged.projects[0].details["placement"] == "Top 10 Finalist from 600+ teams"
    assert report.placements == [(award.name_raw, "CSI National Hackathon")]


def test_a_placement_already_entered_is_kept_and_an_unrelated_award_stays():
    hack = _project("r", "City Data Hackathon", kind="hackathon", details={"placement": "Winner"})
    merged, _ = merge_profiles([Profile(projects=[hack], claims=[
        _award("r", "First place, City Data Hackathon"), _award("r", "Hackathon volunteer award")])], TYPES)
    assert merged.projects[0].details["placement"] == "Winner"
    assert [c.name_raw for c in merged.claims] == ["Hackathon volunteer award"]
