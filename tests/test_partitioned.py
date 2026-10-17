"""PARTITIONED_BATCHES (Phase 3 review 2b): the all-bullets route per source document. Adding a source never lowers a
label whose evidence text is unchanged: the unchanged document keeps its saved answer, the final label is the best
across sources. FakeJudge, offline."""
from datetime import date

from core import config, db
from extraction import store
from extraction.records import BulletRec, Profile, ProjectRec
from extraction.validate import SourceDoc, validate_extraction
from graph.context import Deps
from graph.pipeline import Pipeline, run_scores
from judges.fake_judge import FakeJudge
from judges.questions import QUESTIONS
from tests.helpers import SAMPLE_RESUME_LINES, sample_resume_extract

DS = "baseline-data-scientist-entry"
TODAY = date(2026, 10, 16)


def script(q, ctx):
    """Evidence questions: shown more than 4 lines at once, this judge misses the evidence (the attention effect seen
    live); otherwise a line naming Python or SQL is a bullet. Everything else: the first option."""
    if q in ("skill_evidence", "applied_level", "tool_evidence") and "bullets" in ctx:
        lines = ctx["bullets"] or []
        none = QUESTIONS[q].none_option
        if len(lines) > 4:
            return none
        hit = next((b for b in lines if "Python" in b or "SQL" in b), None)
        if hit is None:
            return none
        return ({"skill_evidence": "bullet", "applied_level": "skill_task_result", "tool_evidence": "used"}[q], hit)
    return list(QUESTIONS[q].options)[0]


def candidate(conn) -> str:
    cid = store.create_candidate(conn, "Ada")
    text = "\n".join(SAMPLE_RESUME_LINES)
    eid = store.add_evidence(conn, cid, "resume", "upload", raw_text=text, status="validated")
    profile, _ = validate_extraction(SourceDoc(eid, "resume", text, "Resume"), sample_resume_extract(), TODAY)
    store.save_profile(conn, cid, profile)
    return cid


def add_repo(conn, cid: str) -> None:
    """A new source whose lines say nothing about the criteria."""
    eid = store.add_evidence(conn, cid, "github_repo", "api", raw_text="Repository: ada/garden", status="validated")
    profile = store.load_profile(conn, cid)
    profile.projects.append(ProjectRec(kind="repo", name="garden", description="Watering schedule for plants.",
                                       url=None, start_date=None, end_date=None, quote="Repository: ada/garden",
                                       evidence_id=eid, is_fork=False, own_commits=3,
                                       bullets=[BulletRec(text=f"Planted bed {i}.", location=None, evidence_id=eid)
                                                for i in range(5)]))
    store.save_profile(conn, cid, profile)


def skills(tmp_path, monkeypatch, partitioned: bool):
    monkeypatch.setattr(config, "PARTITIONED_BATCHES", partitioned)
    path = str(tmp_path / f"p{partitioned}.db")
    conn = db.init_app_db(path)
    cid = candidate(conn)
    judge = FakeJudge(script=script)
    pipe = Pipeline(Deps(path, judge, today=TODAY, planner=None))
    before = run_scores(conn, pipe.score(cid, DS, plan=False)["run_id"])
    asked_before = len(judge.calls)
    add_repo(conn, cid)
    after = run_scores(conn, pipe.score(cid, DS, plan=False)["run_id"])
    return before, after, judge, asked_before


def test_without_partitions_a_new_source_can_lower_a_label(tmp_path, monkeypatch):
    before, after, _, _ = skills(tmp_path, monkeypatch, partitioned=False)
    assert after["role_skills"]["score"] < before["role_skills"]["score"]       # the problem this flag fixes


def test_with_partitions_adding_a_source_never_lowers_a_label(tmp_path, monkeypatch):
    before, after, judge, asked_before = skills(tmp_path, monkeypatch, partitioned=True)
    for sub in ("role_skills", "applied_knowledge", "tools"):
        assert after[sub]["score"] >= before[sub]["score"], sub
    resume_parts = [c["_subject_id"] for k, c in judge.calls[asked_before:] if "#all@" in str(c.get("_subject_id"))]
    assert resume_parts and all("garden" not in s for s in resume_parts)
    first = {c["_subject_id"] for k, c in judge.calls[:asked_before] if "#all@" in str(c.get("_subject_id"))}
    assert not first & set(resume_parts)                     # the resume's partitions were not asked again
