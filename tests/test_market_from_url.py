"""Fill a market posting from its URL (market/from_url.py). Offline: fake HTTP session and DNS."""
import json
import socket

import pytest

from market import from_url
from market.from_url import UrlError

BODY = "<p>" + "We build reliable ML systems and ship them to production. " * 8 + "</p><ul><li>Python</li><li>PyTorch</li></ul>"


class Resp:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code, self.headers = status, headers or {}
        self._body = body if isinstance(body, bytes) else body.encode()
        self.raw = self
        self.is_redirect = status in (301, 302, 303, 307, 308)

    def read(self, n, decode_content=True):
        return self._body[:n]

    @property
    def text(self):
        return self._content.decode()

    def json(self):
        return json.loads(self._content)


class Session:
    def __init__(self, routes):
        self.routes, self.seen = routes, []

    def get(self, url, **kw):
        self.seen.append(url)
        r = self.routes[url]
        return Resp(*r) if isinstance(r, tuple) else Resp(200, r)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    def fake(host, port, *a, **k):
        ip = {"localhost": "127.0.0.1", "internal.example": "10.0.0.5", "meta.example": "169.254.169.254"}.get(host, "93.184.216.34")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_greenhouse_job_page_uses_the_public_board_api():
    api = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/123"
    s = Session({api: json.dumps({"title": "Machine Learning Engineer", "company_name": "Acme",
                                  "content": "&lt;p&gt;" + BODY[3:], "first_published": "2026-10-01T11:27:13-04:00"})})
    got = from_url.read("https://job-boards.greenhouse.io/acme/jobs/123?jr_id=abc", s)
    assert (got["source"], got["title"], got["company"], got["posted_at"]) == ("greenhouse", "Machine Learning Engineer",
                                                                               "Acme", "2026-10-01")
    assert "- Python" in got["text"] and s.seen == [api]


def test_greenhouse_embed_with_gh_jid():
    api = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/987"
    s = Session({api: json.dumps({"title": "Data Scientist", "company_name": "Acme", "content": BODY})})
    assert from_url.read("https://boards.greenhouse.io/embed/job_app?for=acme&gh_jid=987", s)["title"] == "Data Scientist"


def test_lever_job_page():
    api = "https://api.lever.co/v0/postings/acme-ai/abc-123?mode=json"
    s = Session({api: json.dumps({"id": "abc-123", "text": "AI Engineer", "descriptionPlain": BODY, "lists": [],
                                  "createdAt": 1760000000000, "hostedUrl": "https://jobs.lever.co/acme-ai/abc-123"})})
    got = from_url.read("https://jobs.lever.co/acme-ai/abc-123", s)
    assert (got["source"], got["title"], got["company"]) == ("lever", "AI Engineer", "Acme Ai")


def test_other_sites_use_schema_org_job_posting():
    ld = {"@context": "https://schema.org", "@type": "JobPosting", "title": "Applied Scientist", "datePosted": "2026-09-30",
          "hiringOrganization": {"@type": "Organization", "name": "Globex"}, "description": BODY}
    page = f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script></head><body>menu</body></html>'
    got = from_url.read("https://careers.globex.example/jobs/42", Session({"https://careers.globex.example/jobs/42": page}))
    assert (got["source"], got["title"], got["company"], got["posted_at"]) == ("schema.org", "Applied Scientist", "Globex",
                                                                               "2026-09-30")


def test_a_plain_page_falls_back_to_title_and_visible_text():
    page = f'<html><head><title>ML Engineer at Initech</title><meta property="og:site_name" content="Initech">' \
           f'</head><body><script>var x=1</script>{BODY}</body></html>'
    got = from_url.read("https://initech.example/ml", Session({"https://initech.example/ml": page}))
    assert (got["source"], got["title"], got["company"]) == ("page", "ML Engineer at Initech", "Initech")
    assert "var x" not in got["text"]


@pytest.mark.parametrize("url", ["http://localhost:8000/healthz", "http://internal.example/x", "http://meta.example/latest",
                                 "ftp://files.example/a", "not a url"])
def test_only_public_web_pages_are_read(url):
    with pytest.raises(UrlError):
        from_url.read(url, Session({}))


def test_a_redirect_to_a_private_address_is_refused():
    s = Session({"https://short.example/a": (302, b"", {"Location": "http://internal.example/secret"})})
    with pytest.raises(UrlError, match="public"):
        from_url.read("https://short.example/a", s)


def test_a_page_without_a_posting_says_so():
    s = Session({"https://spa.example/job": "<html><body><div id=root></div></body></html>"})
    with pytest.raises(UrlError, match="Paste the text"):
        from_url.read("https://spa.example/job", s)
