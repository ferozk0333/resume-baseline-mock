"""specs/13 §0: review fixes. Fixed inputs and FakeJudge; no network."""
import pytest

from backend import recruiter as rc
from core import config, db
from extraction import store
from graph.context import Deps
from graph.nodes.expertise import skills_plan, tools_plan
from graph.pipeline import Pipeline, run_scores
from scoring import benchmarks as bm
from tests.fixtures.archetypes import ARCHETYPES, TODAY, Builder

DS = "baseline-data-scientist-entry"
AB = "Experimentation / A/B testing"
AB_BULLET = "Designed and analyzed 6 A/B tests for the member app with product managers."


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    import importlib.util
    path = str(tmp_path_factory.mktemp("fx") / "fx.db")
    spec = importlib.util.spec_from_file_location("seed", config.ROOT / "scripts" / "seed_recruiter_demo.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    seed.seed(path)
    return path


def _ab_profile(conn):
    """Skills list says "A/B testing"; the bullet says "A/B tests" (no alias match in the bullet)."""
    b = Builder(conn, "Near-variant DS")
    b.role("Data Scientist", "Acme", start=20, current=True, bullets=[b.bullet(AB_BULLET, metric=True, direction=True)])
    b.skills_list("A/B testing", "Git")
    cid, judge = b.save()

    def script(q, ctx, base=judge.script):
        if q == "skill_evidence" and ctx["criterion"].startswith(AB):
            return ("bullet", "Designed and analyzed 6 A/B tests") if AB_BULLET in ctx.get("bullets", []) else "keyword"
        return base(q, ctx)
    judge.script = script
    return cid, judge


# --- 0.4 Skills matched only in the skills list also see every bullet ------------------------------------

def test_skills_list_only_match_goes_to_the_all_bullets_judge(tmp_path):
    from graph.context import Ctx
    conn = db.init_app_db(str(tmp_path / "a.db"))
    cid, _ = _ab_profile(conn)
    ctx = Ctx(conn=conn, deps=None, svc=None, candidate_id=cid, run_id=None, baseline=db.get_baseline(conn, DS),
              profile=store.load_profile(conn, cid), today=TODAY, evidence={})
    ab = next(c for c in ctx.baseline.criteria if c.name == AB)
    asks = {a.subject_id: a for a in skills_plan(ctx)}
    alias, everything = asks[ab.id], asks[ab.id + "#all"]
    assert alias.route is None and alias.context["bullets"] == [] and "A/B testing" in alias.context["skills_list"]
    assert everything.route == "claude" and everything.require_quote and AB_BULLET in everything.context["bullets"]
    git = next(c for c in ctx.baseline.criteria if c.name == "Git")
    tools = {a.subject_id: a for a in tools_plan(ctx)}
    assert tools[git.id].route is None and tools[git.id + "#all"].route == "claude"      # tools: same rule


@pytest.mark.parametrize("alias,everything,final", [("keyword", "missing", "keyword"), ("keyword", "bullet", "bullet"),
                                                   ("listed", "missing", "listed"), ("listed", "used", "used"),
                                                   (None, "bullet", "bullet"), ("keyword", None, "keyword")])
def test_the_all_bullets_route_only_adds_credit(alias, everything, final):
    from graph.nodes.expertise import best_label
    key = "tool_evidence" if "listed" in (alias, final) or final == "used" else "skill_evidence"
    assert best_label(key, alias, everything) == final


def test_near_variant_bullet_earns_credit_end_to_end(tmp_path):
    path = str(tmp_path / "b.db")
    conn = db.init_app_db(path)
    cid, judge = _ab_profile(conn)
    run_id = Pipeline(Deps(path, judge, today=TODAY), print_timings=False).score(cid, DS)["run_id"]
    scores = run_scores(conn, run_id)
    ab = next(c for c in db.get_baseline(conn, DS).criteria if c.name == AB)
    assert rc.criterion_labels(scores)[ab.id] == "bullet"
    job = {"must_haves": [ab.id]}
    row = next(r for r in rc.requirements(db.get_baseline(conn, DS), job, scores, store.load_profile(conn, cid))
               if r["criterion_id"] == ab.id)
    assert (row["backing"], row["source"], row["quote"]) == ("in a bullet", "role: Data Scientist — Acme", AB_BULLET)


# --- 0.2 Test variants are never applicants ---------------------------------------------------------------

def test_test_variant_is_not_an_applicant_and_not_in_a_pool(demo):
    conn = db.connect(demo)
    variant = conn.execute("SELECT id FROM candidates WHERE data_source = 'test_variant'").fetchall()
    assert len(variant) == 1
    assert conn.execute("SELECT COUNT(*) FROM applications WHERE candidate_id = ?", (variant[0][0],)).fetchone()[0] == 0
    names = [r[0] for r in conn.execute("SELECT c.name FROM applications a JOIN candidates c ON c.id = a.candidate_id")]
    assert len(names) == len(set(names))                                       # no duplicate applicant
    assert bm.is_real_run.__doc__ and "test variant" in bm.is_real_run.__doc__


def test_a_variant_run_never_enters_a_pool(tmp_path):
    path = str(tmp_path / "v.db")
    conn = db.init_app_db(path)
    cid, judge = ARCHETYPES["strong_ds"](conn)
    judge.scripted = False
    run_id = Pipeline(Deps(path, judge, today=TODAY), print_timings=False).score(cid, DS)["run_id"]
    assert bm.is_real_run(conn, run_id)
    with conn:
        conn.execute("UPDATE candidates SET data_source = 'test_variant' WHERE id = ?", (cid,))
    assert not bm.is_real_run(conn, run_id) and bm.baseline_pool(conn, DS) == []


# --- 0.3 / 0.5 Chips, table and score read one source; backing and source agree ----------------------------

def test_chips_table_and_score_agree_for_every_criterion_of_every_demo_applicant(demo):
    conn = db.connect(demo)
    checked = 0
    for job in rc.list_jobs(conn):
        for app in conn.execute("SELECT * FROM applications WHERE job_id = ?", (job["id"],)).fetchall():
            p = rc.panel(conn, dict(app), job)
            labels = rc.criterion_labels(run_scores(conn, app["run_id"])) if app["run_id"] else {}
            rows = {r["criterion_id"]: r for r in p["requirements"]}
            for chip in p["must_haves"]:
                assert chip["status"] == rows[chip["criterion_id"]]["status"], (p["name"], chip)
            for r in p["requirements"]:
                checked += 1
                assert r["label"] == labels.get(r["criterion_id"])               # the saved judgment
                if r["kind"] != "certification" and app["run_id"]:
                    assert r["status"] == rc.must_have_status(r["label"], r["kind"]), (p["name"], r)
                assert rc.BACKING_STATUS[r["backing"]] == r["status"]
                # 0.5: the source is the item that earned the backing level
                if r["backing"] == "artifact":
                    assert r["source"].startswith("repo: ")
                if r["backing"] == "listed only":
                    assert r["source"] == "skills list"
                if r["backing"] == "in a bullet" and r["source"]:
                    assert r["source"].split(":")[0] in ("role", "project", "repo")
            assert p["must_haves_met"] == sum(1 for c in p["must_haves"] if c["status"] == "met_backed")
    assert checked > 300


def test_repo_backing_names_the_repo_not_the_skills_list(demo):
    conn = db.connect(demo)
    app = dict(conn.execute("SELECT a.* FROM applications a JOIN candidates c ON c.id = a.candidate_id "
                            "WHERE c.name = 'Project-strong grad'").fetchone())
    p = rc.panel(conn, app, rc.get_job(conn, app["job_id"]))
    python = next(r for r in p["requirements"] if r["name"] == "Python")
    assert python["backing"] == "artifact" and python["source"] == "repo: Churn predictor"
