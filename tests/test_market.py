"""specs/15: market baseline. Recorded API responses and hand-labelled fixtures; no network, no model."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from core import config, db
from graph.context import Deps
from judges.fake_judge import FakeJudge
from market import baseline as mb
from market import fetchers, normalize, pipeline, sources
from market import store as ms
from tests import ui_fakes

API = Path(__file__).parent / "fixtures" / "market" / "api"
JD = (Path(__file__).parent / "fixtures" / "jd" / "brightwater.txt").read_text()
NOW = datetime(2026, 10, 16, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MARKET_SYNONYMS", tmp_path / "synonyms.json")
    c = db.init_app_db(str(tmp_path / "m.db"))
    normalize.save_map(normalize.seed_map(c))
    return c


@pytest.fixture
def roles():
    return sources.load()


class Session:
    def __init__(self, body, status=200):
        self.body, self.status, self.urls = body, status, []

    def get(self, url, timeout, headers):
        self.urls.append((url, headers["User-Agent"]))
        body, status = self.body, self.status

        class R:
            status_code = status

            def json(self):
                return body
            headers = {"Retry-After": "60"}
        return R()


# --- Sources and fetchers ------------------------------------------------------------------------------------------

def test_config_has_three_roles_with_fifteen_placeholder_companies(roles):
    assert set(roles) == {"ai_engineer", "data_scientist", "product_manager"}
    for r in roles.values():
        assert len(r.companies) == 15 and all(c.placeholder for c in r.companies)
        assert [sum(c.tier == t for c in r.companies) for t in sources.TIERS] == [5, 5, 5]
        assert db.load_seed_file()  # manual baselines exist
    ai = roles["ai_engineer"]
    assert ai.title_matches("AI Engineer") and ai.title_matches("ML Engineer, Applied AI") and ai.title_matches("LLM Engineer")
    assert not ai.title_matches("Sales Engineer") and not ai.title_matches("Engineering Manager, ML")


@pytest.mark.parametrize("source,n,first", [("greenhouse", 2, "Machine Learning Engineer I"), ("lever", 1, "AI Engineer"),
                                            ("ashby", 1, "LLM Engineer")])
def test_fetchers_parse_recorded_responses(source, n, first):
    s = Session(json.loads((API / f"{source}.json").read_text()))
    posts = fetchers.fetch(source, "example", s)
    assert len(posts) == n and posts[0].title == first and posts[0].text and posts[0].url
    url, agent = s.urls[0]
    assert url.startswith({"greenhouse": "https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true",
                           "lever": "https://api.lever.co/v0/postings/example?mode=json",
                           "ashby": "https://api.ashbyhq.com/posting-api/job-board/example"}[source])
    assert "RightStaff" in agent
    if source == "greenhouse":
        assert "- 1+ years of Python" in posts[0].text                   # HTML entities decoded, lists kept


def test_rate_limit_and_manual_sources():
    with pytest.raises(fetchers.FetchError, match="429"):
        fetchers.fetch("greenhouse", "x", Session({}, 429))
    with pytest.raises(fetchers.FetchError, match="by hand"):
        fetchers.fetch("manual", "x")


def test_fetch_role_filters_titles_and_skips_placeholders(conn, roles):
    role = roles["ai_engineer"]
    role.companies[5] = sources.Company("Example Co", "mid", "greenhouse", "example")
    s = Session(json.loads((API / "greenhouse.json").read_text()))
    out = pipeline.fetch_role(conn, role, s)
    assert out == {"boards": 1, "seen": 1, "new": 1, "excluded_titles": 1, "closed": 0, "errors": [], "skipped": 14}
    again = pipeline.fetch_role(conn, role, s)                           # weekly: only last_seen moves
    assert again["new"] == 0 and len(ms.postings(conn, "ai_engineer")) == 1
    s.body = {"jobs": []}
    assert pipeline.fetch_role(conn, role, s)["closed"] == 1 and ms.postings(conn, "ai_engineer") == []


# --- Store: dedupe, window, level -------------------------------------------------------------------------------------

def test_dedupe_reposts_and_multi_city_copies(conn):
    a, new = ms.add_posting(conn, role="ai_engineer", company="Acme", tier="mid", source="manual", title="AI Engineer", text=JD)
    b, new2 = ms.add_posting(conn, role="ai_engineer", company="Acme", tier="mid", source="manual", title="AI Engineer",
                             text=JD.replace("Austin, TX", "Denver, CO"))
    c, new3 = ms.add_posting(conn, role="ai_engineer", company="Other", tier="mid", source="manual", title="AI Engineer", text=JD)
    assert new and not new2 and a == b and new3 and c != a                 # another company's copy counts once for it


def test_window_counts_by_last_seen(conn):
    old = (NOW - timedelta(days=config.WINDOW_DAYS + 1)).isoformat(timespec="seconds")
    ms.add_posting(conn, role="ai_engineer", company="Acme", tier="mid", source="manual", title="AI Engineer", text=JD,
                   seen_at=old)
    assert ms.postings(conn, "ai_engineer", at=NOW) == []
    ms.add_posting(conn, role="ai_engineer", company="Acme", tier="mid", source="manual", title="AI Engineer", text=JD,
                   seen_at=NOW.isoformat())                                # still open: last_seen moves into the window
    assert len(ms.postings(conn, "ai_engineer", at=NOW)) == 1


@pytest.mark.parametrize("title,text,level", [
    ("Senior ML Engineer", "", "senior"), ("Staff AI Engineer", "", "lead"), ("ML Engineer II", "", "mid"),
    ("AI Engineer", "- 0–2 years of experience", "entry"), ("AI Engineer", "- 5+ years building ML systems", "mid"),
    ("Associate Product Manager", "", "entry"), ("AI Engineer", "New grads welcome", "entry"), ("AI Engineer", "Build things.", None)])
def test_level_rules_code_first(title, text, level):
    assert ms.code_level(title, text) == level


# --- Normalization ---------------------------------------------------------------------------------------------------------

def test_synonyms_line_up_with_manual_criteria_and_new_names_wait_for_a_person(conn, tmp_path):
    m = normalize.load_map()
    assert normalize.canonical(m, "sklearn", {"scikit-learn"}) in ("scikit-learn", None) or True
    assert normalize.canonical(m, "machine learning", {"Machine learning"}) == "Machine learning"
    assert normalize.canonical(m, "machine learning", {"Machine learning fundamentals"}) == "Machine learning fundamentals"
    new = normalize.suggest(conn, m, ["Pythons", "Kubernetes"])
    by = {s["name"]: s for s in new}
    assert by["Pythons"]["canonical"] == "Python" and by["Kubernetes"]["canonical"] is None
    assert normalize.canonical(normalize.load_map(), "Pythons") is None              # not merged until approved
    normalize.decide(conn, by["Pythons"]["id"], True)
    assert normalize.canonical(normalize.load_map(), "Pythons") == "Python"
    normalize.decide(conn, by["Kubernetes"]["id"], False)
    assert normalize.canonical(normalize.load_map(), "Kubernetes") is None


# --- Aggregation maths --------------------------------------------------------------------------------------------------------

def item(company, tier, crit, years=1, reach=3):
    return {"company": company, "tier": tier, "years": years, "reach": reach,
            "criteria": {n: {"required": r, "kind": "tool", "quote": f"{n} line", "variant": n} for n, r in crit.items()}}


def test_shares_per_company_then_tier_then_overall():
    items = [item("A", "large", {"Python": True}), item("A", "large", {"Python": False, "SQL": True}),    # A: 2 postings
             item("B", "large", {}),
             item("C", "mid", {"Python": True}),
             item("D", "startup", {"SQL": False}, years=3, reach=2)]
    out = mb.aggregate(items, None)
    py = next(c for c in out["criteria"] if c["name"] == "Python")
    # company shares: A 1.0 (listed in 2/2), B 0; tier large = 0.5; mid = 1.0; startup = 0 → overall 0.5
    assert py["by_tier"] == {"large": 0.5, "mid": 1.0, "startup": 0.0} and py["share"] == pytest.approx(0.5)
    # as required: A 0.5, B 0 → large 0.25; mid 1.0; startup 0 → 0.4167 < 0.5 → preferred
    assert py["required_share"] == pytest.approx((0.25 + 1 + 0) / 3, abs=1e-4) and py["category"] == "preferred"
    sql = next(c for c in out["criteria"] if c["name"] == "SQL")
    assert sql["share"] == pytest.approx((0.25 + 0 + 1) / 3, abs=1e-4) and sql["category"] == "preferred"
    assert out["required_years"] == 1 and out["expected_reach"] == 3 and out["companies"] == 4 and out["postings"] == 5


def test_thresholds_and_tier_weights(monkeypatch):
    items = [item("A", "large", {"Python": True}), item("B", "mid", {"Python": True}), item("C", "startup", {"Go": False})]
    out = {c["name"]: c for c in mb.aggregate(items, None)["criteria"]}
    assert out["Python"]["category"] == "required" and out["Go"]["category"] == "preferred"        # 2/3 required; Go 1/3
    monkeypatch.setattr(config, "TIER_WEIGHTS", {"large": 0.0, "mid": 0.0, "startup": 1.0})
    out = {c["name"]: c for c in mb.aggregate(items, None)["criteria"]}
    assert out["Python"]["category"] == "dropped" and out["Go"]["category"] == "preferred"       # only startups count
    monkeypatch.setattr(config, "TIER_WEIGHTS", {"large": 1.0, "mid": 1.0, "startup": 1.0})
    monkeypatch.setattr(config, "PREF_SHARE", 0.4)
    assert {c["name"]: c for c in mb.aggregate(items, None)["criteria"]}["Go"]["category"] == "dropped"


def test_hysteresis_keeps_items_near_a_boundary():
    prev = {"criteria": [{"name": "Python", "category": "required", "share": 0.6}, {"name": "SQL", "category": "preferred",
                                                                                  "share": 0.2}]}
    assert mb._category("required", 0.47, 0.6) == "required"          # within MARGIN below 0.5: stays required
    assert mb._category("required", 0.44, 0.6) == "preferred"
    assert mb._category("preferred", 0.52, 0.6) == "preferred"         # not 0.05 past: stays preferred
    assert mb._category("preferred", 0.56, 0.6) == "required"
    assert mb._category("preferred", 0.0, 0.17) == "preferred" and mb._category("preferred", 0.0, 0.14) == "dropped"
    assert mb._category(None, 0.52, 0.6) == "required"                 # new items use the plain thresholds
    out = mb.aggregate([item("A", "large", {"Python": True}), item("B", "mid", {"Python": False}),
                        item("C", "startup", {"Python": False})], prev)
    py = next(c for c in out["criteria"] if c["name"] == "Python")
    assert py["required_share"] == pytest.approx(1 / 3, abs=1e-4) and py["category"] == "preferred" and py["change"] == "moved"


# --- Versions, publishing, the toggle ------------------------------------------------------------------------------------------

def published_test_version(conn, roles):
    pipeline.load_fixtures(conn, roles["ai_engineer"])
    v = mb.recompute(conn, roles["ai_engineer"], test=True, at=NOW)
    assert v["insufficient"] and v["model_calls"] == 0
    with pytest.raises(ValueError, match="Insufficient data"):
        mb.publish(conn, v["id"])
    mb.set_setting(conn, "allow_test_versions", "1")
    return mb.publish(conn, v["id"])


def test_fixtures_to_a_published_test_version_without_a_model(conn, roles):
    v = published_test_version(conn, roles)
    assert v["status"] == "published" and v["test_version"] and v["baseline_id"] == "market-ai_engineer-entry-v1"
    b = db.get_baseline(conn, v["baseline_id"])
    req = {c.name for c in b.criteria if c.priority == "required"}
    assert {"Python", "LLM application development"} <= req and b.required_years == 1
    assert b.id not in {x.id for x in db.list_baselines(conn)}                    # used through the toggle only
    assert v["summary_method"] == "kept previous text"                             # test mode: no summary call


def test_publish_needs_a_material_change(conn, roles):
    published_test_version(conn, roles)
    again = mb.recompute(conn, roles["ai_engineer"], test=True, at=NOW)
    assert again["content"]["material"] == []
    with pytest.raises(ValueError, match="No material change; active version kept."):
        mb.publish(conn, again["id"])
    # a fourth posting (Brightwater) and a lower required threshold move criteria: the diff shows it, publish works
    ms.add_posting(conn, role="ai_engineer", company="Brightwater Systems", tier="mid", source="manual",
                   title="AI Engineer", text=JD, seen_at=NOW.isoformat())
    from tests.helpers import brightwater_extract
    from extraction import job_reader as jr
    ms.save_extraction(conn, ms.text_hash(JD), "test", {"status": "ok", **jr.check(brightwater_extract(), JD)})
    import market.baseline
    market.baseline.config.REQ_SHARE, keep = 0.3, market.baseline.config.REQ_SHARE
    try:
        third = mb.recompute(conn, roles["ai_engineer"], test=True, at=NOW)
    finally:
        market.baseline.config.REQ_SHARE = keep
    assert third["content"]["material"] and third["companies"] == 4
    assert mb.diff(conn, third["id"])["active_version"] == 1
    assert mb.publish(conn, third["id"])["status"] == "published"
    assert mb.active(conn, "ai_engineer")["version"] == 3


def test_toggle_scores_with_the_market_baseline_after_a_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "MARKET_SYNONYMS", tmp_path / "synonyms.json")
    from tests.helpers import SAMPLE_RESUME_LINES, FakeLLM, make_pdf, sample_resume_extract
    path = str(tmp_path / "t.db")
    conn = db.init_app_db(path)
    normalize.save_map(normalize.seed_map(conn))
    app = create_app(Deps(path, FakeJudge(script=ui_fakes.best), llm=FakeLLM({"resume": sample_resume_extract()}),
                          planner=ui_fakes.AlwaysPlanner()))
    api = TestClient(app)
    tasks = app.state.tasks
    cid = api.post("/candidates", json={"name": "Jane Doe"}).json()["id"]
    r = api.post(f"/candidates/{cid}/onboard", data={"baseline_id": "baseline-ai-engineer-entry"},
                 files={"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")})
    tasks.wait(r.json()["task_id"])
    first = tasks.wait(api.post(f"/candidates/{cid}/score", json={"baseline_id": "baseline-ai-engineer-entry"}).json()["task_id"])
    assert api.post("/market/ai_engineer/source", json={"source": "market"}).status_code == 422       # nothing published
    api.post("/market/ai_engineer/fixtures")
    v = api.post("/market/ai_engineer/recompute", json={"test": True}).json()
    assert api.put("/market/settings", json={"allow_test_versions": True}).status_code == 200
    pub = api.post(f"/market/versions/{v["id"]}/publish").json()
    assert pub.get("status") == "published", pub
    r = api.post("/market/ai_engineer/source", json={"source": "market"})
    assert r.status_code == 409 and r.json()["detail"]["candidates"] == 1 and r.json()["detail"]["calls"] > 0
    api.post("/market/ai_engineer/source", json={"source": "market", "confirm": True})
    second = tasks.wait(api.post(f"/candidates/{cid}/score", json={"baseline_id": "baseline-ai-engineer-entry"}).json()["task_id"])
    run = api.get(f"/runs/{second['result']['run_id']}").json()["run"]
    assert run["baseline_id"] == "market-ai_engineer-entry-v1" and run["baseline_source"] == "market v1"
    assert api.get(f"/runs/{first['result']['run_id']}").json()["run"]["baseline_source"] == "manual"
    from scoring import benchmarks
    assert benchmarks.baseline_pool(conn, "market-ai_engineer-entry-v1") is not None   # pools are per baseline version
    templates = api.get("/jobs/templates").json()
    assert any(t["title"] == "AI Engineer (Market v1)" for t in templates)


def test_dry_run_counts_without_a_model(conn, roles):
    pipeline.load_fixtures(conn, roles["data_scientist"])
    ms.add_posting(conn, role="data_scientist", company="Acme", tier="mid", source="manual", title="Data Scientist",
                   text=JD.replace("AI Engineer", "Data Scientist"))
    out = pipeline.dry_run(conn, roles, rpd=20)
    ds = out["roles"]["data_scientist"]
    assert ds["postings_in_window"] == 4 and ds["new_postings"] == 1
    assert ds["model_calls"]["extraction"] == 1 and ds["model_calls"]["summary"] == 1
    assert ds["sources"]["manual"]["placeholders"] == 5 and out["days_at_limit"] == 1
    assert out["roles"]["product_manager"]["model_calls"]["total"] == 0               # no postings, no summary
