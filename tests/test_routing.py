"""Routing, failover and fail fast (specs/18 §2, §7): scripted providers, a fake clock, no network."""
import logging
import threading
import time

import pytest
from pydantic import BaseModel

from core import config, db, timing
from core.llm import DailyLimitReached, LLMError, RoutedLLM
from core.routing import ModelLimiter, Overloaded, Router, Throttled, Unusable, last_model, models_of, parse_duration

A, B, C = "gemini/gemini-3.5-flash-lite", "gemini/gemini-3.1-flash-lite", "groq/openai/gpt-oss-120b"


class Out(BaseModel):
    label: str


class Script:
    """One fake provider per model: a list of outcomes (an Out, an exception, or a callable)."""

    def __init__(self, **by_model):
        self.by_model = {k.replace("__", "/"): list(v) for k, v in by_model.items()}
        self.calls: list[str] = []
        self._lock = threading.Lock()

    def client_for(self, model):
        script = self

        class Client:
            def parse(self, *, model, system, user, output_type):
                with script._lock:
                    script.calls.append(model)
                    queue = script.by_model.get(model) or [Out(label=model)]
                    step = queue.pop(0) if len(queue) > 1 else queue[0]
                if callable(step) and not isinstance(step, BaseModel):
                    step = step()
                if isinstance(step, BaseException):
                    raise step
                return step
        return Client()


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "r.db")
    db.init_app_db(path).close()
    monkeypatch.setitem(config.ROUTES, "judge_single", [A, B, C])
    clock, slept = Clock(), []

    def sleep(s):
        slept.append(s)
        clock.t += s
    lim = ModelLimiter(path, clock=clock)
    return type("Env", (), {"path": path, "clock": clock, "slept": slept, "lim": lim, "sleep": sleep})


def router(env, script, **kw):
    return Router(script.client_for, limiter=env.lim, sleep=env.sleep, clock=env.clock, **kw)


def ask(r, model="route:judge_single", user="U"):
    return r.parse(model=model, system="S", user=user, output_type=Out)


# --- Order and overflow ---------------------------------------------------------------------------------------------

def test_the_first_model_answers_and_the_others_are_overflow_only(env):
    s = Script()
    r = router(env, s)
    for _ in range(10):
        assert ask(r).label == A
    assert set(s.calls) == {A} and last_model() == A


def test_a_busy_first_model_overflows_to_the_next_at_once(env, monkeypatch):
    monkeypatch.setitem(config.MODEL_LIMITS, A, config.ModelLimits(2, 10**9, 500))
    s = Script()
    r = router(env, s)
    assert [ask(r).label for _ in range(4)] == [A, A, B, B]           # A's 2 per minute used: B takes over
    assert env.slept == []
    env.clock.t += 61                                                  # a minute later A is first again
    assert ask(r).label == A


# --- Failover --------------------------------------------------------------------------------------------------------

def test_429_503_and_timeouts_fail_over_immediately_without_sleeping(env, caplog):
    caplog.set_level(logging.WARNING, logger="rightstaff.routing")
    s = Script(**{A: [Throttled("A: HTTP 429", retry_after=30)], B: [Overloaded("B: HTTP 503")]})
    rec = timing.Recorder()
    with timing.recording(rec):
        assert ask(router(env, s)).label == C
    assert s.calls == [A, B, C] and env.slept == []
    assert rec.summary()["failovers"] == 2
    assert rec.summary()["calls_by_task"]["judge_single"] == {A: 1, B: 1, C: 1}
    lines = [r.getMessage() for r in caplog.records]
    assert lines[0] == f"failover: judge_single {A} → {B} (limit, retry after 30 s)"
    assert lines[1] == f"failover: judge_single {B} → {C} (overloaded (B: HTTP 503))"
    assert env.lim.used_today(B)[0] == 0                               # an overloaded request does not count today


def test_a_throttled_model_rests_until_the_stated_reset(env):
    s = Script(**{A: [Throttled("429", retry_after=20), Out(label="A again")]})
    r = router(env, s)
    assert ask(r).label == B
    assert ask(r).label == B and s.calls.count(A) == 1                 # still resting: not asked again
    env.clock.t += 21
    assert ask(r).label == "A again"


def test_at_most_four_attempts_per_call(env, monkeypatch):
    monkeypatch.setattr(config, "ROUTE_OVERLOAD_RETRY_ROUNDS", 0)
    monkeypatch.setitem(config.ROUTES, "judge_single", [A, B, C, "groq/x", "groq/y"])
    bad = [Overloaded("busy")]
    s = Script(**{A: bad, B: bad, C: bad, "groq__x": bad, "groq__y": [Out(label="never")]})
    with pytest.raises(LLMError, match="after 4 attempt"):
        ask(router(env, s))
    assert len(s.calls) == 4


def test_when_every_model_is_overloaded_the_route_tries_once_more_after_their_rest(env, caplog):
    """A 503 storm (every model of a route at once) usually passes in seconds: one more round once the models have
    rested (OVERLOAD_COOLDOWN_S), instead of telling the candidate to come back later."""
    caplog.set_level(logging.WARNING, logger="rightstaff.routing")
    s = Script(**{A: [Overloaded("A: HTTP 503"), Out(label="A after rest")], B: [Overloaded("B: HTTP 503")],
                  C: [Overloaded("C: HTTP 503")]})
    assert ask(router(env, s)).label == "A after rest"
    assert s.calls == [A, B, C, A]
    assert config.OVERLOAD_COOLDOWN_S - 1 <= sum(env.slept) <= config.ROUTE_MAX_TOTAL_WAIT_S
    assert any(r.getMessage().startswith("retry: judge_single: every model was overloaded") for r in caplog.records)


def test_the_overload_round_happens_once_and_never_after_a_rate_limit(env):
    always = [Overloaded("HTTP 503")]
    s = Script(**{A: always, B: always, C: always})
    with pytest.raises(LLMError):
        ask(router(env, s))
    assert len(s.calls) == 6                                           # two rounds of three, then the busy message
    s2 = Script(**{A: [Throttled("A: HTTP 429", retry_after=30)], B: always, C: always})
    with pytest.raises(LLMError):
        ask(router(env, s2))
    assert s2.calls == [A, B, C]                                       # a rate limit is not a passing overload


def test_invalid_output_moves_on_and_an_unusable_model_is_skipped(env):
    s = Script(**{A: [LLMError("output failed validation twice")], B: [Unusable("HTTP 404 model not found")]})
    assert ask(router(env, s)).label == C


# --- Waiting and failing fast ---------------------------------------------------------------------------------------

def test_every_model_busy_waits_at_most_five_seconds_then_proceeds(env, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="rightstaff.routing")
    for m in (A, B, C):
        monkeypatch.setitem(config.MODEL_LIMITS, m, config.ModelLimits(1, 10**9, 500))
    s = Script()
    r = router(env, s)
    assert [ask(r).label for _ in range(3)] == [A, B, C]
    assert ask(r).label == A                                           # all three used this minute: wait, then A
    assert env.slept and all(x <= config.ROUTE_MAX_WAIT_S + 0.25 for x in env.slept)
    assert any(m.getMessage().startswith("all busy: judge_single waits") for m in caplog.records)


def test_every_model_exhausted_for_the_day_fails_fast_without_calling(env, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger="rightstaff.routing")
    for m in (A, B, C):
        monkeypatch.setitem(config.MODEL_LIMITS, m, config.ModelLimits(100, 10**9, 2))
    s = Script()
    r = router(env, s)
    for _ in range(6):
        ask(r)
    calls = len(s.calls)
    with pytest.raises(DailyLimitReached, match=r"Daily free-tier limit reached for every model that can do this step \(scoring\)") as e:
        ask(r)
    assert "gemini" not in str(e.value) and "groq" not in str(e.value)               # no model names for people
    assert len(s.calls) == calls and env.slept == []                   # no call, no wait
    assert any(m.getMessage().startswith("fail fast: judge_single: every model is exhausted") for m in caplog.records)


def test_the_providers_daily_limit_wins_over_our_counters_and_survives_a_restart(env):
    s = Script(**{A: [Throttled("A: HTTP 429 (per day)", daily=True)]})
    assert ask(router(env, s)).label == B                              # Google says the day is over (we counted 1)
    fresh = ModelLimiter(env.path, clock=env.clock)                    # a restarted server, same database
    assert fresh.check(A, 10)[0] == "exhausted"
    s2 = Script()
    assert Router(s2.client_for, limiter=fresh, sleep=env.sleep, clock=env.clock).parse(
        model="route:judge_single", system="S", user="U", output_type=Out).label == B and A not in s2.calls


def test_groq_headers_mark_a_model_exhausted_until_the_reset(env):
    env.lim.observe_headers(C, {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "2m59.56s"})
    assert env.lim.check(C, 10)[0] == "exhausted"
    assert parse_duration("2m59.56s") == pytest.approx(179.56) and parse_duration("7.66s") == pytest.approx(7.66)
    assert parse_duration("350ms") == pytest.approx(0.35) and parse_duration(None) is None


def test_a_request_too_big_for_a_model_skips_it(env, monkeypatch):
    monkeypatch.setitem(config.ROUTES, "extract_web", [C, A])                    # groq first, 8K tokens per minute
    s = Script()
    out = router(env, s, max_tokens=2000).parse(model="route:extract_web", system="S", user="x" * 30_000,
                                                output_type=Out)
    assert out.label == A and C not in s.calls


def test_concurrency_per_model_up_to_its_limit(env, monkeypatch):
    monkeypatch.setattr(config, "_CONCURRENCY", {A: 1})
    gate, started = threading.Event(), threading.Event()

    def slow():
        started.set()
        gate.wait(5)
        return Out(label="A slow")
    s = Script(**{A: [slow]})
    r = Router(s.client_for, limiter=env.lim, sleep=time.sleep, clock=env.clock)
    results = []
    t = threading.Thread(target=lambda: results.append(ask(r).label))
    t.start()
    started.wait(5)
    assert ask(r).label == B                                           # A's one slot is taken
    gate.set()
    t.join(5)
    assert results == ["A slow"]


# --- Routes in config ---------------------------------------------------------------------------------------------

def test_a_claude_model_at_the_top_of_a_route_works_with_no_code_change(env, monkeypatch):
    monkeypatch.setitem(config.ROUTES, "plan", ["claude-sonnet-5-5", "gemini/gemini-3.8-flash"])
    s = Script()
    assert ask(router(env, s), "route:plan").label == "claude-sonnet-5-5"
    assert models_of("claude-haiku-4-5-20251001") == ["claude-haiku-4-5-20251001"]


def test_a_single_model_is_a_one_model_route(env):
    s = Script(**{A: [Overloaded("busy")]})
    with pytest.raises(LLMError):
        ask(router(env, s), A)
    assert s.calls == [A, A] and sum(env.slept) >= config.OVERLOAD_COOLDOWN_S - 1   # once more after its rest


# --- LIVE guard ---------------------------------------------------------------------------------------------------

def test_real_providers_need_live(monkeypatch):
    from core.llm import ClaudeLLM, GeminiLLM, GroqLLM
    from judges.jev_judge import JevError, JevJudge
    monkeypatch.setattr(config, "LIVE", False)
    for client in (GeminiLLM(api_key="k"), GroqLLM(api_key="k")):
        with pytest.raises(LLMError, match="LIVE=1"):
            client._call("m", "S", "U", Out)
    with pytest.raises(LLMError, match="LIVE=1"):
        ClaudeLLM()._call("claude-haiku-4-5-20251001", "S", "U", Out)
    with pytest.raises(JevError, match="LIVE=1"):
        JevJudge(fallback=None, api_key="k")._post({})


def test_routed_llm_records_the_answering_model_for_extraction(env, tmp_path, monkeypatch):
    from extraction import extract as ex
    from extraction import schemas
    from extraction.validate import SourceDoc
    monkeypatch.setattr(config, "EXTRACT_MODEL", "route:judge_single")
    s = Script(**{A: [Throttled("429", retry_after=5)], B: [schemas.ProfileExtract(roles=[], projects=[], claims=[], certifications=[])]})
    llm = RoutedLLM(router=router(env, s))
    conn = db.connect(env.path)
    cache = ex.ExtractionCache(conn)
    out = ex.extract_all([SourceDoc("e1", "resume", "Jane Doe resume text", "Resume")], llm, cache)
    assert not isinstance(out["e1"], Exception)
    assert conn.execute("SELECT model FROM extraction_cache").fetchone()[0] == B


# --- Answer budgets per task and the bad-output rule (spec 18 Phase 1 review) --------------------------------------

def _gemini_router(env, *responses):
    from core.llm import GeminiLLM
    from tests.test_gemini import Session
    session = Session(*responses)
    client = GeminiLLM(session=session, api_key="k")
    return RoutedLLM(router=Router(lambda m: client, limiter=env.lim, sleep=env.sleep, clock=env.clock)), session


def test_answer_budget_per_task(env, monkeypatch):
    from judges.claude_judge import ClaudeJudge
    from tests.test_gemini import ok
    monkeypatch.setattr(config, "JUDGE_FALLBACK_MODEL", A)
    monkeypatch.setattr(config, "COMPLEXITY_MODEL", A)
    single = {"label": "yes", "quote": "", "reasoning": "r"}
    llm, session = _gemini_router(env, ok(single), ok({"label": "meets", "quote": "", "reasoning": "r"}),
                                  ok({"answers": [{"criterion": f"C{i}", "label": "keyword", "quote": "", "reasoning": ""}
                                                  for i in range(1, 6)]}))
    judge = ClaudeJudge(llm=llm)
    judge.yes_no("outcome_metric", {"bullet": "Cut churn 12%."})
    judge.choice("complexity", ["meets", "partly", "no"], {"item": "x"})
    judge.batch("skill_evidence", [{"skills_list": "Python, SQL", "criterion": f"c{i}"} for i in range(5)])
    budgets = [b["generationConfig"]["maxOutputTokens"] for b in session.bodies]
    assert budgets == [1024, 2048, 512 + 5 * 192]
    assert config.grouped_budget(40) == 4096                                       # capped


def test_web_pages_and_documents_use_their_task_budgets(env, monkeypatch):
    from extraction import extract as ex
    from extraction.validate import SourceDoc
    from tests.test_gemini import ok
    empty_profile = {"roles": [], "projects": [], "claims": [], "certifications": []}
    llm, session = _gemini_router(env, ok({"projects": [], "claims": []}), ok(empty_profile))
    monkeypatch.setattr(config, "EXTRACT_WEB_MODEL", A)
    monkeypatch.setattr(config, "EXTRACT_MODEL", A)
    ex.extract_document(SourceDoc("e1", "github_repo", "readme text", "README"), llm)
    ex.extract_document(SourceDoc("e2", "resume", "resume text", "Resume"), llm)
    assert [b["generationConfig"]["maxOutputTokens"] for b in session.bodies] == [
        config.TASK_MAX_TOKENS["extract_web"], config.EXTRACT_MAX_TOKENS]


def test_groq_budget_is_the_task_budget_within_its_token_limit():
    from core.llm import GroqLLM
    from core.routing import answer_budget
    g = GroqLLM(session=object(), api_key="k", max_tokens=12000)
    with answer_budget(1024):
        assert g._budget("groq/openai/gpt-oss-120b", "S", "U") == 1024
    assert g._budget("groq/openai/gpt-oss-120b", "S" * 300, "U" * 3000) == 8000 - (3300 // 3 + 200)


def test_an_invalid_or_cut_off_answer_is_retried_once_on_the_next_model_then_missing(env):
    s = Script(**{A: [LLMError("A output was cut off at max_tokens")], B: [LLMError("output failed validation")],
                  C: [Out(label="never")]})
    with pytest.raises(LLMError, match="no valid answer from two models"):
        ask(router(env, s))
    assert s.calls == [A, B]                                                       # C not asked
    s2 = Script(**{A: [LLMError("cut off")]})
    assert ask(router(env, s2)).label == B                                         # once on the next model


def test_a_routed_call_makes_one_attempt_per_model(env):
    from tests.test_gemini import ok
    invalid = ok({"nope": 1})
    llm, session = _gemini_router(env, invalid, invalid)
    with pytest.raises(LLMError, match="no valid answer from two models"):
        llm.parse(model="route:judge_single", system="S", user="U", output_type=Out)
    assert len(session.bodies) == 2                                               # A once, B once (no same-model retry)


# --- Fixes after live run 1 -----------------------------------------------------------------------------------------

def test_document_reading_gets_a_longer_timeout_than_a_question(env, monkeypatch):
    from core.llm import GeminiLLM
    from tests.test_gemini import Session, ok
    session = Session(ok({"label": "x"}), ok({"label": "y"}))
    client = GeminiLLM(session=session, api_key="k")
    r = Router(lambda m: client, limiter=env.lim, sleep=env.sleep, clock=env.clock)
    monkeypatch.setitem(config.ROUTES, "extract_document", [A])
    r.parse(model="route:extract_document", system="S", user="U", output_type=Out)
    r.parse(model="route:judge_single", system="S", user="U", output_type=Out)
    assert session.timeouts == [(10, 180.0), (10, config.GEMINI_TIMEOUT_S)]
    assert "gemini/gemini-2.5-flash" not in config.ROUTES["extract_document"]


def test_a_model_that_rejects_the_request_rests_instead_of_costing_every_call_an_attempt(env):
    s = Script(**{A: [Unusable("HTTP 400: Thinking level is not supported for this model."), Out(label=A)]})
    r = router(env, s)
    assert ask(r).label == B and ask(r).label == B
    assert s.calls.count(A) == 1                                       # not asked again for an hour
    env.clock.t += config.UNUSABLE_REST_S + 1
    assert ask(r).label == A                                           # rested: back in use


def test_gemini_usage_rows_keep_the_bare_model_name(env):
    s = Script()
    ask(router(env, s))
    conn = db.connect(env.path)
    assert conn.execute("SELECT model FROM provider_usage").fetchone()[0] == "gemini-3.5-flash-lite"


def test_candidates_never_see_model_or_provider_details():
    from backend.candidate import BUSY_TEXT, public_error
    assert public_error("extract_document: no model could answer after 4 attempt(s): gemini/gemini-3-flash-preview: "
                        "HTTP 503") == BUSY_TEXT
    assert public_error("extract_web: no valid answer from two models (gemini/gemini-3.5-flash-lite output was cut "
                        "off at max_tokens)") == BUSY_TEXT
    scanned = "This PDF has no text layer (it looks like a scan). Upload a text PDF or a DOCX."
    assert public_error(scanned) == scanned
    daily = "Daily free-tier limit reached for every model that can do this step (reading documents). Try later."
    assert public_error(daily) == daily
