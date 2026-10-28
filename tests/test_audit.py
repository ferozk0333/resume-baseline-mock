"""The Computation view (audit round, item 1; SCORE_AUDIT). Offline: archetypes scored with scripted labels.

- Every subcategory's own compute function, re-run on the saved judgments at the run's scoring date, gives the saved
  score, and the written-out arithmetic ends in that same number.
- The view never calls a model and never writes.
- No model output is a number in scoring: every question's options map to values in code, and scoring code never
  reads a judge's probabilities, score or confidence.
"""
import re
from pathlib import Path

import pytest

from core import config, db
from graph.audit import computation
from graph.context import Deps
from graph.pipeline import Pipeline
from tests.fixtures.archetypes import ARCHETYPES, TODAY, empty_profile

ROOT = Path(__file__).resolve().parent.parent
DS = "baseline-data-scientist-entry"


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("audit") / "a.db")
    conn = db.init_app_db(path)
    out = {}
    for name, build in {**ARCHETYPES, "empty": empty_profile}.items():
        cid, judge = build(conn)
        out[name] = Pipeline(Deps(path, judge, today=TODAY), print_timings=False).score(cid, DS, plan=False)["run_id"]
    conn.close()
    return path, out


def _last_number(line: str) -> float:
    return float(re.findall(r"-?\d+\.\d{3}", line)[-1])


def test_every_subcategory_recomputes_to_the_saved_score_and_its_arithmetic_ends_there(runs):
    path, out = runs
    for name, run_id in out.items():
        c = computation(path, run_id)
        assert c["today"] == TODAY.isoformat()                     # the run's own scoring date (runs.scored_on)
        for key, sub in c["subcategories"].items():
            assert sub["matches_saved"], (name, key, sub["score"], sub["recomputed"])
            if sub["status"] == "scored":
                assert sub["arithmetic"], (name, key)
                assert abs(_last_number(sub["arithmetic"][-1]) - sub["score"]) < 5e-4, (name, key, sub["arithmetic"])
        for d, dim in c["dimensions"].items():
            if dim["value"] is not None:
                assert abs(_last_number(dim["arithmetic"]) - dim["value"]) < 5e-4, (name, d)
        final = c["final"]["value"]
        assert final is not None and f"= {final:.1f}" in c["final"]["arithmetic"]


def test_items_left_out_carry_a_reason_and_labels_name_who_decided(runs):
    path, out = runs
    c = computation(path, out["project_strong_grad"])
    for key, sub in c["subcategories"].items():
        assert all(x.get("reason") for x in sub["excluded"]), key
        for row in sub["inputs"]:
            for who in row["decided_by"]:
                assert who["decided_by"], (key, row["text"][:40])
    rel = c["subcategories"]["relevance"]
    if rel["status"] == "scored":
        assert re.match(r"\(\d+×[\d.]+( \+ \d+×[\d.]+)*\) / \d+ = \d\.\d{3}$", rel["arithmetic"][0]), rel["arithmetic"]


def test_the_view_never_calls_a_model_and_never_writes(runs, monkeypatch):
    path, out = runs
    import judges.service as service
    monkeypatch.setattr(service.JudgmentService, "_call", lambda *a, **k: pytest.fail("a model was called"))
    conn = db.connect(path)
    before = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("judgments", "scores", "runs")}
    for run_id in out.values():
        computation(path, run_id)
    assert before == {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in before}
    conn.close()


def test_weights_from_the_sliders_change_only_the_dimension_and_final_arithmetic(runs):
    path, out = runs
    run_id = next(iter(out.values()))
    w = config.default_weights()
    w["dimensions"]["trust"] = 0
    a, b = computation(path, run_id), computation(path, run_id, w)
    assert a["subcategories"] == b["subcategories"]
    assert "20×" in a["final"]["arithmetic"] and b["final"]["arithmetic"] != a["final"]["arithmetic"]


def test_the_api_serves_it_only_when_the_flag_is_on(runs, monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import create_app
    from judges.fake_judge import FakeJudge
    path, out = runs
    run_id = next(iter(out.values()))
    client = TestClient(create_app(Deps(path, FakeJudge(script=lambda *a: None))))
    assert client.get(f"/runs/{run_id}").json()["audit"] is True
    assert client.get(f"/runs/{run_id}/computation").json()["run_id"] == run_id
    monkeypatch.setattr(config, "SCORE_AUDIT", False)
    assert client.get(f"/runs/{run_id}/computation").status_code == 404


# --- No model math ----------------------------------------------------------------------------------------------

def test_every_label_a_model_can_give_maps_to_a_value_in_code():
    from judges.questions import QUESTIONS
    from scoring import formulas as f
    maps = {"relevance": f.RELEVANCE, "scope_vs_baseline": f.SCOPE, "progression": f.PROGRESSION,
            "skill_evidence": f.SKILL_EVIDENCE, "applied_level": f.APPLIED_LEVEL, "tool_evidence": f.tool_evidence_values(),
            "complexity": f.COMPLEXITY, "impact_type": f.IMPACT, "ownership": f.OWNERSHIP, "reach": f.REACH,
            "proof_support": f.PROOF_SUPPORT,
            "reference_confirms": {"confirms_relevant_work": True, "does_not": False},
            "source_alignment": {"aligned": True, "not_aligned": False}}
    for key, q in QUESTIONS.items():
        if q.kind == "yes_no":
            assert set(q.options) == {"yes", "no"}, key                # read as True/False by Labels.yes
            continue
        assert key in maps, f"{key}: no value map in code"
        assert set(q.options) == set(maps[key]), (key, sorted(q.options), sorted(maps[key]))


def test_scoring_code_never_reads_a_judges_numbers():
    pattern = re.compile(r"\.(probabilities|confidence)\b|\bj(?:udgment)?\.score\b")
    allowed = {"graph/audit.py", "graph/context.py"}             # shown in the view and the evidence trail only
    for folder in ("scoring", "graph"):
        for p in (ROOT / folder).rglob("*.py"):
            rel = p.relative_to(ROOT).as_posix()
            if rel not in allowed:
                assert not pattern.search(p.read_text()), rel
