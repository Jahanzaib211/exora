"""24/7 scheduler: turns automations into queued runs, rotates their parameters, and snapshots daily metrics.

Rules that keep it safe to leave running:
- an automation never has more than one run queued or running (no pile-up after sleep or a slow source);
- rotation walks one parameter through a list (city, keyword...), so each external source sees one modest
  query per cadence instead of bursts;
- missed ticks (laptop asleep) are not replayed: the next run is scheduled from "now".
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from .db import Database
from .discovery import RunError, enqueue, heartbeat, reap_stale_runs
from .models import Automation, Company, DailyMetric, Person, Run, utcnow
from .power import prevent_sleep

log = logging.getLogger(__name__)
TICK_SECONDS = 30
SNAPSHOT_MINUTES = 60

UK_CITIES = [
    "London",
    "Manchester",
    "Birmingham",
    "Leeds",
    "Liverpool",
    "Bristol",
    "Glasgow",
    "Edinburgh",
    "Cardiff",
    "Sheffield",
    "Nottingham",
    "Newcastle upon Tyne",
    "Leicester",
    "Southampton",
    "Reading",
    "Cambridge",
    "Oxford",
    "Belfast",
    "Milton Keynes",
    "Brighton",
]
US_CITIES = [
    "New York",
    "Chicago",
    "Los Angeles",
    "Houston",
    "Dallas",
    "Boston",
    "Philadelphia",
    "Atlanta",
    "Miami",
    "San Francisco",
    "Seattle",
    "Denver",
    "Phoenix",
    "Austin",
    "Washington",
]
GULF_CITIES = ["Riyadh", "Jeddah", "Dammam", "Khobar", "Mecca", "Medina", "Dubai", "Abu Dhabi", "Doha"]
KSA_KEYWORDS = ["law", "legal", "advocates", "accounting", "audit", "clinic", "medical", "hospital", "dental",
                "technology", "information technology", "consulting"]  # fmt: skip

# name, source, params, rotate field, rotate values, cadence (minutes), enabled
BUILTINS: list[tuple[str, str, dict[str, Any], str | None, list[str] | None, int, bool]] = [
    ("Grow · UK law firms (map)", "osm", {"segment": "Law", "country": "GB", "limit": 500}, "city", UK_CITIES, 60,
     True),
    ("Grow · UK accountants (map)", "osm", {"segment": "CPA", "country": "GB", "limit": 500}, "city", UK_CITIES, 60,
     True),
    ("Grow · US law firms (map)", "osm", {"segment": "Law", "country": "US", "limit": 500}, "city", US_CITIES, 90,
     True),
    ("Grow · US accountants (map)", "osm", {"segment": "CPA", "country": "US", "limit": 500}, "city", US_CITIES, 90,
     True),
    ("Grow · Gulf clinics (map)", "osm", {"segment": "Healthcare", "limit": 500}, "city", GULF_CITIES, 120, True),
    ("Grow · KSA registered entities", "gleif", {"country": "SA", "limit": 200}, "query", KSA_KEYWORDS, 120, True),
    ("Grow · UK firms (Wikidata)", "wikidata", {"country": "GB", "limit": 500}, "segment",
     ["Law", "CPA", "Healthcare"], 24 * 60, True),
    ("Grow · UK companies (Companies House)", "companies-house", {"segment": "Law", "limit": 200}, "location",
     UK_CITIES, 60, False),  # needs an API key
    ("Enrich · crawl websites", "website", {"limit": 30, "recrawl_days": 30}, None, None, 20, True),
    ("Signals · job boards", "jobs", {"limit": 50}, None, None, 6 * 60, True),
    ("Signals · news mentions", "news", {"min_fit": 50, "limit": 40, "days": 90}, None, None, 12 * 60, True),
    ("Quality · re-verify records", "verify", {"older_than_days": 7, "limit": 300}, None, None, 60, True),
    ("Receipts · link archived copies", "archive", {"limit": 100}, None, None, 6 * 60, True),
    ("Outreach · process due steps", "outreach", {}, None, None, 15, True),  # enabled only with an engine
]  # fmt: skip


def seed_automations(db: Database, outreach_enabled: bool = False) -> int:
    """Create the built-in automations once (matched by name). Never overwrites user changes."""
    added = 0
    with db.session() as s:
        have = set(s.scalars(select(Automation.name)))
        for i, (name, source, params, field, values, cadence, enabled) in enumerate(BUILTINS):
            if name in have:
                continue
            if source == "outreach":
                enabled = enabled and outreach_enabled
            s.add(
                Automation(
                    name=name,
                    source=source,
                    params=params,
                    rotate_field=field,
                    rotate_values=values,
                    cadence_minutes=cadence,
                    enabled=enabled,
                    builtin=True,
                    # stagger first runs so a fresh install does not fire everything at once
                    next_run_at=utcnow() + timedelta(minutes=2 + 3 * i) if enabled else None,
                )
            )
            added += 1
    return added


def next_params(a: Automation) -> tuple[dict[str, Any], int]:
    """Params for this tick and the rotation index to store for the next one."""
    params = dict(a.params or {})
    values = [v for v in (a.rotate_values or []) if str(v).strip()]
    if a.rotate_field and values:
        i = (a.rotate_index or 0) % len(values)
        params[a.rotate_field] = values[i]
        return params, (i + 1) % len(values)
    return params, a.rotate_index or 0


def has_active_run(db: Database, automation_id: int) -> bool:
    with db.session() as s:
        return (
            s.scalar(
                select(Run.id).where(Run.automation_id == automation_id, Run.status.in_(["queued", "running"])).limit(1)
            )
            is not None
        )


def trigger(db: Database, automation_id: int, user_id: int | None = None, now: datetime | None = None) -> int | None:
    """Queue one run for an automation now (scheduler tick or "Run now"). Returns the run id, or None if one
    is already queued/running or the parameters are invalid (recorded on the automation)."""
    now = now or utcnow()
    if has_active_run(db, automation_id):
        return None
    with db.session() as s:
        a = s.get(Automation, automation_id)
        if a is None:
            return None
        params, next_index = next_params(a)
        source = a.source
    try:
        run_id = enqueue(db, source, params, user_id, automation_id=automation_id)
    except RunError as exc:
        log.warning("automation %s has invalid parameters: %s", automation_id, exc)
        with db.session() as s:
            a = s.get(Automation, automation_id)
            a.enabled = False  # stop retrying a broken configuration every tick
            a.next_run_at = None
        return None
    with db.session() as s:
        a = s.get(Automation, automation_id)
        a.rotate_index = next_index
        a.last_run_at, a.last_run_id = now, run_id
        a.next_run_at = now + timedelta(minutes=max(5, a.cadence_minutes))
    return run_id


def due_automations(db: Database, now: datetime) -> list[int]:
    with db.session() as s:
        return list(
            s.scalars(
                select(Automation.id).where(
                    Automation.enabled.is_(True), Automation.next_run_at.is_not(None), Automation.next_run_at <= now
                )
            )
        )


def tick(db: Database, now: datetime | None = None) -> list[int]:
    now = now or utcnow()
    queued = []
    for aid in due_automations(db, now):
        run_id = trigger(db, aid, now=now)
        if run_id:
            queued.append(run_id)
        else:
            # an earlier run is still active: try again next cadence, do not stack runs
            with db.session() as s:
                a = s.get(Automation, aid)
                if a and a.enabled and a.next_run_at and a.next_run_at <= now:
                    a.next_run_at = now + timedelta(minutes=max(5, min(a.cadence_minutes, 15)))
    return queued


def snapshot(db: Database, day: date | None = None) -> DailyMetric:
    """Upsert today's metrics row (called hourly; the last call of the day is the day's value)."""
    day = day or utcnow().date()
    start = datetime.combine(day, datetime.min.time())
    with db.session() as s:
        row = s.get(DailyMetric, day) or DailyMetric(day=day)
        row.companies = s.scalar(select(func.count(Company.id))) or 0
        row.people = s.scalar(select(func.count(Person.id))) or 0
        row.with_website = s.scalar(select(func.count()).where(Company.website.is_not(None))) or 0
        row.crawled = s.scalar(select(func.count()).where(Company.last_crawled_at.is_not(None))) or 0
        has_email = Company.id.in_(select(Person.company_id).where(Person.email.is_not(None)))
        row.with_email = s.scalar(select(func.count()).where((Company.general_email.is_not(None)) | has_email)) or 0
        row.with_phone = s.scalar(select(func.count()).where(Company.phone.is_not(None))) or 0
        row.with_decision_maker = (
            s.scalar(
                select(func.count(func.distinct(Person.company_id))).where(
                    Person.seniority.in_(["owner", "c_level", "partner", "director"])
                )
            )
            or 0
        )
        row.high_quality = s.scalar(select(func.count()).where(Company.quality_score >= 70)) or 0
        avg = s.scalar(select(func.avg(Company.quality_score)).where(Company.quality_score.is_not(None)))
        row.avg_quality = round(float(avg), 1) if avg is not None else None
        row.runs = s.scalar(select(func.count()).where(Run.created_at >= start)) or 0
        row.run_errors = s.scalar(select(func.count()).where(Run.created_at >= start, Run.status == "failed")) or 0
        s.add(row)
        return row


class Scheduler:
    """One thread: automations every 30 s, metrics hourly, stale-run reaper, heartbeat."""

    def __init__(self, db: Database, keep_awake: bool = False, outreach_enabled: bool = False) -> None:
        self.db = db
        self.keep_awake = keep_awake
        self.outreach_enabled = outreach_enabled
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_snapshot: datetime | None = None

    def start(self) -> None:
        seed_automations(self.db, outreach_enabled=self.outreach_enabled)
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)

    def _loop(self) -> None:
        if self.keep_awake:
            prevent_sleep()  # held by this thread for the life of the scheduler
        while not self._stop.is_set():
            try:
                now = utcnow()
                tick(self.db, now)
                reap_stale_runs(self.db)
                if not self._last_snapshot or now - self._last_snapshot >= timedelta(minutes=SNAPSHOT_MINUTES):
                    snapshot(self.db)
                    self._last_snapshot = now
                heartbeat(self.db, "scheduler")
            except Exception:
                log.exception("scheduler tick failed")
            self._stop.wait(TICK_SECONDS)
