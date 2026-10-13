"""GitHub: public commit metadata as a business email signal for companies already in the database.

A commit's author name/email is public data that a developer publishes with their work. We keep it only
when the address sits on the company's own domain (a business address the company controls, the same bar
the crawler applies to its own pages); personal/free-mail addresses and role mailboxes are dropped, so
nothing here resolves a private individual's personal contact details.

How it works: the company's domain is looked up in the public repository search, then the recent commits
of the top repositories are read, and authors whose email is on that domain become named contacts with
the commit page as their receipt.

Rate limits (api.github.com): unauthenticated = 60 core requests/hour and 10 search requests/minute, so
one run is deliberately small. Setting PROSPECTOR_GITHUB_TOKEN to a free personal access token (no scopes
needed for public data) raises that to 5000/hour and 30 search/minute, and shortens the pacing. A
User-Agent with contact information is always sent (GitHub requires one).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import httpx
from sqlalchemy import select

from ..models import Company
from ..normalize import email_kind
from ..resolve import CompanyRecord, PersonRecord, api_evidence
from .base import Connector, ParamSpec

API = "https://api.github.com"
TOKEN_ENV = "PROSPECTOR_GITHUB_TOKEN"
UNAUTH_INTERVAL = 6.5  # search: 10 requests/minute without a token
TOKEN_INTERVAL = 2.0  # with a token: 30 search requests/minute
MAX_REPOS = 3
COMMITS_PER_REPO = 30


def token() -> str | None:
    """Free personal access token, optional (see the module docstring for what it buys)."""
    return os.environ.get(TOKEN_ENV, "").strip() or None


def headers() -> dict[str, str]:
    out = {"Accept": "application/vnd.github+json"}
    auth = token()
    if auth:
        out["Authorization"] = f"Bearer {auth}"
    return out


class GithubConnector(Connector):
    key = "github"
    name = "GitHub commits"
    description = (
        "Commit authors whose email is on the company's own domain (a business address signal), with the "
        "commit as its receipt. Keyless: 60 requests/hour; PROSPECTOR_GITHUB_TOKEN raises it to 5000/hour."
    )
    licence = "GitHub API terms; public commit metadata"
    needs_db = True
    params = [ParamSpec("limit", "Max companies", "int", False, default=15)]

    def __init__(self, http, api_key=None, db=None) -> None:
        super().__init__(http, api_key)
        self.db = db

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        limit = int(params.get("limit") or 15)
        with self.db.session() as s:
            rows = s.execute(
                select(Company.id, Company.name, Company.website, Company.domain)
                .where(Company.domain.is_not(None))
                .order_by(Company.fit_score.desc().nullslast(), Company.id)
                .limit(limit)
            ).all()
        interval = TOKEN_INTERVAL if token() else UNAUTH_INTERVAL
        for _cid, name, website, domain in rows:
            try:
                rec = self._company(name, website, domain, interval)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (403, 429):
                    break  # rate limited: every remaining company would fail the same way
                continue
            except Exception:
                continue  # one broken repository or a host that is down must not sink the run
            if rec is not None:
                yield rec

    def _company(self, name: str, website: str | None, domain: str, interval: float) -> CompanyRecord | None:
        search = self.http.get(
            f"{API}/search/repositories",
            params={"q": domain, "per_page": MAX_REPOS},
            headers=headers(),
            interval=interval,
        )
        items = search.json().get("items") or []
        people: list[PersonRecord] = []
        evidence = []
        repo_url: str | None = None
        seen: set[str] = set()
        for item in items[:MAX_REPOS]:
            full = item.get("full_name")
            if not full:
                continue
            repo_url = repo_url or f"https://github.com/{full}"
            commits_url = f"{API}/repos/{full}/commits"
            try:
                commits = self.http.get(
                    commits_url, params={"per_page": COMMITS_PER_REPO}, headers=headers(), interval=interval
                ).json()
            except Exception:
                continue
            evidence.append(api_evidence(commits_url, commits))
            if not isinstance(commits, list):
                continue
            for commit in commits:
                author = ((commit or {}).get("commit") or {}).get("author") or {}
                email = (author.get("email") or "").strip().lower()
                person = " ".join((author.get("name") or "").split())
                if not email or not person or person.lower() in seen:
                    continue
                if email_kind(email, domain) != "published":
                    continue  # only the company's own named addresses: never personal or role mailboxes
                message = ((commit.get("commit") or {}).get("message") or "").splitlines()
                seen.add(person.lower())
                people.append(
                    PersonRecord(
                        name=person,
                        email=email,
                        url=commit.get("html_url"),
                        excerpt=(message[0].strip()[:200] or None) if message else None,
                    )
                )
        if not people:
            return None
        return CompanyRecord(
            source="github",
            url=repo_url,
            name=name,
            website=website,
            confidence=0.75,
            people=people,
            evidence=evidence,
        )
