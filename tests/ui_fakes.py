"""Deps factory for UI tests (RIGHTSTAFF_DEPS_FACTORY=tests.ui_fakes:make_deps). No network."""
from core import config
from graph.context import Deps
from judges.fake_judge import FakeJudge
from judges.questions import QUESTIONS
from scoring.plan import Action, Plan
from tests.helpers import FakeLLM, sample_resume_extract

CALLS = {"judge": 0, "planner": 0}


def best(q, c):
    CALLS["judge"] += 1
    return list(QUESTIONS[q].options)[0]


class AlwaysPlanner:
    def parse(self, *, model, system, user, output_type):
        CALLS["planner"] += 1
        import json
        import re
        keys = re.findall(r'"key": "(\w+)"', user)
        return Plan(actions=[Action(type="build_project", title="Build an A/B test project",
                                    what_to_do="Design and analyze an experiment.", must_show=["public repo"],
                                    addresses=[k for k in keys if k in ("applied_knowledge", "role_skills", "tools",
                                                                        "proof_density", "impact")][:2] or keys[:1],
                                    covers_criteria=["Python"], builds_on=None)],
                    quick_wins=["Link your repo"], not_actionable=[])


def make_deps() -> Deps:
    return Deps(str(config.DB_PATH), FakeJudge(script=best), llm=FakeLLM({"resume": sample_resume_extract()}),
                planner=AlwaysPlanner())
