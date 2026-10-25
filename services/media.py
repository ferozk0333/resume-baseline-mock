"""Demo video links (specs/10 §3): YouTube and Loom only, display only, not scored."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from core.db import new_id, now_iso

MAX_LINKS = 3
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LOOM_ID = re.compile(r"^[0-9a-f]{32}$")


class MediaError(ValueError):
    pass


def classify(url: str) -> tuple[str, str]:
    """(kind, video id) for a YouTube or Loom link; MediaError for anything else."""
    raw = (url or "").strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in parsed.path.split("/") if p]
    if parsed.scheme not in ("http", "https"):
        raise MediaError(f"'{url}' is not a web link.")
    if host in ("youtube.com", "youtube-nocookie.com"):
        vid = parse_qs(parsed.query).get("v", [None])[0] if parts[:1] == ["watch"] else \
            (parts[1] if len(parts) > 1 and parts[0] in ("embed", "shorts", "live") else None)
        if vid and _YOUTUBE_ID.match(vid):
            return "youtube", vid
    elif host == "youtu.be" and parts and _YOUTUBE_ID.match(parts[0]):
        return "youtube", parts[0]
    elif host == "loom.com" and len(parts) == 2 and parts[0] in ("share", "embed") and _LOOM_ID.match(parts[1]):
        return "loom", parts[1]
    raise MediaError(f"'{url}' is not a YouTube or Loom video link. Only YouTube and Loom are supported.")


def embed_url(kind: str, video_id: str) -> str:
    """Privacy-enhanced YouTube domain; Loom's embed player."""
    return f"https://www.youtube-nocookie.com/embed/{video_id}" if kind == "youtube" \
        else f"https://www.loom.com/embed/{video_id}"


def validate_links(urls: list[str]) -> list[tuple[str, str, str]]:
    urls = [u.strip() for u in urls if u and u.strip()]
    if len(urls) > MAX_LINKS:
        raise MediaError(f"Add at most {MAX_LINKS} demo videos.")
    return [(u, *classify(u)) for u in urls]


def save_links(conn, candidate_id: str, urls: list[str], titles: list[str] | None = None) -> None:
    links = validate_links(urls)
    titles = titles or []
    with conn:
        conn.execute("DELETE FROM media_links WHERE candidate_id = ?", (candidate_id,))
        conn.executemany("INSERT INTO media_links (id, candidate_id, url, kind, title, project_id, created_at) "
                         "VALUES (?, ?, ?, ?, ?, NULL, ?)",
                         [(new_id(), candidate_id, u, kind, titles[i] if i < len(titles) else None, now_iso())
                          for i, (u, kind, _) in enumerate(links)])


def list_links(conn, candidate_id: str) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM media_links WHERE candidate_id = ? ORDER BY created_at, rowid", (candidate_id,)):
        _, vid = classify(r["url"])
        out.append({**dict(r), "embed_url": embed_url(r["kind"], vid)})
    return out
