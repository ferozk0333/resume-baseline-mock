"""Spec 21: recruiter weight assistant. Offline: the model is StubWeightsLLM (hand-written stand-in answers); the
demo data is the seeded recruiter demo. Weights only change aggregation; nothing changes until Apply."""
import importlib.util
import json
import math

import pytest
from fastapi.testclient import TestClient

from backend import recruiter as rc
from backend import weight_assistant as wa
from backend.main import create_app
from core import config, db
from providers.llm import LLMError
from providers.routing import Overloaded
from graph.context import Deps
from judges.fake_judge import FakeJudge
from tests import ui_fakes
from tests.ui_fakes import StubWeightsLLM

DS = "baseline-data-scientist-entry"
TEXTS = {t["id"]: t for t in __import__("yaml").safe_load(
    (config.ROOT / "tests" / "fixtures" / "weight_texts.yaml").read_text())["texts"]}


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("wa") / "wa.db")
    spec = importlib.util.spec_from_file_location("seed", config.ROOT / "scripts" / "seed_recruiter_demo.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    seed.seed(path)
    return path


@pytest.fixture
def conn(demo):
    c = db.connect(demo)
    yield c
    c.close()


def ds_job(conn) -> dict:
    return rc.get_job(conn, next(j["id"] for j in rc.list_jobs(conn) if j["baseline_id"] == DS))


def out(dims=None, subs=None, **kw):
    return wa.WeightsOut(dimensions=[wa.DimWeight(key=k, weight=v) for k, v in (dims or {}).items()],
                         subcategories=[wa.SubWeight(dimension=d, key=k, weight=v)
                                        for d, g in (subs or {}).items() for k, v in g.items()], **kw)


DEFAULT = config.default_weights()


# --- Validation, bounds, normalization, rounding ------------------------------------------------------------------

def test_unknown_keys_are_ignored_and_missing_keys_keep_their_current_values():
    w = wa.validate(out({"experience": 30, "bogus": 50}, {"expertise": {"tools": 15, "made_up": 9}}), DEFAULT)
    assert "bogus" not in w["dimensions"] and "made_up" not in w["subcategories"]["expertise"]
    assert w["subcategories"]["expertise"] == DEFAULT["subcategories"]["expertise"]       # nothing really changed
    assert w["subcategories"]["trust"] == wa.normalized(DEFAULT)["subcategories"]["trust"]


@pytest.mark.parametrize("bad", [-5, 101, float("nan"), float("inf")])
def test_negative_out_of_range_or_non_numbers_are_invalid(bad):
    with pytest.raises(wa.InvalidSuggestion):
        wa.validate(out({"experience": bad}), DEFAULT)


def test_a_group_of_zeros_is_invalid():
    with pytest.raises(wa.InvalidSuggestion):
        wa.validate(out(subs={"trust": {"proof_density": 0, "validation_strength": 0, "consistency": 0,
                                        "evidence_quality": 0}}), DEFAULT)


def test_dimensions_are_bounded_normalized_and_rounded_to_exactly_100():
    w = wa.validate(out({"experience": 90, "expertise": 6, "authority": 2, "trust": 2},
                        {"authority": {"outcomes": 33, "impact": 33, "ownership": 34, "scope_of_impact": 0,
                                       "recognition": 0}}), DEFAULT)
    dims = w["dimensions"]
    assert sum(dims.values()) == 100 and all(config.WEIGHT_DIM_MIN <= v <= config.WEIGHT_DIM_MAX for v in dims.values())
    assert dims["experience"] == 60 and all(v % config.WEIGHT_STEP == 0 for v in dims.values())
    a = w["subcategories"]["authority"]
    assert sum(a.values()) == 100 and all(v % 5 == 0 for v in a.values()) and a["scope_of_impact"] == 0


def test_rounding_hands_leftover_steps_to_the_largest_remainders():
    r = wa._rounded({"a": 33.4, "b": 33.3, "c": 33.3}, 5)
    assert sum(r.values()) == 100 and sorted(r.values()) == [30, 35, 35]


def test_a_group_left_alone_keeps_its_exact_current_values():
    w = wa.validate(out({"experience": 30, "expertise": 30, "authority": 20, "trust": 20}), DEFAULT)
    assert w["subcategories"]["trust"] == pytest.approx(DEFAULT["subcategories"]["trust"])   # 31.58…, not rounded


# --- Fairness screen ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,hits", [
    ("Young, energetic native English speakers who are a great culture fit", ["Young", "native English speakers", "culture fit"]),
    ("Ideally an Ivy League grad with no kids", ["Ivy League", "kids"]),
    ("must be a US citizen; men preferred; good-looking; active on Instagram", ["citizen", "men", "good-looking", "Instagram"]),
    ("someone under 30, no pregnancy plans, Christian values", ["under 30", "pregnancy", "Christian"]),
])
def test_the_keyword_screen_finds_non_job_related_phrases(text, hits):
    found = wa.screen(text)
    for h in hits:
        assert any(h.lower() in f.lower() for f in found), (h, found)


@pytest.mark.parametrize("text", [
    "White-box testing, whiteboard sessions and black-box QA", "A manager who mentors the team",
    "Experience with social media marketing campaigns", "Maintains old codebases and an MIT license audit",
    "Ships native apps for iOS", "Grew adoption to over 50 countries"])
def test_the_keyword_screen_leaves_job_related_text_alone(text):
    assert wa.screen(text) == [], wa.screen(text)


def test_code_hits_the_model_missed_are_added_and_made_up_phrases_dropped():
    text = "Native English speakers with strong Python; culture fit"
    merged = wa.merge_ignored(["culture fit", "something not in the text"], wa.screen(text), text)
    assert merged == ["culture fit", "Native English speakers"]


# --- The call: prompt, retry, unavailable, cache ------------------------------------------------------------------

def test_no_applicant_data_is_sent_and_the_text_is_tagged_data(conn):
    job = ds_job(conn)
    llm = StubWeightsLLM()
    assert wa.suggest(conn, llm, job, TEXTS["senior_leads"]["text"], fresh=True)["status"] == "ok"
    prompt = llm.calls[0]["system"] + llm.calls[0]["user"]
    names = [r[0] for r in conn.execute("SELECT c.name FROM applications a JOIN candidates c ON c.id = a.candidate_id "
                                        "WHERE a.job_id = ?", (job["id"],))]
    assert names and not any(n in prompt for n in names)
    for word in ("applicant data", "resume:", "bullet:"):
        assert word not in llm.calls[0]["user"].lower()
    assert f"<recruiter_text>\n{TEXTS['senior_leads']['text']}\n</recruiter_text>" in llm.calls[0]["user"]
    assert job["title"] in llm.calls[0]["user"] and "Team default weights" in llm.calls[0]["user"]
    assert not any(p in prompt.lower() for p in ("gemini", "groq", "claude", "jev"))      # model-neutral


def test_an_invalid_answer_is_retried_once_on_the_next_model_then_a_message(conn, monkeypatch):
    monkeypatch.setattr(config, "ROUTES", {**config.ROUTES, "plan": ["gemini/a", "gemini/b", "gemini/c"]})
    monkeypatch.setattr(config, "WEIGHTS_MODEL", "route:plan")
    job = ds_job(conn)
    good = StubWeightsLLM.stub(TEXTS["verified"]["text"])
    llm = StubWeightsLLM([out({"experience": -1}), good])
    assert wa.suggest(conn, llm, job, "text one", fresh=True)["status"] == "ok"
    assert [c["model"] for c in llm.calls] == ["gemini/a", "gemini/b"]
    llm = StubWeightsLLM([out({"experience": -1}), out({"trust": 500}), good])
    res = wa.suggest(conn, llm, job, "text two", fresh=True)
    assert res == {"status": "invalid", "message": wa.INVALID} and len(llm.calls) == 2


def test_unavailable_models_fail_over_then_the_presets_message(conn, monkeypatch):
    monkeypatch.setattr(config, "ROUTES", {**config.ROUTES, "plan": ["gemini/a", "gemini/b"]})
    monkeypatch.setattr(config, "WEIGHTS_MODEL", "route:plan")
    job = ds_job(conn)

    def busy():
        e = LLMError("direct: no model could answer")
        e.__cause__ = Overloaded("503")
        return e
    llm = StubWeightsLLM([busy(), StubWeightsLLM.stub("A great engineer.")])
    assert wa.suggest(conn, llm, job, "text three", fresh=True)["status"] == "ok"
    llm = StubWeightsLLM([busy(), busy()])
    assert wa.suggest(conn, llm, job, "text four", fresh=True) == {"status": "unavailable", "message": wa.UNAVAILABLE}
    assert wa.suggest(conn, None, job, "text five", fresh=True)["status"] == "unavailable"


def test_the_same_text_is_served_from_the_cache_and_suggest_again_bypasses_it(conn):
    job = ds_job(conn)
    llm = StubWeightsLLM()
    t = TEXTS["stakeholders"]["text"]
    a = wa.suggest(conn, llm, job, t, fresh=True)
    b = wa.suggest(conn, llm, job, "  " + t.upper() + " ")           # normalized text: same key
    assert len(llm.calls) == 1
    ra, rb = wa.get(conn, job["id"], a["id"]), wa.get(conn, job["id"], b["id"])
    assert rb["cached"] == 1 and ra["weights_json"] == rb["weights_json"]
    wa.suggest(conn, llm, job, t, fresh=True)
    assert len(llm.calls) == 2


def test_text_length_and_empty_text_are_refused(conn):
    with pytest.raises(ValueError):
        wa.suggest(conn, StubWeightsLLM(), ds_job(conn), "x" * (config.WEIGHT_TEXT_MAX + 1))
    with pytest.raises(ValueError):
        wa.suggest(conn, StubWeightsLLM(), ds_job(conn), "   ")


# --- Showing, preview, apply, undo, logging ------------------------------------------------------------------------

def test_the_view_has_the_label_changes_with_reasons_and_the_ignored_notice(conn):
    job = ds_job(conn)
    sid = wa.suggest(conn, StubWeightsLLM(), job, TEXTS["protected_1"]["text"], fresh=True)["id"]
    v = wa.view(conn, job, wa.get(conn, job["id"], sid))
    assert v["label"] == "Suggested by AI · you decide"
    assert v["ignored_notice"].startswith("Some parts of your description can't be used for ranking and were ignored:")
    assert "culture fit" in v["ignored_notice"] and len(v["changes"]) + len(v["unchanged"]) == 4 + 19
    assert all(c["reason"] for c in v["changes"])


def test_the_preview_equals_an_actual_re_aggregation(conn):
    job = ds_job(conn)
    sid = wa.suggest(conn, StubWeightsLLM(), job, TEXTS["senior_leads"]["text"], fresh=True)["id"]
    row = wa.get(conn, job["id"], sid)
    v = wa.view(conn, job, row)
    p = v["preview"]
    assert p["available"] and p["up"] + p["down"] + p["same"] == p["total"]
    before = [r["application_id"] for r in rc.applicants(conn, job) if r["scored"]]
    job_after = rc.get_job(conn, job["id"])
    job_after["weights"] = v["weights"]
    after = [(r["application_id"], r["match"]) for r in rc.applicants(conn, job_after) if r["scored"]]
    assert [(r["application_id"], r["match"]) for r in p["top"]] == after[:10]
    assert [r["was"] for r in p["top"]] == [before.index(r["application_id"]) + 1 for r in p["top"]]


def test_nothing_changes_until_apply_then_undo_restores_and_every_step_is_logged(conn):
    job = ds_job(conn)
    original = job["weights"]
    sid = wa.suggest(conn, StubWeightsLLM(), job, TEXTS["shipped_metrics"]["text"], fresh=True)["id"]
    assert rc.get_job(conn, job["id"])["weights"] == original                    # HITL: no change yet
    applied = wa.apply(conn, job, wa.get(conn, job["id"], sid))
    row = wa.get(conn, job["id"], sid)
    assert applied["weights"] == json.loads(row["weights_json"]) and row["applied"] == 1 and row["applied_at"]
    assert json.loads(row["previous_weights_json"]) == original and row["recruiter"] == wa.RECRUITER
    assert row["model"] and json.loads(row["ignored_json"]) == [] and row["text"] == TEXTS["shipped_metrics"]["text"]
    restored = wa.undo(conn, rc.get_job(conn, job["id"]), row)
    assert restored["weights"] == original
    row = wa.get(conn, job["id"], sid)
    assert row["applied"] == 0 and row["undone_at"]
    hist = wa.history(conn, job["id"])
    assert len(hist) <= 5 and hist[0]["id"] == sid and hist[0]["undone"]


def test_presets_and_sliders_still_work_and_scores_never_change(conn):
    job = ds_job(conn)
    run = conn.execute("SELECT run_id FROM applications WHERE job_id = ? AND run_id IS NOT NULL", (job["id"],)).fetchone()[0]
    before = conn.execute("SELECT subcategory, score, status FROM scores WHERE run_id = ? ORDER BY 1", (run,)).fetchall()
    sid = wa.suggest(conn, StubWeightsLLM(), job, TEXTS["verified"]["text"], fresh=True)["id"]
    wa.apply(conn, job, wa.get(conn, job["id"], sid))
    assert rc.update_job(conn, job["id"], preset="Startup")["weights"] == rc.preset_weights("Startup")
    w = config.default_weights()
    w["dimensions"]["trust"] = 41
    assert rc.update_job(conn, job["id"], weights=w)["weights"] == w
    after = conn.execute("SELECT subcategory, score, status FROM scores WHERE run_id = ? ORDER BY 1", (run,)).fetchall()
    assert [tuple(x) for x in before] == [tuple(x) for x in after]


def test_the_api_end_to_end(demo):
    with TestClient(create_app(Deps(demo, FakeJudge(script=ui_fakes.best), planner=ui_fakes.AlwaysPlanner(),
                                    weights_llm=StubWeightsLLM()))) as api:
        job = next(j for j in api.get("/jobs").json() if j["baseline_id"] == DS)
        r = api.post(f"/jobs/{job['id']}/weight-suggestions", json={"text": TEXTS["projects_certs"]["text"]}).json()
        s = r["suggestion"]
        assert r["status"] == "ok" and s["changes"] and s["preview"]["available"]
        assert api.get(f"/jobs/{job['id']}/weight-suggestions/{s['id']}").json()["id"] == s["id"]
        assert api.get(f"/jobs/{job['id']}/weight-suggestions").json()[0]["id"] == s["id"]
        j = api.post(f"/jobs/{job['id']}/weight-suggestions/{s['id']}/apply").json()["job"]
        assert j["weights"] == s["weights"]
        assert api.post(f"/jobs/{job['id']}/weight-suggestions/{s['id']}/undo").json()["job"]["weights"] == job["weights"]
        assert api.post(f"/jobs/{job['id']}/weight-suggestions/{s['id']}/undo").status_code == 409
        assert api.post(f"/jobs/{job['id']}/weight-suggestions", json={"text": ""}).status_code == 422


# --- Sample set (stubs offline; the live run reports against these directions) ----------------------------------------

def check_direction(t: dict, current: dict, weights: dict, ignored: list[str]) -> list[str]:
    """Problems with one suggestion against its sample's expected direction ([] = as expected)."""
    cur = wa.normalized(current)
    flat_cur = {**cur["dimensions"], **{k: v for g in cur["subcategories"].values() for k, v in g.items()}}
    flat = {**weights["dimensions"], **{k: v for g in weights["subcategories"].values() for k, v in g.items()}}
    problems = [f"{k} not raised ({flat_cur[k]:.1f} → {flat[k]:.1f})" for k in t.get("up", []) if flat[k] <= flat_cur[k]]
    problems += [f"'{p}' not ignored" for p in t.get("ignored", []) if not any(p.lower() in i.lower() for i in ignored)]
    if t.get("small"):
        problems += [f"{k} moved {abs(flat[k] - flat_cur[k]):.0f}" for k in flat if abs(flat[k] - flat_cur[k]) > 10]
    return problems


@pytest.mark.parametrize("tid", list(TEXTS))
def test_the_stub_answers_point_the_expected_way(conn, tid):
    job = rc.get_job(conn, rc.create_job(conn, DS, f"Sample {tid}", "Acme"))     # the team default weights
    sid = wa.suggest(conn, StubWeightsLLM(), job, TEXTS[tid]["text"], fresh=True)["id"]
    row = wa.get(conn, job["id"], sid)
    assert check_direction(TEXTS[tid], job["weights"], json.loads(row["weights_json"]),
                           json.loads(row["ignored_json"])) == []
    assert all(math.isclose(sum(g.values()), 100) for g in json.loads(row["weights_json"])["subcategories"].values())
