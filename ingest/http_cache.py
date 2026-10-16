"""HTTP response caching and record/replay for the GitHub API (iteration 2, phase 9).

CachedSession   24-hour cache in SQLite, keyed by URL (+ Accept header). A stale entry with an ETag
                is revalidated with If-None-Match; GitHub answers 304 without spending rate limit.
RecordingSession / ReplaySession
                Record one run's responses (with their latency) to a JSON file and replay them,
                so timing measurements use identical GitHub input. Replay never touches the network.

All three expose `get(url, params=None, headers=None, timeout=None)` like requests.Session.
The Authorization header is never part of a key and never stored.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from requests.utils import parse_header_links

from core import config

CACHEABLE = {200, 404, 409}      # 409 = empty repository


class CachedResponse:
    """The parts of requests.Response the GitHub client reads."""

    def __init__(self, status_code: int, text: str, headers: dict):
        self.status_code = status_code
        self.text = text
        self.headers = requests.structures.CaseInsensitiveDict(headers)
        link = self.headers.get("Link")
        self.links = {(lk.get("rel") or lk["url"]): lk for lk in parse_header_links(link)} if link else {}

    def json(self):
        return json.loads(self.text)


def request_key(url: str, params: dict | None, headers: dict | None) -> str:
    full = requests.Request("GET", url, params=params).prepare().url
    return f"{full}|{(headers or {}).get('Accept', '')}"


def _kept_headers(resp) -> dict:
    kept = {k: v for k, v in resp.headers.items() if k.lower() in ("etag", "link", "content-type")}
    links = getattr(resp, "links", None)
    if links and not any(k.lower() == "link" for k in kept):     # pagination ("last" page = commit count)
        kept["Link"] = ", ".join(f'<{v["url"]}>; rel="{rel}"' for rel, v in links.items())
    return kept


class CachedSession:
    def __init__(self, conn, session: requests.Session | None = None, max_age_hours: float | None = None):
        self.conn = conn
        self.session = session or requests.Session()
        self.max_age = timedelta(hours=config.GITHUB_CACHE_HOURS if max_age_hours is None else max_age_hours)
        self._lock = threading.Lock()
        self.hits = self.revalidated = self.misses = 0

    def _load(self, key: str):
        with self._lock:
            return self.conn.execute("SELECT * FROM http_cache WHERE key = ?", (key,)).fetchone()

    def _store(self, key: str, status: int, text: str, headers: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO http_cache (key, status, headers_json, body, fetched_at) "
                              "VALUES (?, ?, ?, ?, ?)", (key, status, json.dumps(headers), text,
                                                         datetime.now(timezone.utc).isoformat()))

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, timeout=None):
        key = request_key(url, params, headers)
        row = self._load(key)
        if row is not None:
            cached = CachedResponse(row["status"], row["body"], json.loads(row["headers_json"]))
            age = datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])
            if age < self.max_age:
                self.hits += 1
                return cached
            etag = cached.headers.get("ETag")
            if etag:
                resp = self.session.get(url, params=params, headers={**(headers or {}), "If-None-Match": etag},
                                        timeout=timeout)
                if resp.status_code == 304:
                    self.revalidated += 1
                    self._store(key, row["status"], row["body"], json.loads(row["headers_json"]))
                    return cached
                return self._keep(key, resp)
        self.misses += 1
        return self._keep(key, self.session.get(url, params=params, headers=headers, timeout=timeout))

    def _keep(self, key: str, resp):
        if resp.status_code in CACHEABLE:
            self._store(key, resp.status_code, resp.text, _kept_headers(resp))
        return resp


class RecordingSession:
    """Pass-through that remembers every response and how long it took."""

    def __init__(self, session=None):
        self.session = session or requests.Session()
        self.records: dict[str, dict] = {}
        self._lock = threading.Lock()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, timeout=None):
        start = time.perf_counter()
        resp = self.session.get(url, params=params, headers=headers, timeout=timeout)
        with self._lock:
            self.records[request_key(url, params, headers)] = {
                "status": resp.status_code, "headers": _kept_headers(resp), "text": resp.text,
                "seconds": round(time.perf_counter() - start, 4)}
        return resp

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.records, indent=1, sort_keys=True))


class ReplaySession:
    """Serves recorded responses; `delay=True` waits each response's recorded latency."""

    def __init__(self, path: str | Path, delay: bool = True):
        self.records = json.loads(Path(path).read_text())
        self.delay = delay

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, timeout=None):
        rec = self.records.get(request_key(url, params, headers))
        if rec is None:
            raise requests.ConnectionError(f"not recorded: {url}")
        if self.delay:
            time.sleep(rec["seconds"])
        return CachedResponse(rec["status"], rec["text"], rec["headers"])
