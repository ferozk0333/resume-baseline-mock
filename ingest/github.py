"""Public GitHub REST API: a user's repos with languages, own-commit counts and READMEs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

import requests

from core import config

API = "https://api.github.com"
_USERNAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")


class GitHubError(Exception):
    """User-facing GitHub problem; onboarding continues with `partial` repos (often none)."""

    def __init__(self, message: str, partial: list | None = None):
        super().__init__(message)
        self.partial = partial or []


@dataclass
class Repo:
    name: str
    full_name: str
    description: str | None
    language: str | None
    html_url: str
    is_fork: bool
    created_at: str | None
    pushed_at: str | None
    languages: dict[str, int] = field(default_factory=dict)
    own_commits: int = 0
    readme: str | None = None

    @property
    def counted(self) -> bool:
        # 01: forks with zero commits by the user are not the user's work.
        return not (self.is_fork and self.own_commits == 0)

    def as_text(self) -> str:
        """Source text for this repo's evidence item (quotes are checked against it)."""
        lines = [
            f"Repository: {self.full_name}",
            f"URL: {self.html_url}",
            f"Description: {self.description or ''}",
            f"Primary language: {self.language or ''}",
            f"Languages: {', '.join(self.languages)}",
            f"Fork: {'yes' if self.is_fork else 'no'}",
            f"Commits by user: {self.own_commits}",
            f"Last push: {self.pushed_at or ''}",
        ]
        if self.readme:
            lines += ["", "README:", self.readme]
        return "\n".join(lines)


def is_profile_repo(repo_name: str, username: str) -> bool:
    """The GitHub profile README repo (<user>/<user>) or the personal site repo (<user>.github.io).

    These describe the person, not a project, so they are not counted as projects or work samples.
    The personal site counts through the portfolio URL instead.
    """
    name, user = repo_name.casefold(), username.casefold()
    return name in (user, f"{user}.github.io", f"{user}.github.com")


def parse_username(value: str | None) -> str | None:
    """Accept 'user', '@user', 'github.com/user' or a full profile/repo URL."""
    if not value or not value.strip():
        return None
    value = value.strip().lstrip("@")
    if "github.com" in value:
        if "://" not in value:
            value = "https://" + value
        parts = [p for p in urlparse(value).path.split("/") if p]
        value = parts[0] if parts else ""
    return value if _USERNAME.match(value) else None


class GitHubClient:
    def __init__(self, session: requests.Session | None = None, token: str | None = None):
        self.session = session or requests.Session()
        self.token = config.GITHUB_TOKEN if token is None else token

    def _get(self, path: str, params: dict | None = None, accept: str = "application/vnd.github+json"):
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            resp = self.session.get(f"{API}{path}", params=params, headers=headers, timeout=15)
        except requests.RequestException as exc:
            raise GitHubError(f"Could not reach GitHub ({exc.__class__.__name__}).") from exc
        if resp.status_code in (403, 429) and (
            resp.headers.get("X-RateLimit-Remaining") == "0" or resp.status_code == 429
        ):
            raise GitHubError(
                "GitHub rate limit reached; continuing without GitHub. "
                "Set GITHUB_TOKEN in .env to raise the limit."
            )
        return resp

    def _own_commits(self, full_name: str, username: str) -> int:
        resp = self._get(f"/repos/{full_name}/commits", {"author": username, "per_page": 1})
        if resp.status_code == 409:  # empty repository
            return 0
        if resp.status_code != 200:
            return 0
        last = resp.links.get("last", {}).get("url")
        if last:
            return int(parse_qs(urlparse(last).query).get("page", ["1"])[0])
        return len(resp.json())

    def _languages(self, full_name: str) -> dict[str, int]:
        resp = self._get(f"/repos/{full_name}/languages")
        return resp.json() if resp.status_code == 200 else {}

    def _readme(self, full_name: str) -> str | None:
        resp = self._get(f"/repos/{full_name}/readme", accept="application/vnd.github.raw+json")
        if resp.status_code != 200:
            return None
        return resp.text[: config.GITHUB_README_CHARS]

    def fetch_repos(self, username: str, include_readme: bool = True) -> list[Repo]:
        user = self._get(f"/users/{username}")
        if user.status_code == 404:
            raise GitHubError(f"GitHub user '{username}' was not found.")
        if user.status_code != 200:
            raise GitHubError(f"GitHub returned status {user.status_code}.")

        raw: list[dict] = []
        page = 1
        while True:
            resp = self._get(f"/users/{username}/repos",
                             {"type": "owner", "sort": "pushed", "per_page": 100, "page": page})
            if resp.status_code != 200:
                raise GitHubError(f"Could not list repositories (status {resp.status_code}).")
            batch = resp.json()
            raw += batch
            if len(batch) < 100 or len(raw) >= 300:
                break
            page += 1

        active = [r for r in raw if not r.get("archived") and not is_profile_repo(r["name"], username)]
        active.sort(key=lambda r: r.get("pushed_at") or "", reverse=True)
        repos: list[Repo] = []
        try:
            for r in active[: config.GITHUB_MAX_REPOS]:
                repos.append(self._repo_details(r, username, include_readme))
        except GitHubError as exc:
            # Keep what was fetched before the rate limit hit.
            raise GitHubError(f"{exc} Kept {len(repos)} of {len(active)} repositories.", partial=repos) from exc
        return repos

    def _repo_details(self, r: dict, username: str, include_readme: bool) -> Repo:
        repo = Repo(
            name=r["name"],
            full_name=r["full_name"],
            description=r.get("description"),
            language=r.get("language"),
            html_url=r["html_url"],
            is_fork=bool(r.get("fork")),
            created_at=r.get("created_at"),
            pushed_at=r.get("pushed_at"),
        )
        repo.own_commits = self._own_commits(repo.full_name, username)
        repo.languages = self._languages(repo.full_name)
        if include_readme and repo.counted:
            repo.readme = self._readme(repo.full_name)
        return repo
