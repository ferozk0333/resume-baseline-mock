"""Gemini client: request shape and one attempt per call (specs/18): limits, overloads and timeouts are raised for
the router, never slept on. Offline, fake HTTP."""
import json

import pytest

from core import config, db
from providers.llm import DailyLimitReached, GeminiLLM, LLMError, RoutedLLM, gemini_schema
from providers.routing import ModelLimiter, Overloaded, Router, Throttled, Unusable
from extraction import schemas
from tests.helpers import FakeResponse


class Session:
    def __init__(self, *responses):
        self.responses, self.bodies, self.urls, self.timeouts = list(responses), [], [], []

    def post(self, url, json=None, headers=None, timeout=None):
        self.urls.append(url)
        self.bodies.append(json)
        self.timeouts.append(timeout)
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


EMPTY = {"projects": [], "claims": []}


def call(session, model="gemini/gemini-3.8-flash"):
    return GeminiLLM(session=session, api_key="k").parse(model=model, system="RULES", user="<resume>doc</resume>",
                                                       output_type=schemas.PortfolioExtract)


def test_request_keeps_instructions_in_system_uses_an_inlined_schema_and_a_short_timeout():
    s = Session(ok(EMPTY))
    assert call(s).projects == []
    body = s.bodies[0]
    assert s.urls[0].endswith("/models/gemini-3.8-flash:generateContent")
    assert body["systemInstruction"]["parts"][0]["text"] == "RULES"
    assert body["contents"] == [{"role": "user", "parts": [{"text": "<resume>doc</resume>"}]}]
    assert body["generationConfig"]["temperature"] == 0
    assert s.timeouts[0] == (10, config.GEMINI_TIMEOUT_S) == (10, 45.0)


def test_limits_overloads_and_timeouts_are_raised_not_slept_on():
    import requests
    with pytest.raises(Throttled) as e:
        call(Session(quota(per_day=False, delay="7s")))
    assert e.value.retry_after == 7.0 and not e.value.daily
    with pytest.raises(Throttled) as e:
        call(Session(quota(per_day=True)))
    assert e.value.daily
    with pytest.raises(Overloaded):
        call(Session((503, {"error": {"message": "high demand"}})))

    class Slow(Session):
        def post(self, *a, **k):
            raise requests.ReadTimeout("slow")
    with pytest.raises(Overloaded, match="no answer"):
        call(Slow())
    with pytest.raises(Unusable):
        call(Session((404, {"error": {"message": "model not found"}})))


def test_cut_off_and_runaway_output_are_errors():
    with pytest.raises(LLMError, match="cut off"):
        call(Session(ok(EMPTY, finish="MAX_TOKENS")))
    looping = '{"projects": [], "claims": [' + "\\t" * 400
    with pytest.raises(LLMError, match="repeating"):
        call(Session(ok(looping), ok(looping)))


def test_schema_inlines_definitions():
    out = gemini_schema(schemas.ProfileExtract.model_json_schema())
    text = json.dumps(out)
    assert "$ref" not in text and "$defs" not in text and '"default"' not in text
    role = out["properties"]["roles"]["items"]
    assert "title" in role["properties"] and "title" in role["required"]
    assert role["properties"]["bullets"]["items"]["properties"]["text"]


def test_provider_setting_maps_every_claude_role_to_its_route(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setenv("EXTRACT_MODEL", "claude-haiku-4-5-20251001")            # left over in .env: replaced
    monkeypatch.setenv("JUDGE_FALLBACK_MODEL", "groq/openai/gpt-oss-20b")        # explicit other provider: kept
    assert config._role_model("EXTRACT_MODEL", "x", "extract") == "route:extract_document"
    assert config._role_model("JUDGE_FALLBACK_MODEL", "x", "judge") == "groq/openai/gpt-oss-20b"
    for role, task in (("plan", "plan"), ("complexity", "judge_single"), ("judge_batch", "judge_grouped"),
                       ("extract_web", "extract_web"), ("email", "judge_single")):
        assert config._role_model("NOT_SET", "x", role) == f"route:{task}"
    monkeypatch.setattr(config, "LLM_PROVIDER", "claude")
    assert config._role_model("EXTRACT_MODEL", "x", "extract") == "claude-haiku-4-5-20251001"


def test_default_routes_and_limits_match_the_spec():
    assert config.ROUTES["extract_document"][0] == "gemini/gemini-3.8-flash"
    assert config.ROUTES["judge_single"][:3] == ["gemini/gemini-3.5-flash-lite", "gemini/gemini-3.1-flash-lite",
                                                "groq/openai/gpt-oss-120b"]
    assert config.ROUTES["judge_grouped"] == ["gemini/gemini-3.5-flash-lite", "gemini/gemini-3.1-flash-lite",
                                             "gemini/gemma-4-31b-it"]
    assert config.model_limits("gemini/gemini-3.8-flash") == config.ModelLimits(5, 250000, 20, 0)
    assert config.model_limits("gemini/gemini-3.5-flash-lite") == config.ModelLimits(15, 250000, 500, 0)
    assert config.model_limits("groq/openai/gpt-oss-120b") == config.ModelLimits(30, 8000, 1000, 200000)
    assert config.model_concurrency("gemini/gemini-3.5-flash-lite") == 15                  # up to its RPM, not 4
    assert config.GROQ_TIMEOUT_S == 20


def test_router_and_judgment_labels():
    from judges.claude_judge import method_of
    assert isinstance(RoutedLLM()._for("gemini/gemini-3.8-flash"), GeminiLLM)
    assert method_of("gemini/gemini-3.8-flash") == "gemini"


def test_a_gemini_judge_answer_is_a_valid_judgment_end_to_end(tmp_path, monkeypatch):
    """Regression: the 'gemini' method label must be accepted by Judgment (a live run failed on it)."""
    from judges.claude_judge import ClaudeJudge
    path = str(tmp_path / "g.db")
    db.init_app_db(path).close()
    monkeypatch.setattr(config, "JUDGE_FALLBACK_MODEL", "gemini/gemini-3.5-flash-lite")
    answer = {"label": "yes", "quote": "cut churn 12%", "reasoning": "A number is stated."}
    client = GeminiLLM(session=Session(ok(answer)), api_key="k")
    llm = RoutedLLM(router=Router(lambda m: client, limiter=ModelLimiter(path), sleep=lambda s: None))
    j = ClaudeJudge(llm=llm).yes_no("outcome_metric", {"bullet": "Built a model that cut churn 12%."})
    assert (j.label, j.method, j.model_version, j.quote) == ("yes", "gemini", "gemini/gemini-3.5-flash-lite",
                                                            "cut churn 12%")


def test_daily_limit_in_a_run_is_reported_as_paused(tmp_path):
    from graph.context import Deps
    from graph.pipeline import Pipeline
    from judges.base import JudgeError
    from tests.fixtures.archetypes import ARCHETYPES, TODAY
    from providers.routing import daily_message
    import time
    path = str(tmp_path / "p.db")
    conn = db.init_app_db(path)
    cid, judge = ARCHETYPES["strong_ds"](conn)
    base = judge.script
    msg = daily_message("judge_single", ["gemini/gemini-3.5-flash-lite"], time.time())
    judge.script = lambda q, c: JudgeError(msg) if q == "complexity" else base(q, c)
    res = Pipeline(Deps(path, judge, today=TODAY)).score(cid, "baseline-data-scientist-entry")
    assert res["paused"].startswith("Daily free-tier limit reached for every model")
    assert isinstance(DailyLimitReached("x"), LLMError)


def test_timing_summary_of_a_run_that_stopped_early():
    from core import timing
    rec = timing.Recorder()
    rec.add(timing.STEP, "prepare_match", 0.0, 1.0)
    assert rec.summary()["scoring_and_plan_seconds"] is None


def test_requests_send_only_what_the_model_supports(monkeypatch):
    """Phase 3 review 2c: Gemma rejects a thinking level; nothing else changes (same prompt, schema, temperature)."""
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "low")
    s = Session(ok(EMPTY), ok(EMPTY))
    call(s, "gemini/gemini-3.8-flash")
    call(s, "gemini/gemma-4-31b-it")
    flash, gemma = (b["generationConfig"] for b in s.bodies)
    assert flash["thinkingConfig"] == {"thinkingLevel": "low"} and "thinkingConfig" not in gemma
    assert gemma["temperature"] == 0 and gemma["responseJsonSchema"] == flash["responseJsonSchema"]
    assert s.bodies[1]["systemInstruction"] == s.bodies[0]["systemInstruction"]
    assert config.model_caps("claude-sonnet-5-5").temperature is False
    assert config.model_caps("gemini/gemini-3.5-flash-lite") == config.ModelCaps()
