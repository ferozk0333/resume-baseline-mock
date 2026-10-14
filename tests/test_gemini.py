"""Gemini provider: request shape, limits, daily quota, retries (offline, fake HTTP)."""
import json

import pytest

from core import config, db
from core.llm import (DailyLimitReached, GeminiLLM, LLMError, RateLimiter, RoutedLLM, gemini_schema)
from extraction import schemas
from tests.helpers import FakeResponse


class Session:
    def __init__(self, *responses):
        self.responses, self.bodies, self.urls = list(responses), [], []

    def post(self, url, json=None, headers=None, timeout=None):
        self.urls.append(url)
        self.bodies.append(json)
        status, payload = self.responses.pop(0)
        return FakeResponse(status, payload)


def ok(content, finish="STOP"):
    text = content if isinstance(content, str) else json.dumps(content)
    return 200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
                 "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 40, "thoughtsTokenCount": 10}}


def quota(per_day: bool, delay="7s"):
    quota_id = "GenerateRequestsPerDayPerProjectPerModel-FreeTier" if per_day else \
        "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"
    return 429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}]}}


@pytest.fixture
def limiter(tmp_path):
    path = str(tmp_path / "g.db")
    db.init_app_db(path).close()
    return RateLimiter("gemini", rpm=100, tpm=10**9, rpd=5, db_path=path, sleep=lambda s: None)


def llm(session, limiter, slept=None):
    return GeminiLLM(session=session, api_key="k", limiter=limiter, sleep=(slept.append if slept is not None
                                                                          else lambda s: None))


EMPTY = {"projects": [], "claims": []}


def test_request_keeps_instructions_in_system_and_uses_an_inlined_schema(limiter):
    s = Session(ok(EMPTY))
    out = llm(s, limiter).parse(model="gemini/gemini-3.8-flash", system="RULES", user="<resume>doc</resume>",
                                output_type=schemas.PortfolioExtract)
    assert out.projects == []
    body = s.bodies[0]
    assert s.urls[0].endswith("/models/gemini-3.8-flash:generateContent")
    assert body["systemInstruction"]["parts"][0]["text"] == "RULES"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "<resume>doc</resume>"}]}]
    gen = body["generationConfig"]
    assert gen["responseMimeType"] == "application/json" and gen["temperature"] == 0
    assert "$ref" not in json.dumps(gen["responseJsonSchema"]) and "$defs" not in gen["responseJsonSchema"]


def test_per_minute_429_waits_retry_delay_then_succeeds(limiter):
    slept = []
    s = Session(quota(per_day=False, delay="7s"), ok(EMPTY))
    llm(s, limiter, slept).parse(model="gemini/gemini-3.5-flash-lite", system="S", user="U",
                                 output_type=schemas.PortfolioExtract)
    assert slept == [7.0] and len(s.bodies) == 2


def test_daily_429_stops_with_a_clear_message_and_later_calls_do_not_hit_the_api(limiter):
    s = Session(quota(per_day=True))
    with pytest.raises(DailyLimitReached, match="run again after midnight Pacific"):
        llm(s, limiter).parse(model="gemini/gemini-3.8-flash", system="S", user="U",
                              output_type=schemas.PortfolioExtract)
    with pytest.raises(DailyLimitReached):
        llm(Session(), limiter).parse(model="gemini/gemini-3.8-flash", system="S", user="U",
                                      output_type=schemas.PortfolioExtract)
    assert isinstance(DailyLimitReached("x"), LLMError)


def test_requests_per_day_are_counted_across_restarts(limiter, tmp_path):
    for _ in range(5):
        llm(Session(ok(EMPTY)), limiter).parse(model="gemini/m", system="S", user="U",
                                               output_type=schemas.PortfolioExtract)
    fresh = RateLimiter("gemini", rpm=100, tpm=10**9, rpd=5, db_path=limiter.db_path)   # new process, same DB
    with pytest.raises(DailyLimitReached):
        fresh.acquire("m", 10)


def test_requests_per_minute_are_spread_out():
    clock = [0.0]
    waits = []
    lim = RateLimiter("gemini", rpm=2, tpm=10**9, rpd=10**6, clock=lambda: clock[0],
                      sleep=lambda s: (waits.append(s), clock.__setitem__(0, clock[0] + s)))
    lim._record = lambda model, tokens: None
    lim.used_today = lambda model: 0
    for _ in range(3):
        lim.acquire("m", 1)
    assert len(waits) == 1 and 59 < waits[0] <= 60.1


def test_server_errors_back_off_and_cut_off_output_is_an_error(limiter):
    slept = []
    llm(Session((503, {"error": {"message": "high demand"}}), ok(EMPTY)), limiter, slept).parse(
        model="gemini/m", system="S", user="U", output_type=schemas.PortfolioExtract)
    assert slept == [2]
    with pytest.raises(LLMError, match="cut off"):
        llm(Session(ok('{"projects": [', finish="MAX_TOKENS")), limiter).parse(
            model="gemini/m", system="S", user="U", output_type=schemas.PortfolioExtract)


def test_runaway_output_is_retried_then_reported(limiter):
    from core.llm import RUNAWAY_MESSAGE, RunawayOutput
    runaway = '{"projects": [{"name": "' + "\\t" * 300
    with pytest.raises(RunawayOutput, match=RUNAWAY_MESSAGE):
        llm(Session(ok(runaway), ok(runaway)), limiter).parse(model="gemini/m", system="S", user="U",
                                                              output_type=schemas.PortfolioExtract)


def test_schema_inlines_definitions():
    out = gemini_schema(schemas.ProfileExtract.model_json_schema())
    text = json.dumps(out)
    assert "$ref" not in text and "$defs" not in text and '"default"' not in text
    role = out["properties"]["roles"]["items"]
    assert "title" in role["properties"] and "title" in role["required"]        # a field named "title" survives
    assert role["properties"]["bullets"]["items"]["properties"]["text"]


def test_provider_setting_maps_every_claude_role_to_gemini(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setenv("EXTRACT_MODEL", "claude-haiku-4-5-20251001")            # left over in .env: replaced
    monkeypatch.setenv("JUDGE_FALLBACK_MODEL", "groq/openai/gpt-oss-20b")        # explicit other provider: kept
    assert config._role_model("EXTRACT_MODEL", "x", "extract") == f"gemini/{config.GEMINI_MODELS['extract']}"
    assert config._role_model("JUDGE_FALLBACK_MODEL", "x", "judge") == "groq/openai/gpt-oss-20b"
    monkeypatch.setattr(config, "LLM_PROVIDER", "claude")
    assert config._role_model("EXTRACT_MODEL", "x", "extract") == "claude-haiku-4-5-20251001"


def test_router_and_judgment_labels():
    from judges.claude_judge import method_of
    assert isinstance(RoutedLLM()._for("gemini/gemini-3.8-flash"), GeminiLLM)
    assert method_of("gemini/gemini-3.8-flash") == "gemini"


def test_daily_limit_in_a_run_is_reported_as_paused(tmp_path, monkeypatch):
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.base import JudgeError
    from judges.fake_judge import FakeJudge
    from tests.fixtures.archetypes import ARCHETYPES, TODAY
    from core.llm import daily_limit_message
    path = str(tmp_path / "p.db")
    conn = db.init_app_db(path)
    cid, judge = ARCHETYPES["strong_ds"](conn)
    base = judge.script
    judge.script = lambda q, c: JudgeError(daily_limit_message("gemini-3.8-flash")) if q == "complexity" else base(q, c)
    res = Pipeline(Deps(path, judge, today=TODAY)).score(cid, "baseline-data-scientist-entry")
    assert res["paused"].startswith("Daily free-tier limit for gemini-3.8-flash reached")
