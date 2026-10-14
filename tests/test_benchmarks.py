"""Phase 12 benchmarks (specs/12 §4): percentiles, peer signals, privacy, recompute. Offline (FakeJudge)."""
import json

import pytest

from core import config, db
from extraction import store
from graph.context import Deps
from graph.pipeline import Pipeline
from scoring import benchmarks as bm
from scoring.benchmarks import Member
from scoring.peer_features import FEATURES
from tests.fixtures.archetypes import ARCHETYPES, TODAY, empty_profile

DS = "baseline-data-scientist-entry"


@pytest.fixture
def demo_limits(monkeypatch):
    monkeypatch.setattr(config, "MIN_POOL", 5)
    monkeypatch.setattr(config, "MIN_GROUP", 2)


@pytest.fixture(scope="module")
def pool_db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("bm") / "bm.db")
    conn = db.init_app_db(path)
    runs = {}
    for name, build in {**ARCHETYPES, "empty": empty_profile}.items():
        cid, judge = build(conn)
        runs[name] = (cid, Pipeline(Deps(path, judge, today=TODAY), print_timings=False).score(cid, DS)["run_id"])
    return path, runs


# --- Percentiles -----------------------------------------------------------------------------------------

def test_percentile_maths_and_ties():
    assert bm.percentile(20, [10, 30]) == 50
    assert bm.percentile(20, [20, 10]) == 75             # a tie counts half
    assert bm.percentile(5, [10, 20]) == 0 and bm.percentile(50, [10, 20]) == 100
    assert [bm.round5(x) for x in (72, 73, 77.4, 2)] == [70, 75, 75, 0]


def test_minimum_pool_rule(pool_db, monkeypatch):
    path, runs = pool_db
    conn = db.connect(path)
    monkeypatch.setattr(config, "MIN_POOL", 20)
    out = bm.for_run(conn, runs["strong_ds"][1])["percentiles"]
    assert out == {"pool_size": 7, "min_pool": 20, "demo_pool": out["demo_pool"], "shown": False,
                   "reason": "Not enough candidates for a comparison yet"}
    monkeypatch.setattr(config, "MIN_POOL", 7)
    out = bm.for_run(conn, runs["strong_ds"][1])["percentiles"]
    assert out["shown"] and out["final"] == {"percentile": 100, "n": 6} and out["demo_pool"]


def test_pool_is_one_latest_run_per_candidate_for_the_same_baseline(pool_db, demo_limits):
    path, runs = pool_db
    conn = db.connect(path)
    pool = bm.baseline_pool(conn, DS)
    assert len(pool) == 7 and len({m.candidate_id for m in pool}) == 7
    assert bm.baseline_pool(conn, "baseline-ai-engineer-entry") == []      # never across baselines


# --- Peer signals ------------------------------------------------------------------------------------------

def _m(cid, trust, **features):
    return Member(f"r-{cid}", cid, None, {"experience": 0.5, "expertise": 0.5, "authority": 0.5, "trust": trust},
                  {f.key: features.get(f.key, False) for f in FEATURES})


def test_group_selection_rate_rounding_and_cap(monkeypatch):
    monkeypatch.setattr(config, "MIN_POOL", 5)
    monkeypatch.setattr(config, "MIN_GROUP", 3)
    pool = [_m("me", 0.1)] + [_m(f"low{i}", 0.2) for i in range(2)] + [
        _m("a", 0.9, manager_reference=True, aligned_second_source=True, verified_certification=True),
        _m("b", 0.9, manager_reference=True, aligned_second_source=True),
        _m("c", 0.8, manager_reference=True, verified_certification=True),
        _m("d", 0.95, aligned_second_source=False)]
    trust = bm.peer_signals(pool, "me")["dimensions"]["trust"]
    # median of the 7 trust scores is 0.8, so "above the median" is a, b and d (c sits on it)
    assert trust["group_size"] == 3 and not trust["suppressed"]
    rates = {s["feature"]: s["rate"] for s in trust["signals"]}
    assert rates == {"manager_reference": 65, "aligned_second_source": 65}       # 2 of 3 → 67 → 65; cert 1 of 3 below 50
    assert trust["signals"][0]["text"] == ("Among higher-scoring candidates for this role, 65% have a manager or "
                                           "supervisor reference confirming relevant work. You don't yet.")
    assert len(trust["signals"]) <= 3


def test_no_signal_for_a_feature_the_candidate_has_or_above_median(monkeypatch):
    monkeypatch.setattr(config, "MIN_POOL", 3)
    monkeypatch.setattr(config, "MIN_GROUP", 1)
    pool = [_m("me", 0.1, manager_reference=True), _m("x", 0.9, manager_reference=True), _m("y", 0.8)]
    assert all(s["feature"] != "manager_reference" for s in bm.peer_signals(pool, "me")["dimensions"]["trust"]["signals"])
    assert "trust" not in bm.peer_signals(pool, "x")["dimensions"]                 # above the median: nothing


def test_suppression_below_min_group(monkeypatch):
    monkeypatch.setattr(config, "MIN_POOL", 3)
    monkeypatch.setattr(config, "MIN_GROUP", 10)
    pool = [_m("me", 0.1)] + [_m(f"x{i}", 0.9, manager_reference=True) for i in range(5)]
    trust = bm.peer_signals(pool, "me")["dimensions"]["trust"]
    assert trust == {"suppressed": True, "group_size": None, "signals": []}


# --- Privacy ----------------------------------------------------------------------------------------------

def _profile_strings(conn, cid) -> set[str]:
    p = store.load_profile(conn, cid)
    cand = store.get_candidate(conn, cid)
    strings = {cand["name"]} | {b.text for b in p.all_bullets()} | {c.name_raw for c in p.claims}
    strings |= {r.title for r in p.roles} | {r.employer for r in p.roles if r.employer} | {x.name for x in p.projects}
    return {s for s in strings if s and len(s) >= 4}


def test_no_text_from_other_profiles_in_any_output(pool_db, demo_limits):
    path, runs = pool_db
    conn = db.connect(path)
    for name, (cid, run_id) in runs.items():
        result = bm.for_run(conn, run_id)
        result.pop("baseline_title")                       # the job's own title, not another profile's text
        out = json.dumps(result) + json.dumps(bm.plan_lines(conn, run_id))
        for other, (ocid, _) in runs.items():
            if ocid == cid:
                continue
            leaked = [s for s in _profile_strings(conn, ocid) if s in out]
            assert leaked == [], (name, other, leaked)


def test_percentages_are_rounded_to_5(pool_db, demo_limits):
    path, runs = pool_db
    conn = db.connect(path)
    for _, run_id in runs.values():
        for d in bm.for_run(conn, run_id)["peer_signals"]["dimensions"].values():
            assert all(s["rate"] % 5 == 0 for s in d["signals"])


# --- Recompute ---------------------------------------------------------------------------------------------

def test_percentiles_and_signals_recompute_when_a_new_run_is_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MIN_POOL", 3)
    monkeypatch.setattr(config, "MIN_GROUP", 1)
    path = str(tmp_path / "r.db")
    conn = db.init_app_db(path)
    ids = {}
    for name in ("keyword_heavy", "career_switcher", "resume_only"):
        cid, judge = ARCHETYPES[name](conn)
        ids[name] = Pipeline(Deps(path, judge, today=TODAY), print_timings=False).score(cid, DS)["run_id"]
    before = bm.for_run(conn, ids["resume_only"])["percentiles"]
    cid, judge = ARCHETYPES["strong_ds"](conn)
    Pipeline(Deps(path, judge, today=TODAY), print_timings=False).score(cid, DS)
    after = bm.for_run(conn, ids["resume_only"])["percentiles"]
    assert before["pool_size"] == 3 and after["pool_size"] == 4
    assert after["final"]["percentile"] < before["final"]["percentile"]


# --- Where it appears --------------------------------------------------------------------------------------

def test_api_endpoint_and_plan_input(pool_db, demo_limits):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from judges.fake_judge import FakeJudge
    from scoring.plan import build_user_message
    path, runs = pool_db
    client = TestClient(create_app(Deps(path, FakeJudge())))
    out = client.get(f"/runs/{runs['keyword_heavy'][1]}/benchmarks").json()
    assert out["percentiles"]["shown"] and out["baseline_title"] == "Data Scientist"
    lines = bm.plan_lines(db.connect(path), runs["keyword_heavy"][1])
    assert lines and all(t.startswith("Among higher-scoring candidates") for v in lines.values() for t in v)
    from scoring.gaps import rank_gaps
    from graph.pipeline import run_scores
    conn = db.connect(path)
    subs = run_scores(conn, runs["keyword_heavy"][1])
    baseline = db.get_baseline(conn, DS)
    msg = build_user_message(baseline, subs, config.default_weights(), rank_gaps(subs, None, baseline),
                             store.load_profile(conn, runs["keyword_heavy"][0]), lines)
    assert "what_higher_scorers_have" in msg


def test_feature_list_is_complete_and_in_one_place():
    assert {f.dimension for f in FEATURES} == set(config.DIMENSIONS)
    assert len({f.key for f in FEATURES}) == len(FEATURES) == 10
