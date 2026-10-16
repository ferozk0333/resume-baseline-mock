"""Official public job-board APIs only (specs/15 §2). No scraping of career sites, LinkedIn or Indeed.

Checked 2026-10-16:
- Greenhouse Job Board API: GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true.
  GET endpoints need no authentication ("Job Board data is publicly available"); `content` is HTML-entity encoded.
- Lever Postings API: GET https://api.lever.co/v0/postings/{site}?mode=json (EU: api.eu.lever.co). GET is public;
  only application POSTs are rate limited (2/s) and need a key.
- Ashby public job posting API: GET https://api.ashbyhq.com/posting-api/job-board/{board}. Postings with
  isListed=false are for direct links only and are skipped here.
None of them publishes a read rate limit, so each board is read once per weekly fetch, with an identifying
User-Agent, and a 429 stops that board for this run (Retry-After respected by not retrying).
"""
from __future__ import annotations

import html
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from core import config

URLS = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/{id}/jobs?content=true",
        "lever": "https://api.lever.co/v0/postings/{id}?mode=json",
        "ashby": "https://api.ashbyhq.com/posting-api/job-board/{id}"}


class FetchError(Exception):
    pass


@dataclass
class RawPosting:
    source: str
    external_id: str
    title: str
    location: str | None
    url: str | None
    text: str
    posted_at: str | None


def html_text(markup: str) -> str:
    soup = BeautifulSoup(markup or "", "html.parser")
    for li in soup.find_all("li"):
        li.replace_with(f"\n- {li.get_text(' ', strip=True)}\n")
    text = soup.get_text("\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def _get(session, url: str):
    r = session.get(url, timeout=30, headers={"User-Agent": config.MARKET_USER_AGENT, "Accept": "application/json"})
    if r.status_code == 429:
        raise FetchError(f"rate limited (429); Retry-After={r.headers.get('Retry-After')}")
    if r.status_code == 404:
        raise FetchError("board not found (404): check source_id")
    if r.status_code >= 400:
        raise FetchError(f"HTTP {r.status_code}")
    return r.json()


def greenhouse(data: dict) -> list[RawPosting]:
    out = []
    for j in data.get("jobs", []):
        out.append(RawPosting("greenhouse", str(j["id"]), j.get("title", ""), (j.get("location") or {}).get("name"),
                              j.get("absolute_url"), html_text(html.unescape(j.get("content") or "")),
                              j.get("first_published") or j.get("updated_at")))
    return out


def lever(data: list) -> list[RawPosting]:
    out = []
    for j in data:
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists", []):
            parts.append(lst.get("text", ""))
            parts.append(html_text(lst.get("content", "")))
        parts.append(j.get("additionalPlain") or "")
        created = j.get("createdAt")
        from datetime import datetime, timezone
        when = datetime.fromtimestamp(created / 1000, timezone.utc).isoformat() if created else None
        out.append(RawPosting("lever", j["id"], j.get("text", ""), (j.get("categories") or {}).get("location"),
                              j.get("hostedUrl"), "\n".join(p for p in parts if p).strip(), when))
    return out


def ashby(data: dict) -> list[RawPosting]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        text = j.get("descriptionPlain") or html_text(j.get("descriptionHtml") or "")
        out.append(RawPosting("ashby", j.get("id") or j.get("jobUrl", ""), j.get("title", ""), j.get("location"),
                              j.get("jobUrl"), text, j.get("publishedAt")))
    return out


PARSERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}


def fetch(source: str, source_id: str, session=None) -> list[RawPosting]:
    if source not in URLS:
        raise FetchError(f"{source} has no public API: add its postings by hand")
    return PARSERS[source](_get(session or requests.Session(), URLS[source].format(id=source_id)))
