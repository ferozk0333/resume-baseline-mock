"""Prototype deployment (docs/deploy.md): health check, upload rate limit, spending caps, the required demo redirect."""
import pytest

from core import config, db, spend
from tests.test_api import DS, api  # noqa: F401  (fixture)


def test_healthz_says_ok(api):  # noqa: F811
    r = api.get("/healthz")
    assert r.status_code == 200 and r.text == "ok"


def test_uploads_are_limited_per_address(api, monkeypatch):  # noqa: F811
    from tests.helpers import SAMPLE_RESUME_LINES, make_pdf
    monkeypatch.setattr(config, "UPLOAD_RATE_PER_HOUR", 2)
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(api.app.state.rs.deps)
    with TestClient(app) as c:
        cid = c.post("/candidates", json={"name": "Rate Test"}).json()["id"]
        files = {"resume": ("cv.pdf", make_pdf(SAMPLE_RESUME_LINES), "application/pdf")}
        codes = [c.post(f"/candidates/{cid}/onboard", data={"baseline_id": DS}, files=files).status_code for _ in range(3)]
        assert codes[:2] == [202, 202] and codes[2] == 429
        r = c.post(f"/candidates/{cid}/onboard", data={"baseline_id": DS}, files=files)
        assert r.status_code == 429 and "Too many uploads" in r.json()["detail"] and int(r.headers["retry-after"]) > 0
        assert c.get("/healthz").status_code == 200                       # only uploads are limited


def test_upload_limit_uses_the_forwarded_address_only_behind_the_proxy(monkeypatch):
    from backend.main import UploadRateLimit

    class Req:
        headers = {"x-forwarded-for": "203.0.113.7, 10.0.0.2"}
        client = type("C", (), {"host": "172.18.0.5"})()
    monkeypatch.setattr(config, "TRUST_PROXY", False)
    assert UploadRateLimit.address(Req()) == "172.18.0.5"
    monkeypatch.setattr(config, "TRUST_PROXY", True)
    assert UploadRateLimit.address(Req()) == "203.0.113.7"
    t = [0.0]
    lim = UploadRateLimit(2, clock=lambda: t[0])
    assert [lim.wait_seconds("a") for _ in range(3)][:2] == [0, 0] and lim.wait_seconds("b") == 0
    t[0] = 3601
    assert lim.wait_seconds("a") == 0                                     # the window moved on


def test_daily_spending_caps(tmp_path, monkeypatch):
    path = str(tmp_path / "s.db")
    db.init_app_db(path).close()
    monkeypatch.setattr(config, "JEV_DAILY_MAX_REQUESTS", 2)
    monkeypatch.setattr(config, "CLAUDE_DAILY_MAX_REQUESTS", 0)
    spend.charge("jev", path)
    spend.charge("jev", path)
    with pytest.raises(spend.SpendCapReached):
        spend.charge("jev", path)
    assert spend.used_today("jev", path) == 2
    with pytest.raises(spend.SpendCapReached):
        spend.charge("claude", path)                                       # 0: no paid Claude calls at all


def test_jev_past_its_cap_falls_back_without_sending(tmp_path, monkeypatch):
    from judges.jev_judge import JevError, JevJudge
    path = str(tmp_path / "s.db")
    db.init_app_db(path).close()
    monkeypatch.setattr(config, "DB_PATH", path)
    monkeypatch.setattr(config, "LIVE", True)
    monkeypatch.setattr(config, "JEV_DAILY_MAX_REQUESTS", 0)
    sent = []
    j = JevJudge(api_key="x", fallback=None)
    j._real = True
    j.session = type("S", (), {"post": lambda *a, **k: sent.append(1)})()
    with pytest.raises(JevError, match="daily cap"):
        j._post({"questions": {}})
    assert sent == []


def test_the_api_refuses_to_start_without_the_demo_redirect(monkeypatch):
    from backend.main import create_app
    monkeypatch.setattr(config, "REQUIRE_DEMO_EMAIL_TO", True)
    monkeypatch.setattr(config, "DEMO_EMAIL_TO", "")
    with pytest.raises(RuntimeError, match="DEMO_EMAIL_TO must be set"):
        create_app()
