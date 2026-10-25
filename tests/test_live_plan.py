"""Live improvement plans from REASONING_MODEL (specs/05 §C). Opt-in: RIGHTSTAFF_LIVE=1. One Sonnet call each."""
import os

import pytest

from core import config, db
from providers.llm import ClaudeLLM
from graph.context import Deps
from graph.pipeline import Pipeline
from tests.fixtures.archetypes import TODAY, keyword_heavy, project_strong_grad, stale_expert

pytestmark = pytest.mark.skipif(os.getenv("RIGHTSTAFF_LIVE") != "1", reason="live model test; set RIGHTSTAFF_LIVE=1")


class Recorder:
    """Wraps ClaudeLLM to record how many attempts (first + retry) the plan needed."""

    def __init__(self):
        self.llm, self.calls = ClaudeLLM(), 0

    def parse(self, **kw):
        self.calls += 1
        return self.llm.parse(**kw)


@pytest.mark.parametrize("build", [keyword_heavy, stale_expert, project_strong_grad])
def test_live_plan(build, tmp_path, capsys):
    path = str(tmp_path / "p.db")
    conn = db.init_app_db(path)
    cid, judge = build(conn)
    conn.close()
    planner = Recorder()
    res = Pipeline(Deps(path, judge, today=TODAY, planner=planner)).score(cid, "baseline-data-scientist-entry")
    conn = db.connect(path)
    row = conn.execute("SELECT plan_json FROM gap_reports WHERE run_id = ?", (res["run_id"],)).fetchone()
    with capsys.disabled():
        print(f"\n===== {build.__name__} (final {res['final']:.1f}; {planner.calls} model call(s), "
              f"{config.REASONING_MODEL}) =====")
        print("Ranked gaps: " + "; ".join(f"{g['label']} {g['gain_text']}" for g in res["ranked_gaps"][:5]))
        print(res["plan_markdown"])
        import json
        stored = json.loads(row["plan_json"]) if row else {}
        print(f"Fixed by the retry: {stored.get('first_attempt_errors') or 'none'}")
        print(f"Dropped by validation: {stored.get('dropped') or 'none'}")
    assert res["plan_error"] is None and res["plan_markdown"]
    assert "up to" in res["plan_markdown"]
