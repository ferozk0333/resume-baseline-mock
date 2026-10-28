"""Iteration 2 rule 1: FakeJudge archetype scores must not change (docs/archive/ITERATION_2.md, specs/09 §3).

The snapshot was recorded at the start of phase 9 on v0.1-mock. It covers every archetype
against every seeded baseline: final score, dimension scores, each subcategory's status and
score, and the multiset of judgment labels the run used. Re-record only when a scoring
change is intended and approved:  RIGHTSTAFF_RECORD_SNAPSHOT=1 pytest tests/test_regression.py
"""
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from core import db
from graph.context import Deps
from graph.pipeline import Pipeline
from tests.fixtures.archetypes import ARCHETYPES, TODAY, empty_profile

SNAPSHOT = Path(__file__).parent / "fixtures" / "archetype_scores.json"
RECORD = os.getenv("RIGHTSTAFF_RECORD_SNAPSHOT") == "1"


def _r(x):
    return None if x is None else round(x, 6)


def label_counts(conn, candidate_id: str, baseline_id: str) -> dict[str, int]:
    """subject_type|question|label → count over the candidate's used judgments for this baseline
    (plus job-independent ones). Not keyed on run_id: cached and early judgments carry other runs' ids."""
    rows = conn.execute("SELECT subject_type, question, label FROM judgments WHERE candidate_id = ? AND used = 1 "
                        "AND (baseline_id = ? OR baseline_id IS NULL)", (candidate_id, baseline_id)).fetchall()
    return dict(sorted(Counter(f"{r[0]}|{r[1]}|{r[2]}" for r in rows).items()))


def snapshot_of(path: str) -> dict:
    conn = db.init_app_db(path)
    baselines = [r[0] for r in conn.execute("SELECT id FROM baselines ORDER BY id")]
    out = {}
    for name, build in {**ARCHETYPES, "empty": empty_profile}.items():
        cid, judge = build(conn)
        for baseline in baselines:
            res = Pipeline(Deps(path, judge, today=TODAY)).score(cid, baseline)
            assert res["errors"] == [], (name, baseline, res["errors"])
            out[f"{name}@{baseline}"] = {
                "final": _r(res["final"]),
                "dimensions": {d: _r(v) for d, v in sorted(res["dimension_scores"].items())},
                "subcategories": {s: {"status": v["status"], "score": _r(v["score"])}
                                  for s, v in sorted(res["subcategory_scores"].items())},
                "labels": label_counts(conn, cid, baseline),
            }
    conn.close()
    return out


@pytest.fixture(scope="module")
def current(tmp_path_factory):
    return snapshot_of(str(tmp_path_factory.mktemp("regression") / "r.db"))


def test_archetype_scores_unchanged(current):
    if RECORD:
        SNAPSHOT.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
        pytest.skip(f"recorded {len(current)} runs to {SNAPSHOT.name}")
    expected = json.loads(SNAPSHOT.read_text())
    assert sorted(current) == sorted(expected)
    for key in expected:
        for part in ("final", "dimensions", "subcategories", "labels"):
            assert current[key][part] == expected[key][part], f"{key}: {part} changed"
