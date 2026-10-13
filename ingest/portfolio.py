"""Fetch a portfolio page and reduce it to visible text."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from core import config

LITTLE_TEXT_WARNING = (
    "Your portfolio page returned very little text (it may be built with JavaScript). "
    "Paste its content or add your projects manually in the review step."
)
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)


class PortfolioError(Exception):
    """User-facing fetch problem; onboarding continues without the portfolio."""


@dataclass
class PortfolioPage:
    url: str
    text: str
    warning: str | None = None


def html_to_text(html: str) -> str:
    """Visible text only: drops scripts, styles and hidden elements (common prompt-injection hiding places)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe", "head"]):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        attrs = tag.attrs or {}
        if "hidden" in attrs or attrs.get("aria-hidden") == "true" or _HIDDEN_STYLE.search(attrs.get("style", "")):
            tag.decompose()
    lines = (line.strip() for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def page_from_text(url: str | None, text: str) -> PortfolioPage:
    text = text.strip()[: config.MAX_PORTFOLIO_CHARS]
    warning = LITTLE_TEXT_WARNING if len("".join(text.split())) < config.MIN_PORTFOLIO_CHARS else None
    return PortfolioPage(url or "", text, warning)


def fetch_portfolio(url: str, session: requests.Session | None = None) -> PortfolioPage:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise PortfolioError("Enter a full portfolio URL starting with http:// or https://.")
    session = session or requests.Session()
    try:
        resp = session.get(url, timeout=15, headers={"User-Agent": "RightStaff-mock/0.1"})
    except requests.RequestException as exc:
        raise PortfolioError(f"Could not fetch the portfolio ({exc.__class__.__name__}).") from exc
    if resp.status_code != 200:
        raise PortfolioError(f"The portfolio returned status {resp.status_code}.")
    content_type = resp.headers.get("Content-Type", "")
    text = html_to_text(resp.text) if "html" in content_type or "<html" in resp.text[:500].lower() else resp.text
    return page_from_text(url, text)
