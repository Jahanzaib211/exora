"""News signals from GDELT (free global news index): recent articles that name a company.

Matching is conservative because company names collide: the exact name must appear in the headline, the
name must be distinctive (two or more meaningful words, or one long uncommon word), and very short or
dictionary-like single names are skipped. Best-fit companies are checked first; each company at most weekly.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update

from ..models import Company, utcnow
from ..normalize import ascii_fold
from ..resolve import CompanyRecord
from .base import Connector, ParamSpec

API = "https://api.gdeltproject.org/api/v2/doc/doc"
HN_API = "https://hn.algolia.com/api/v1/search"
GENERIC = {
    "law", "legal", "group", "partners", "partner", "solicitors", "associates", "accounting", "consulting",
    "services", "solutions", "clinic", "medical", "health", "the", "and", "company", "office", "care",
}  # fmt: skip


def search_name(name: str) -> str | None:
    """The phrase to search, or None when the name is too generic to match reliably."""
    clean = re.sub(r"\b(llp|llc|ltd|limited|inc|plc|pllc|pc|gmbh|sa|ag)\b\.?", "", name, flags=re.I)
    clean = re.sub(r"[^\w&' -]", " ", clean)
    clean = " ".join(clean.split())
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'&-]+", clean)]
    meaningful = [w for w in words if w.lower() not in GENERIC]
    if len(meaningful) >= 2 or (len(meaningful) == 1 and len(meaningful[0]) >= 8 and len(words) >= 1):
        return clean
    return None


def headline_mentions(title: str, phrase: str) -> bool:
    t = ascii_fold(title).lower()
    p = ascii_fold(phrase).lower()
    return re.search(r"(?<![a-z])" + re.escape(p) + r"(?![a-z])", t) is not None


class NewsConnector(Connector):
    key = "news"
    name = "News mentions"
    description = "Recent news articles that name your best-fit companies (GDELT), as dated signals."
    licence = "GDELT open data; articles link to their publishers"
    needs_db = True
    params = [
        ParamSpec("min_fit", "Minimum fit score", "int", False, default=50),
        ParamSpec("limit", "Max companies", "int", False, default=40),
        ParamSpec("days", "Look back (days)", "int", False, default=90, help="Up to 90 days"),
    ]

    def __init__(self, http, api_key=None, db=None) -> None:
        super().__init__(http, api_key)
        self.db = db
        self.checked_ids: list[int] = []

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        cutoff = utcnow() - timedelta(days=7)
        with self.db.session() as s:
            rows = s.execute(
                select(Company.id, Company.name, Company.website, Company.segment)
                .where(
                    Company.fit_score >= int(params.get("min_fit") or 50),
                    or_(Company.last_news_at.is_(None), Company.last_news_at < cutoff),
                )
                .order_by(Company.fit_score.desc(), Company.id)
                .limit(int(params.get("limit") or 40))
            ).all()
        days = max(1, min(int(params.get("days") or 90), 90))
        for cid, name, website, segment in rows:
            self.checked_ids.append(cid)
            phrase = search_name(name)
            if not phrase:
                continue
            signals = self._hacker_news(phrase, days) if segment == "AI startup" else []
            try:
                body = self.http.get(
                    API,
                    params={
                        "query": f'"{phrase}"',
                        "mode": "artlist",
                        "format": "json",
                        "maxrecords": 15,
                        "timespan": f"{days}d",
                        "sort": "datedesc",
                    },
                    interval=5.5,  # GDELT asks for at most one request every 5 seconds
                    timeout=40,
                ).json()
            except Exception:
                body = None  # GDELT sometimes answers non-JSON under load; the company is retried next week
            for art in (body or {}).get("articles", []):
                title = (art.get("title") or "").strip()
                if not title or not headline_mentions(title, phrase):
                    continue
                seen = art.get("seendate") or ""
                when = datetime.strptime(seen[:8], "%Y%m%d").date().isoformat() if seen[:8].isdigit() else ""
                src = art.get("domain") or ""
                signals.append(("news", f"{title} ({src}{', ' + when if when else ''})", art.get("url")))
            if signals:
                yield CompanyRecord(
                    source="news", url=None, name=name, website=website, confidence=0.7, signals=signals[:10]
                )

    def _hacker_news(self, phrase: str, days: int) -> list[tuple[str, str, str | None]]:
        """Hacker News stories naming an AI startup (Algolia HN Search API, free, no key)."""
        since = int((utcnow() - timedelta(days=days)).timestamp())
        try:
            body = self.http.get(
                HN_API,
                params={"query": f'"{phrase}"', "tags": "story", "numericFilters": f"created_at_i>{since}",
                        "hitsPerPage": 10},
                interval=1.0, timeout=30,
            ).json()  # fmt: skip
        except Exception:
            return []
        out = []
        for hit in body.get("hits", []):
            title = (hit.get("title") or "").strip()
            if title and headline_mentions(title, phrase):
                link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                out.append(("news", f"{title} (Hacker News, {(hit.get('created_at') or '')[:10]})", link))
        return out

    def mark_checked(self) -> None:
        if self.checked_ids:
            with self.db.session() as s:
                s.execute(update(Company).where(Company.id.in_(self.checked_ids)).values(last_news_at=utcnow()))
