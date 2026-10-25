"""Test isolation: tests never send email, whatever .env says (EMAIL_PROVIDER=smtp, SMTP_* settings)."""
import pytest

from core import config


@pytest.fixture(autouse=True)
def no_real_email(monkeypatch):
    monkeypatch.setattr(config, "EMAIL_PROVIDER", "outbox")
    for name in ("SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "EMAIL_FROM"):
        monkeypatch.setattr(config, name, "")
    monkeypatch.setattr(config, "DEMO_EMAIL_TO", "demo@example.org")

    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to open an SMTP connection")
    monkeypatch.setattr("smtplib.SMTP", refuse)
    monkeypatch.setattr("smtplib.SMTP_SSL", refuse)


@pytest.fixture(autouse=True)
def no_workflow_ticker(monkeypatch):
    """The scheduled workflow checks never run in the background during tests (tests call tick() directly)."""
    monkeypatch.setattr(config, "WORKFLOW_TICK_SECONDS", 0)


@pytest.fixture(autouse=True)
def never_live(monkeypatch):
    """specs/18 §2 item 8: tests never call a real provider, whatever the environment says."""
    from core import config
    from providers.routing import LIMITER
    monkeypatch.setattr(config, "LIVE", False)
    LIMITER.reset()
