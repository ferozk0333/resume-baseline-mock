"""Sending recruiter email (specs/13 §4).

EMAIL_PROVIDER=outbox (default): the email is stored and shown on the Outbox page; nothing leaves the machine.
EMAIL_PROVIDER=smtp: sent with SMTP_HOST/PORT/USER/PASSWORD from EMAIL_FROM.

Demo redirect: while DEMO_EMAIL_TO is set, every email goes to that address only. The subject is prefixed
"[RightStaff demo → <candidate name>]" and the body starts with the intended recipient. Any other recipient is
refused. Only `send()` delivers, and only for an email a recruiter sent from the UI (the Send action).
"""
from __future__ import annotations

import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

from core import config


class SendRefused(Exception):
    """The email may not go to this address (demo redirect) or the provider is not configured."""


@dataclass
class Outgoing:
    intended_to: str | None
    actual_to: str
    subject: str
    body: str
    provider: str


def address(intended_to: str | None, candidate_name: str | None, subject: str, body: str) -> Outgoing:
    """Where an email actually goes, with the demo redirect applied."""
    redirect = config.DEMO_EMAIL_TO
    if redirect:
        who = candidate_name or intended_to or "candidate"
        header = f"Intended recipient: {who} <{intended_to or 'no address on file'}>\n" \
                 f"(RightStaff demo: redirected to {redirect}.)\n\n"
        return Outgoing(intended_to, redirect, f"[RightStaff demo → {who}] {subject}", header + body,
                        config.EMAIL_PROVIDER)
    if not intended_to:
        raise SendRefused("The candidate has no email address on file.")
    return Outgoing(intended_to, intended_to, subject, body, config.EMAIL_PROVIDER)


def deliver(out: Outgoing) -> None:
    """Hand one addressed email to the provider. Refuses any recipient but DEMO_EMAIL_TO while it is set."""
    if config.DEMO_EMAIL_TO and out.actual_to.strip().lower() != config.DEMO_EMAIL_TO.strip().lower():
        raise SendRefused(f"Demo redirect is on: only {config.DEMO_EMAIL_TO} may receive email.")
    if out.provider == "outbox":
        return                                             # stored by the caller; shown on the Outbox page
    if out.provider != "smtp":
        raise SendRefused(f"Unknown EMAIL_PROVIDER {out.provider!r}.")
    missing = [k for k, v in (("SMTP_HOST", config.SMTP_HOST), ("SMTP_USER", config.SMTP_USER),
                              ("SMTP_PASSWORD", config.SMTP_PASSWORD), ("EMAIL_FROM", config.EMAIL_FROM)) if not v]
    if missing:
        raise SendRefused(f"SMTP is not configured: set {', '.join(missing)} in .env.")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.EMAIL_FROM, out.actual_to, out.subject
    msg.set_content(out.body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)
