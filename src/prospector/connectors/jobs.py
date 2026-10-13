"""Hiring signals from public job boards the company itself links to (Greenhouse, Lever, Ashby).

The board is detected during the website crawl, so no board names are guessed. Roles that matter for ALI's
offers (IT, security, data, AI, operations, compliance, innovation) are listed first.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from sqlalchemy import select

from ..models import Company
from ..resolve import CompanyRecord, api_evidence
from .base import Connector, ParamSpec

RELEVANT = re.compile(
    r"\b(it|information technology|security|cyber|data|ai|machine learning|ml|automation|innovation|"
    r"knowledge|operations|ops|compliance|systems|infrastructure|devops|engineer|developer|digital|technology|"
    r"practice manager|transformation)\b",
    re.I,
)
MAX_ROLES = 25


def board_urls(provider: str, token: str) -> tuple[str, str]:
    """(API URL, public board URL)."""
    if provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", f"https://boards.greenhouse.io/{token}"
    if provider == "lever":
        return f"https://api.lever.co/v0/postings/{token}?mode=json", f"https://jobs.lever.co/{token}"
    if provider == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}", f"https://jobs.ashbyhq.com/{token}"
    raise ValueError(f"unknown job board provider: {provider}")


def parse_roles(provider: str, body: Any) -> list[tuple[str, str | None, str | None]]:
    """-> [(title, location, url)]"""
    roles = []
    if provider == "greenhouse":
        for j in (body or {}).get("jobs", []):
            roles.append((j.get("title"), (j.get("location") or {}).get("name"), j.get("absolute_url")))
    elif provider == "lever":
        for j in body or []:
            roles.append((j.get("text"), (j.get("categories") or {}).get("location"), j.get("hostedUrl")))
    elif provider == "ashby":
        for j in (body or {}).get("jobs", []):
            roles.append((j.get("title"), j.get("location"), j.get("jobUrl")))
    return [(t.strip(), loc, url) for t, loc, url in roles if t]


class JobsConnector(Connector):
    key = "jobs"
    name = "Job boards"
    description = "Open roles from the public Greenhouse, Lever and Ashby boards linked on company websites."
    licence = "Public job postings"
    needs_db = True
    params = [ParamSpec("limit", "Max companies", "int", False, default=50)]

    def __init__(self, http, api_key=None, db=None) -> None:
        super().__init__(http, api_key)
        self.db = db

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        with self.db.session() as s:
            rows = s.execute(
                select(Company.name, Company.website, Company.ats)
                .where(Company.ats.is_not(None), Company.website.is_not(None))
                .order_by(Company.fit_score.desc().nullslast(), Company.id)
                .limit(int(params.get("limit") or 50))
            ).all()
        for name, website, ats in rows:
            provider, token = (ats or {}).get("provider"), (ats or {}).get("token")
            if not provider or not token:
                continue
            api_url, public_url = board_urls(provider, token)
            try:
                roles = parse_roles(provider, self.http.get(api_url, interval=1.0).json())
            except Exception:
                continue  # board removed or renamed: skip, the next crawl re-detects it
            if not roles:
                continue
            roles.sort(key=lambda r: 0 if RELEVANT.search(r[0]) else 1)
            signals = [
                ("hiring", f"Open role: {title}" + (f" ({loc})" if loc else ""), url or public_url)
                for title, loc, url in roles[:MAX_ROLES]
            ]
            yield CompanyRecord(
                source="jobs", url=public_url, name=name, website=website, confidence=0.9, signals=signals,
                evidence=[api_evidence(public_url, roles)],
            )  # fmt: skip
