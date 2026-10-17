"""Show Review early (specs/18 Phase 3, deferred specs/09 item 5): each source is saved into the profile as soon as it
is read (in the usual order), Review opens once the resume is read, Confirm waits for the rest, an edit made in Review
meanwhile is kept, and early-start judging runs per source. FakeLLM / FakeJudge, offline."""
import threading
from dataclasses import asdict

from core import db
from extraction import schemas, store
from extraction.onboard import (OnboardingInputs, ProgressiveSave, Upload, docs_to_extract, form_profile, ingest,
                                run_onboarding)
from graph.context import Deps
from graph.pipeline import Pipeline
from judges.fake_judge import FakeJudge
from tests import ui_fakes
from tests.helpers import SAMPLE_RESUME_LINES, FakeLLM, make_pdf, sample_resume_extract
from tests.test_candidate import env, roles  # noqa: F401  (fixture)

LI_LINES = ["LinkedIn export", "Top Skills", "Looker", "Licenses & certifications", "Google Data Analytics Certificate",
            "Experience", "Acme Retail", "Data Analyst", "January 2023 - Present", "Seattle, Washington"]


def li_extract():
    src = lambda q, loc: schemas.Source(quote=q, location=loc)  # noqa: E731
    return schemas.ProfileExtract(
        roles=[schemas.Role(title="Data Analyst", employer="Acme Retail", industry=None, start="2023-01", end=None,
                            is_current=True, team_size=None, bullets=[], source=src("Data Analyst", "role 1"))],
        projects=[], certifications=[schemas.Certification(name="Google Data Analytics Certificate", issuer=None,
                                                           date=None, credential_id=None, verify_url=None,
                                                           source=src("Google Data Analytics Certificate", "c"))],
        claims=[schemas.Claim(kind="tool", name="Looker", bullet_ref=None, source=src("Looker", "top skills"))])


def inputs(candidate_id=None):
    return OnboardingInputs(resume=Upload("cv.pdf", make_pdf(SAMPLE_RESUME_LINES)),
                            linkedin_pdf=Upload("li.pdf", make_pdf(LI_LINES * 2)), candidate_id=candidate_id)


def shape(profile) -> dict:
    """A profile without ids (they are random), for comparing two runs."""
    d = asdict(profile)
    strip = lambda x: {k: (strip(v) if isinstance(v, dict) else [strip(i) if isinstance(i, dict) else i for i in v]  # noqa: E731
                           if isinstance(v, list) else v) for k, v in x.items()
                       if k not in ("id", "evidence_id", "bullet_id", "role_id")}
    return strip(d)


def test_saving_each_source_as_it_is_read_gives_the_same_profile_as_one_merge(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "up")
    llm = lambda: FakeLLM({"resume": sample_resume_extract(), "linkedin_pdf": li_extract()})  # noqa: E731
    one = str(tmp_path / "one.db")
    conn = db.init_app_db(one)
    once = run_onboarding(conn, inputs(), llm()).profile
    two = str(tmp_path / "two.db")
    db.init_app_db(two).close()
    res = Pipeline(Deps(two, FakeJudge(script=ui_fakes.best), llm=llm())).start_onboarding(
        inputs(), "baseline-data-scientist-entry", early=False)
    conn2 = db.connect(two)
    progressive = store.load_profile(conn2, res["candidate_id"])
    assert shape(progressive) == shape(once)
    assert {e["status"] for e in store.list_evidence(conn2, res["candidate_id"])} == {"validated"}


def test_an_edit_made_in_review_is_kept_when_the_next_source_arrives(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "up")
    path = str(tmp_path / "e.db")
    conn = db.init_app_db(path)
    cid, docs, _ = ingest(conn, inputs())
    ready = []
    saver = ProgressiveSave(path, cid, docs, form_profile(conn, cid, inputs()),
                            needs_extraction={d.evidence_id for d in docs_to_extract(docs)},
                            on_ready=lambda c, t: ready.append(t))
    saver.start()
    resume, linkedin = docs
    saver.done(resume.evidence_id, sample_resume_extract())
    assert [r.title for r in store.load_profile(conn, cid).roles] == ["Data Analyst", "Analytics Intern"]
    assert {e["type"]: e["status"] for e in store.list_evidence(conn, cid)} == {"resume": "validated",
                                                                               "linkedin_pdf": "parsed"}
    store.save_role(conn, cid, title="Volunteer Tutor", employer="City Library", industry=None, start_date=None,
                    end_date=None, is_current=True, team_size=None, bullets=["Tutored 12 students in algebra."])
    saver.done(linkedin.evidence_id, li_extract())
    result = saver.finish([])
    titles = [r.title for r in result.profile.roles]
    assert "Volunteer Tutor" in titles                                          # the edit survived
    assert "Looker" in [c.name_raw for c in result.profile.claims]               # LinkedIn joined
    assert titles.count("Data Analyst") == 1                                     # merged with the resume role
    assert ready == ["resume", "linkedin_pdf"]                                   # early start per source


class GatedLLM(FakeLLM):
    """The LinkedIn read waits until the test releases it."""

    def __init__(self, outputs):
        super().__init__(outputs)
        self.release = threading.Event()

    def parse(self, *, model, system, user, output_type):
        if "<linkedin_pdf>" in user:
            self.release.wait(30)
        return super().parse(model=model, system=system, user=user, output_type=output_type)


def test_review_opens_with_linkedin_pending_and_confirm_waits(env):  # noqa: F811
    api = env.api
    gated = GatedLLM({"resume": sample_resume_extract(), "linkedin_pdf": li_extract()})
    api.app.state.rs.deps.llm = gated
    cid = api.post("/candidates", json={"name": "Ada"}).json()["id"]
    files = {"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf"),
             "linkedin_pdf": ("li.pdf", make_pdf(LI_LINES * 2), "application/pdf")}
    read = api.post(f"/candidates/{cid}/onboard", data={"onboarding": "true"}, files=files).json()["task_id"]
    api.put(f"/candidates/{cid}/targets", json={"baseline_ids": roles(api)[:1]})
    for _ in range(300):                                                          # the resume is read first
        view = api.get(f"/candidates/{cid}/reading-progress").json()
        if view.get("review_ready"):
            break
        threading.Event().wait(0.05)
    assert view["review_ready"] and {s["type"]: s["status"] for s in view["sources"]}["linkedin_pdf"] == "reading"
    assert api.put(f"/candidates/{cid}/onboarding", json={"step": "review"}).status_code == 200
    prof = api.get(f"/candidates/{cid}/profile").json()
    assert prof["reading"] is True and prof["pending"] == [{"type": "linkedin_pdf", "label": "LinkedIn"}]
    assert [r["title"] for r in prof["profile"]["roles"]] == ["Data Analyst", "Analytics Intern"]
    r = api.post(f"/candidates/{cid}/baseline-scores")
    assert r.status_code == 409 and "still reading" in r.json()["detail"]
    gated.release.set()
    assert api.tasks.wait(read)["status"] == "done"
    prof = api.get(f"/candidates/{cid}/profile").json()
    assert prof["reading"] is False and prof["pending"] == []
    assert "Looker" in [c["name_raw"] for c in prof["profile"]["claims"]]
    assert api.post(f"/candidates/{cid}/baseline-scores").status_code == 202


def test_early_start_asked_again_while_running_runs_once_more(tmp_path, monkeypatch):
    import graph.pipeline as gp
    started, gate = [], threading.Event()

    def fake(deps, cid, bid):
        started.append(bid)
        if len(started) == 1:
            gate.wait(5)
        return 1
    monkeypatch.setattr(gp, "early_role_judgments", fake)
    pipe = Pipeline(Deps(str(tmp_path / "x.db"), FakeJudge(script=ui_fakes.best)))
    pipe.prejudge("c", ["b"])
    pipe.prejudge("c", ["b"])                                       # a new source was read meanwhile
    pipe.prejudge("c", ["b"])                                       # (asked twice: still one more run)
    gate.set()
    pipe.wait_for_prejudge("c")
    assert started == ["b", "b"]
