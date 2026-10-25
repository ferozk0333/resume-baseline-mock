"""Deps factory for UI tests (RIGHTSTAFF_DEPS_FACTORY=tests.ui_fakes:make_deps). No network."""
from core import config
from graph.context import Deps
from judges.fake_judge import FakeJudge
from judges.questions import QUESTIONS
from scoring.plan import Action, Plan
from extraction import schemas
from tests.helpers import FakeLLM, sample_resume_extract

CALLS = {"judge": 0, "planner": 0}

# Early Review (specs/18 phase 3): this LinkedIn PDF takes the fake reader a few seconds, so Review opens with
# "Still reading LinkedIn…" and its items appear when it finishes.
SLOW_LINKEDIN_LINES = ["SLOW-LINKEDIN Priya Natarajan", "Data Analyst at Acme Retail", "Seattle, Washington",
                       "Top Skills", "Looker", "Licenses & certifications", "Google Data Analytics Certificate",
                       "Experience", "Acme Retail", "Data Analyst", "January 2023 - Present"]
SLOW_SECONDS = 6.0


def slow_linkedin_extract() -> schemas.ProfileExtract:
    src = lambda q, loc: schemas.Source(quote=q, location=loc)  # noqa: E731
    return schemas.ProfileExtract(
        roles=[], projects=[],
        claims=[schemas.Claim(kind="tool", name="Looker", bullet_ref=None, source=src("Looker", "top skills"))],
        certifications=[schemas.Certification(name="Google Data Analytics Certificate", issuer=None, date=None,
                                              credential_id=None, verify_url=None,
                                              source=src("Google Data Analytics Certificate", "certifications"))])


class SlowLinkedIn(FakeLLM):
    def parse(self, *, model, system, user, output_type):
        if "SLOW-LINKEDIN" in user:
            import time
            time.sleep(SLOW_SECONDS)
            return slow_linkedin_extract()
        return super().parse(model=model, system=system, user=user, output_type=output_type)


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
    """No network, except EMAIL_LIVE=1: recruiter email drafts then come from the real EMAIL_MODEL (demo)."""
    import os
    writer = None
    if os.getenv("EMAIL_LIVE") == "1":
        from providers.llm import RoutedLLM
        writer = RoutedLLM()
    job_llm = None
    if os.getenv("JOB_EXTRACT_FAKE") == "1":                # e2e: the scripted Brightwater answer, else the fallback
        from tests.helpers import FakeJobReader
        job_llm = FakeJobReader()
    if os.getenv("JOB_EXTRACT_LIVE") == "1":                # reading job descriptions with JOB_EXTRACT_MODEL (demo)
        from providers.llm import RoutedLLM
        job_llm = RoutedLLM(max_tokens=config.EXTRACT_MAX_TOKENS)
    return Deps(str(config.DB_PATH), FakeJudge(script=best), llm=SlowLinkedIn({"resume": sample_resume_extract()}),
                planner=AlwaysPlanner(), writer=writer, job_llm=job_llm, weights_llm=StubWeightsLLM())


class StubWeightsLLM:
    """The weight assistant on fakes: the hand-written stand-in answer of tests/fixtures/weight_texts.yaml for a
    sample text, else the "vague" one. No network. `calls` records every prompt it was given (tests assert on it)."""

    def __init__(self, answers: list | None = None):
        self.answers = list(answers or [])          # explicit answers in order (tests: invalid → retry)
        self.calls: list[dict] = []

    @staticmethod
    def stub(text: str):
        import yaml
        from pathlib import Path
        from services.weight_assistant import DimWeight, Reason, SubWeight, WeightsOut
        texts = yaml.safe_load((Path(__file__).parent / "fixtures" / "weight_texts.yaml").read_text())["texts"]
        t = next((x for x in texts if x["text"].strip().lower() == text.strip().lower()),
                 next(x for x in texts if x["id"] == "vague"))["stub"]
        return WeightsOut(dimensions=[DimWeight(key=k, weight=v) for k, v in t["dimensions"].items()],
                          subcategories=[SubWeight(dimension=d, key=k, weight=v)
                                         for d, g in t.get("subcategories", {}).items() for k, v in g.items()],
                          reasons=[Reason(key=k, reason=v) for k, v in t.get("reasons", {}).items()],
                          summary=t.get("summary", ""), ignored=t.get("ignored", []))

    def parse(self, *, model: str, system: str, user: str, output_type):
        import re
        self.calls.append({"model": model, "system": system, "user": user})
        if self.answers:
            a = self.answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a
        m = re.search(r"<recruiter_text>\n(.*)\n</recruiter_text>", user, re.S)
        return self.stub(m.group(1) if m else "")
