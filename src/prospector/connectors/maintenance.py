"""Maintenance jobs that run through the same queue as discovery, so they show in run history.

A maintenance connector implements run_maintenance(params) -> (found, updated, message) instead of
yielding new company records.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from ..models import Company, utcnow
from .base import Connector, ParamSpec


class VerifyConnector(Connector):
    """Re-check DNS, contacts and scores for records that have not been verified recently."""

    key = "verify"
    name = "Re-verify records"
    description = "Re-checks domains, mail servers, contacts and scores on records not verified recently."
    licence = "Internal"
    maintenance = True
    needs_db = True
    params = [
        ParamSpec("older_than_days", "Not verified for (days)", "int", False, default=7),
        ParamSpec("limit", "Max companies", "int", False, default=300),
    ]

    def __init__(self, http, api_key=None, db=None, settings=None) -> None:
        super().__init__(http, api_key)
        self.db = db

    def company_ids(self, params: dict[str, Any]) -> list[int]:
        cutoff = utcnow() - timedelta(days=int(params.get("older_than_days") or 7))
        with self.db.session() as s:
            return list(
                s.scalars(
                    select(Company.id)
                    .where(or_(Company.last_verified_at.is_(None), Company.last_verified_at < cutoff))
                    .order_by(Company.last_verified_at.is_not(None), Company.last_verified_at, Company.id)
                    .limit(int(params.get("limit") or 300))
                )
            )

    def run_maintenance(self, params: dict[str, Any]) -> tuple[int, int, str | None]:
        from ..discovery import verify_many  # late import: discovery imports the connector registry

        ids = self.company_ids(params)
        verify_many(self.db, ids)
        return len(ids), len(ids), None


class ArchiveConnector(Connector):
    """Attach the nearest existing Internet Archive copy to each page receipt (lookup only, nothing is archived)."""

    key = "archive"
    name = "Archive receipts"
    description = "Links each page receipt to its nearest existing copy in the Internet Archive's Wayback Machine."
    licence = "Internet Archive availability API (lookup only)"
    maintenance = True
    needs_db = True
    params = [ParamSpec("limit", "Max receipts", "int", False, default=100)]

    def __init__(self, http, api_key=None, db=None, settings=None) -> None:
        super().__init__(http, api_key)
        self.db = db

    def run_maintenance(self, params: dict[str, Any]) -> tuple[int, int, str | None]:
        from ..models import Evidence

        with self.db.session() as s:
            rows = [
                (e.id, e.url, e.fetched_at)
                for e in s.scalars(
                    select(Evidence)
                    .where(Evidence.kind == "page", Evidence.archive_checked_at.is_(None))
                    .order_by(Evidence.fetched_at.desc())
                    .limit(int(params.get("limit") or 100))
                )
            ]
        found = 0
        for eid, url, fetched_at in rows:
            snapshot = None
            try:
                body = self.http.get(
                    "https://archive.org/wayback/available",
                    params={"url": url, "timestamp": fetched_at.strftime("%Y%m%d%H%M%S")},
                    interval=1.0,
                    timeout=30,
                ).json()
                closest = (body.get("archived_snapshots") or {}).get("closest") or {}
                if closest.get("available") and str(closest.get("status")) == "200":
                    snapshot = closest.get("url")
            except Exception:
                pass  # mark it checked anyway, so one failing URL never blocks the queue; new receipts get looked up
            with self.db.session() as s:
                e = s.get(Evidence, eid)
                if e is not None:
                    e.archive_url, e.archive_checked_at = snapshot, utcnow()
            found += bool(snapshot)
        return len(rows), found, f"{found} of {len(rows)} receipts have an archived copy"


class OutreachTickConnector(Connector):
    """Run the outreach engine's due steps: drafts for approval, scheduled emails, LinkedIn tasks, replies."""

    key = "outreach"
    name = "Process outreach"
    description = (
        "Runs due outreach steps in the engine: drafts for approval, scheduled emails (dry run until "
        "switched off), the LinkedIn task list and, when mailboxes are configured, replies."
    )
    licence = "Internal"
    maintenance = True
    needs_db = True
    params: list[ParamSpec] = []

    def __init__(self, http, api_key=None, db=None, settings=None) -> None:
        super().__init__(http, api_key)
        self.settings = settings

    def run_maintenance(self, params: dict[str, Any]) -> tuple[int, int, str | None]:
        from ..outreach import get_link

        link = get_link(self.settings)
        if link is None:
            raise RuntimeError("The outreach engine is not configured (PROSPECTOR_ENGINE_CONFIG)")
        r = link.tick()
        sent = (r.get("flush") or {}).get("sent", 0)
        return (
            r["steps"],
            sent,
            f"{r['steps']} steps processed, {sent} emails sent" + (" (paused)" if r.get("paused") else ""),
        )
