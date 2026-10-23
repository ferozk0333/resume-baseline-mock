"""Fill the "Add a posting" form from the posting's URL (code only, no model call; the recruiter checks and adds).

- Greenhouse, Lever and Ashby job pages: their official public job-board APIs (exact title, company, date, text).
- Any other page: its schema.org JobPosting data (most job sites publish it), else the page title and visible text.
Only public http(s) addresses: a host that resolves to a private, loopback or link-local address is refused, and so
is every redirect to one (the server must not fetch its own network for a user).
"""
from __future__ import annotations

import html
import ipaddress
import json
import re
import socket
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from core import config
from market.fetchers import html_text

MAX_BYTES = 3_000_000
UNREADABLE = "Couldn't read the posting from this page (it may need a sign-in or JavaScript). Paste the text or upload the file."


class UrlError(ValueError):
    pass


def _public(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise UrlError("Enter a full URL starting with http:// or https://.")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise UrlError("That address can't be found. Check the URL.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise UrlError("Only public web pages can be read.")


def _get(session, url: str, accept: str = "text/html,application/json"):
    """GET with every redirect checked against _public; a size cap."""
    for _ in range(5):
        _public(url)
        r = session.get(url, timeout=20, allow_redirects=False, stream=True,
                        headers={"User-Agent": config.MARKET_USER_AGENT, "Accept": accept})
        if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
            url = urljoin(url, r.headers.get("Location", ""))
            continue
        if r.status_code >= 400:
            raise UrlError(f"The page answered with an error (HTTP {r.status_code}).")
        body = r.raw.read(MAX_BYTES + 1, decode_content=True)
        if len(body) > MAX_BYTES:
            raise UrlError("The page is too large to read.")
        r._content = body
        return r
    raise UrlError("Too many redirects.")


def _date(v: str | None) -> str | None:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", v or "")
    return m.group(1) if m else None


def _out(source, title, company, text, posted_at, url) -> dict:
    return {"source": source, "title": (title or "").strip(), "company": (company or "").strip(),
            "text": (text or "").strip(), "posted_at": _date(posted_at), "url": url}


def _greenhouse(session, url: str, p) -> dict | None:
    parts = [x for x in p.path.split("/") if x]
    board = job = None
    if "jobs" in parts and parts.index("jobs") >= 1 and len(parts) > parts.index("jobs") + 1:
        board, job = parts[parts.index("jobs") - 1], parts[parts.index("jobs") + 1]
    job = (parse_qs(p.query).get("gh_jid") or [job])[0]
    board = (parse_qs(p.query).get("for") or [board])[0]
    if not (board and job and job.isdigit()):
        return None
    d = _get(session, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}", "application/json").json()
    return _out("greenhouse", d.get("title"), d.get("company_name") or board.title(),
                html_text(html.unescape(d.get("content") or "")), d.get("first_published") or d.get("updated_at"), url)


def _lever(session, url: str, p) -> dict | None:
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        return None
    api = "api.eu.lever.co" if p.hostname.endswith("eu.lever.co") else "api.lever.co"
    from market.fetchers import lever
    d = _get(session, f"https://{api}/v0/postings/{parts[0]}/{parts[1]}?mode=json", "application/json").json()
    raw = lever([d])[0]
    return _out("lever", raw.title, parts[0].replace("-", " ").title(), raw.text, raw.posted_at, url)


def _ashby(session, url: str, p) -> dict | None:
    parts = [x for x in p.path.split("/") if x]
    if len(parts) < 2:
        return None
    from market.fetchers import ashby
    d = _get(session, f"https://api.ashbyhq.com/posting-api/job-board/{parts[0]}", "application/json").json()
    for raw in ashby(d):
        if parts[1] in (raw.external_id or "") or parts[1] in (raw.url or ""):
            return _out("ashby", raw.title, parts[0].replace("-", " ").title(), raw.text, raw.posted_at, url)
    return None


BOARDS = {"greenhouse.io": _greenhouse, "lever.co": _lever, "ashbyhq.com": _ashby}


def _job_posting(soup) -> dict | None:
    """schema.org JobPosting in a JSON-LD script, if the page has one."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and "JobPosting" in str(it.get("@type")):
                return it
    return None


def _page(session, url: str) -> dict:
    from ingest.portfolio import html_to_text
    r = _get(session, url)
    soup = BeautifulSoup(r.text, "html.parser")
    jp = _job_posting(soup)
    if jp:
        org = jp.get("hiringOrganization")
        company = org.get("name") if isinstance(org, dict) else org
        return _out("schema.org", jp.get("title"), company, html_text(html.unescape(jp.get("description") or "")),
                    jp.get("datePosted"), url)
    meta = {m.get("property") or m.get("name"): m.get("content") for m in soup.find_all("meta") if m.get("content")}
    title = meta.get("og:title") or (soup.title.string if soup.title else "")
    return _out("page", title, meta.get("og:site_name", ""), html_to_text(r.text), None, url)


def read(url: str, session=None) -> dict:
    """{source, title, company, text, posted_at, url}. Raises UrlError with a message for the form."""
    url = (url or "").strip()
    _public(url)
    session = session or requests.Session()
    p = urlparse(url)
    try:
        for host, reader in BOARDS.items():
            if p.hostname == host or p.hostname.endswith("." + host):
                got = reader(session, url, p)
                if got:
                    break
        else:
            got = None
        got = got or _page(session, url)
    except UrlError:
        raise
    except (requests.RequestException, ValueError, KeyError, IndexError) as exc:
        raise UrlError(UNREADABLE) from exc
    if len(got["text"]) < 200:
        raise UrlError(UNREADABLE)
    return got
