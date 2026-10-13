"""Prospector tables. The engine keeps its own tables (leads, messages, replies...); these are the team's.

All datetimes are naive UTC, as in the engine.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Role:
    ADMIN = "admin"  # everything, including users and settings
    SDR = "sdr"  # work the plan, log calls, tick the routine, approve drafts
    VIEWER = "viewer"  # read only
    ALL = (ADMIN, SDR, VIEWER)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default=Role.SDR)
    password_hash: Mapped[str] = mapped_column(String(300))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class PlanWeek(Base):
    __tablename__ = "plan_weeks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # 1..4
    label: Mapped[str] = mapped_column(String(16))  # W1
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    focus: Mapped[str] = mapped_column(Text, default="")
    # Weekly numeric targets, e.g. {"linkedin_requests": 75, "emails_per_inbox_day": [5, 10]}
    targets: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class Meta(Base):
    """Key/value facts about the app, e.g. which plan file was synced and when."""

    __tablename__ = "meta"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text)


class PlanTask(Base):
    __tablename__ = "plan_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Stable key from the markdown (week:track:slug-of-title); team state is matched to it on re-sync.
    key: Mapped[str] = mapped_column(String(200), unique=True)
    week_id: Mapped[int] = mapped_column(ForeignKey("plan_weeks.id"), index=True)
    track: Mapped[str] = mapped_column(String(16))  # build | outreach | content
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)  # no longer in the markdown
    status: Mapped[str] = mapped_column(String(16), default="todo")  # todo | doing | done | blocked
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    notes: Mapped[str | None] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class KpiDef(Base):
    """A KPI with its target and kill/fix threshold (09 doc). Actuals are computed or entered manually."""

    __tablename__ = "kpi_defs"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(16))  # pct | hours | count
    direction: Mapped[str] = mapped_column(String(8))  # higher | lower (is better)
    target: Mapped[float] = mapped_column(Float)
    threshold: Mapped[float | None] = mapped_column(Float)  # crossing it triggers the action
    min_sample: Mapped[int] = mapped_column(Integer, default=0)  # e.g. "after 200 sends"
    action: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(16), default="engine")  # engine | manual | calls
    raw_target: Mapped[str] = mapped_column(Text, default="")  # verbatim from the markdown
    raw_threshold: Mapped[str] = mapped_column(Text, default="")
    sort: Mapped[int] = mapped_column(Integer, default=0)


class KpiEntry(Base):
    """Manual readings for KPIs the app cannot measure itself (e.g. Google Postmaster spam rate)."""

    __tablename__ = "kpi_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(ForeignKey("kpi_defs.key"), index=True)
    value: Mapped[float] = mapped_column(Float)
    on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class FunnelStage(Base):
    """Planning assumptions from the 09 funnel maths. Actuals come from the engine and the calls log."""

    __tablename__ = "funnel_stages"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    assumption: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(200))
    plan_low: Mapped[float] = mapped_column(Float)
    plan_high: Mapped[float] = mapped_column(Float)
    is_assumption: Mapped[bool] = mapped_column(Boolean, default=False)  # "my assumption" in the doc
    raw: Mapped[str] = mapped_column(Text, default="")  # the "30 days" cell verbatim
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Call(Base):
    """Discovery calls and their outcome, logged by the team."""

    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str | None] = mapped_column(String(32), index=True)  # engine lead id
    company: Mapped[str] = mapped_column(String(300))
    contact: Mapped[str | None] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(16))  # linkedin | email | event | referral | other
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime)
    held: Mapped[bool] = mapped_column(Boolean, default=False)
    outcome: Mapped[str] = mapped_column(String(16), default="pending")  # pending | pilot | no | later
    package: Mapped[str | None] = mapped_column(String(8))  # P01..P12
    value_usd: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RoutineItem(Base):
    __tablename__ = "routine_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    minutes: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    detail: Mapped[str] = mapped_column(Text, default="")
    live_counter: Mapped[str | None] = mapped_column(String(48))  # which live count to show next to it
    sort: Mapped[int] = mapped_column(Integer, default=0)


class RoutineCheck(Base):
    __tablename__ = "routine_checks"
    __table_args__ = (UniqueConstraint("item_id", "user_id", "on"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("routine_items.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    on: Mapped[date] = mapped_column(Date)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100))
    country: Mapped[str] = mapped_column(String(8))
    start: Mapped[date] = mapped_column(Date)
    end: Mapped[date] = mapped_column(Date)
    segments: Mapped[list[str] | None] = mapped_column(JSON)  # sequences it suits
    status: Mapped[str] = mapped_column(String(16), default="watch")  # watch | prepare | outreach | attending | done
    notes: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)  # no longer in the markdown


# ---- Prospect database ---------------------------------------------------------------------------------
class CompanyStatus:
    NEW = "new"
    READY = "ready"  # reviewed, cleared for outreach
    REJECTED = "rejected"
    IN_OUTREACH = "in_outreach"
    ALL = (NEW, READY, REJECTED, IN_OUTREACH)


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    name_key: Mapped[str] = mapped_column(String(300), index=True)  # normalized for matching
    domain: Mapped[str | None] = mapped_column(String(253), unique=True)  # registrable domain
    website: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    city: Mapped[str | None] = mapped_column(String(120), index=True)
    address: Mapped[str | None] = mapped_column(Text)
    segment: Mapped[str | None] = mapped_column(String(48), index=True)
    phone: Mapped[str | None] = mapped_column(String(32))
    general_email: Mapped[str | None] = mapped_column(String(320))
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    socials: Mapped[dict[str, str] | None] = mapped_column(JSON)  # {"linkedin": url, "x": url, ...}
    tech: Mapped[list[str] | None] = mapped_column(JSON)  # e.g. ["WordPress", "HubSpot"]
    email_provider: Mapped[str | None] = mapped_column(String(48))  # Microsoft 365 | Google Workspace | ...
    email_security: Mapped[str | None] = mapped_column(String(32))  # DMARC policy: reject | quarantine | none | missing
    domain_created_on: Mapped[date | None] = mapped_column(Date)  # from public RDAP registration data
    employees: Mapped[int | None] = mapped_column(Integer)  # when a source states it (e.g. Wikidata)
    founded: Mapped[int | None] = mapped_column(Integer)  # year
    careers_url: Mapped[str | None] = mapped_column(Text)
    ats: Mapped[dict[str, str] | None] = mapped_column(JSON)  # public job board: {"provider": ..., "token": ...}
    lei: Mapped[str | None] = mapped_column(String(20), unique=True)
    company_number: Mapped[str | None] = mapped_column(String(32), index=True)  # e.g. Companies House
    registry_status: Mapped[str | None] = mapped_column(String(32))
    size_band: Mapped[str | None] = mapped_column(String(32))
    fit_score: Mapped[int | None] = mapped_column(Integer, index=True)
    quality_score: Mapped[int | None] = mapped_column(Integer, index=True)
    # golden | verified | partial | raw (see verify.tier_of): golden = ready for a salesperson today
    tier: Mapped[str | None] = mapped_column(String(12), index=True)
    last_news_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(16), default=CompanyStatus.NEW, index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    engine_lead_id: Mapped[str | None] = mapped_column(String(32))  # set when promoted to outreach
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Person(Base):
    __tablename__ = "people"
    __table_args__ = (UniqueConstraint("company_id", "name_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str | None] = mapped_column(String(300))
    seniority: Mapped[str | None] = mapped_column(String(24), index=True)  # owner | c_level | partner | director | ...
    email: Mapped[str | None] = mapped_column(String(320), index=True)
    email_status: Mapped[str | None] = mapped_column(String(24))  # published | role | unverified | invalid_domain
    phone: Mapped[str | None] = mapped_column(String(32))
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Evidence(Base):
    """A receipt: exactly what a source returned, when, and its SHA-256 fingerprint.

    For a web page the hash is of the HTML received; for an API it is of the record's canonical JSON. If the
    source later changes or disappears, the hash and timestamp still show what was seen. archive_url points to
    the nearest existing Internet Archive copy when one exists (looked up, never created by Exora).
    """

    __tablename__ = "evidence"
    __table_args__ = (UniqueConstraint("url", "sha256"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(1000))
    kind: Mapped[str] = mapped_column(String(8))  # page | api | file
    source: Mapped[str] = mapped_column(String(48))
    sha256: Mapped[str] = mapped_column(String(64))
    title: Mapped[str | None] = mapped_column(String(500))
    size: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    archive_url: Mapped[str | None] = mapped_column(Text)
    archive_checked_at: Mapped[datetime | None] = mapped_column(DateTime)


class Observation(Base):
    """One value for one field, seen at one URL, from one source, at one time. The provenance trail."""

    __tablename__ = "observations"
    __table_args__ = (Index("ix_observations_lookup", "entity_type", "entity_id", "field", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(16))  # company | person
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    field: Mapped[str] = mapped_column(String(48))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(48), index=True)  # osm | gleif | crawler | seed-sheet ...
    url: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id"), index=True)
    evidence_id: Mapped[int | None] = mapped_column(ForeignKey("evidence.id", ondelete="SET NULL"), index=True)
    excerpt: Mapped[str | None] = mapped_column(Text)  # the words around the value on the source page


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (Index("ix_signals_company_kind", "company_id", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    # fit (research note) | site_mention (website mentions AI, ISO 27001, ...) | hiring | funding | news
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    on: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(48))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Run(Base):
    """One execution of a connector or crawl."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(48), index=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    found: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    automation_id: Mapped[int | None] = mapped_column(ForeignKey("automations.id", ondelete="SET NULL"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Automation(Base):
    """A recurring job: run a source every N minutes, optionally rotating one parameter through a list
    (e.g. city = London, Manchester, Leeds, ...) so the database keeps growing without hammering one query."""

    __tablename__ = "automations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    source: Mapped[str] = mapped_column(String(48))
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rotate_field: Mapped[str | None] = mapped_column(String(48))
    rotate_values: Mapped[list[str] | None] = mapped_column(JSON)
    rotate_index: Mapped[int] = mapped_column(Integer, default=0)
    cadence_minutes: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_run_id: Mapped[int | None] = mapped_column(Integer)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class DailyMetric(Base):
    """Daily snapshot of the database, for growth and quality trends."""

    __tablename__ = "daily_metrics"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    companies: Mapped[int] = mapped_column(Integer, default=0)
    people: Mapped[int] = mapped_column(Integer, default=0)
    with_website: Mapped[int] = mapped_column(Integer, default=0)
    crawled: Mapped[int] = mapped_column(Integer, default=0)
    with_email: Mapped[int] = mapped_column(Integer, default=0)
    with_phone: Mapped[int] = mapped_column(Integer, default=0)
    with_decision_maker: Mapped[int] = mapped_column(Integer, default=0)
    high_quality: Mapped[int] = mapped_column(Integer, default=0)  # quality >= 70
    avg_quality: Mapped[float | None] = mapped_column(Float)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    run_errors: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Exclusion(Base):
    """Companies the team removed and does not want rediscovered (by domain or by name within a country)."""

    __tablename__ = "exclusions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    value: Mapped[str] = mapped_column(String(320), unique=True)  # "domain:acme.com" | "name:GB:acme law"
    label: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TaskStatus:
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    CANCELLED = "cancelled"
    ACTIVE = (OPEN, IN_PROGRESS)
    ALL = (OPEN, IN_PROGRESS, DONE, CANCELLED)


class Task(Base):
    """Work assigned to a team member: research a company, call, LinkedIn, follow up..."""

    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_assignee_status", "assignee_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(
        String(16), default="other"
    )  # research|call|email|linkedin|meeting|follow_up|other
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id", ondelete="SET NULL"))
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    due_on: Mapped[date | None] = mapped_column(Date, index=True)
    priority: Mapped[str] = mapped_column(String(8), default="normal")  # low | normal | high
    status: Mapped[str] = mapped_column(String(16), default=TaskStatus.OPEN, index=True)
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Note(Base):
    """Team notes on a company (CRM activity)."""

    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class QualityCheck(Base):
    __tablename__ = "quality_checks"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "check"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[int] = mapped_column(Integer, index=True)
    check: Mapped[str] = mapped_column(String(48))
    passed: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ContactCheck(Base):
    """One verdict for one contact detail, from one source, at one time (the app's provenance rule).

    Emails are judged with RFC parsing plus public DNS (MX, null-MX, reject-all SPF); phone numbers with
    the offline number plans in `phonenumbers`. These are data-quality rows only: no verdict is a marketing
    action, and the phone number is never dialled or texted by the campaign.
    """

    __tablename__ = "contact_checks"
    __table_args__ = (UniqueConstraint("kind", "value", "source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)  # email | phone
    value: Mapped[str] = mapped_column(String(320))  # the address or number exactly as stored
    verdict: Mapped[str] = mapped_column(String(24))  # valid | possible | risky | invalid | unknown | rejected ...
    reason: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(48))  # email-validator+dns | phonenumbers | smtp-rcpt
    checked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class Audit(Base):
    __tablename__ = "audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
