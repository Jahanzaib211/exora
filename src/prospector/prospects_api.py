"""REST endpoints: prospect database (full CRUD), discovery runs, automations, engine status, quality, exports.

Every write bumps the aggregate cache and is written to the audit log. Manual edits are recorded as
observations from source "manual", so the provenance trail shows what the team changed and when.
"""

import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import String, and_, case, cast, delete, func, or_, select, update
from sqlalchemy.orm import Session

from . import cache
from .connectors import CONNECTORS
from .db import Database, audit
from .discovery import RunError, enqueue, validate_params
from .exports import (
    COMPANY_COLUMNS,
    PEOPLE_COLUMNS,
    company_docx,
    company_rows,
    people_rows,
    readme_rows,
    to_csv,
    to_xlsx,
)
from .fit import evaluate_fit
from .models import (
    Automation,
    Company,
    CompanyStatus,
    DailyMetric,
    Evidence,
    Exclusion,
    Meta,
    Note,
    Observation,
    Person,
    QualityCheck,
    Run,
    Signal,
    Task,
    User,
    utcnow,
)
from .normalize import country_code, domain_of, email_kind, name_key, person_key, phone_e164, seniority_of, website_of
from .resolve import CompanyRecord, _observe, exclusion_keys, find_company
from .scheduler import next_params, snapshot, trigger
from .verify import CHECK_BY_KEY, CHECKS, TIER_RULES, TIERS, DnsCache, verify_company

TIER_ORDER = case({"golden": 0, "verified": 1, "partial": 2, "raw": 3}, value=Company.tier, else_=4)
SORTS = {
    "best": (TIER_ORDER, Company.fit_score.desc().nullslast(), Company.quality_score.desc().nullslast()),
    "fit": (Company.fit_score.desc().nullslast(), Company.quality_score.desc().nullslast()),
    "quality": (Company.quality_score.desc().nullslast(), Company.fit_score.desc().nullslast()),
    "name": (Company.name.asc(),),
    "newest": (Company.created_at.desc(),),
    "updated": (Company.updated_at.desc(),),
}
SENIOR = ("owner", "c_level", "partner", "director")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def company_row(c: Company, people: int = 0) -> dict[str, Any]:
    return {
        "id": c.id,
        "name": c.name,
        "domain": c.domain,
        "website": c.website,
        "country": c.country,
        "city": c.city,
        "segment": c.segment,
        "phone": c.phone,
        "general_email": c.general_email,
        "linkedin_url": c.linkedin_url,
        "email_provider": c.email_provider,
        "tech": c.tech or [],
        "quality_score": c.quality_score,
        "fit_score": c.fit_score,
        "tier": c.tier,
        "status": c.status,
        "owner_id": c.owner_id,
        "people": people,
        "lei": c.lei,
        "registry_status": c.registry_status,
        "last_crawled_at": _iso(c.last_crawled_at),
        "updated_at": _iso(c.updated_at),
        "created_at": _iso(c.created_at),
    }


def person_row(p: Person, c: Company | None = None) -> dict[str, Any]:
    return {
        "id": p.id,
        "company_id": p.company_id,
        "company": c.name if c else None,
        "company_domain": c.domain if c else None,
        "country": c.country if c else None,
        "segment": c.segment if c else None,
        "name": p.name,
        "title": p.title,
        "seniority": p.seniority,
        "email": p.email,
        "email_status": p.email_status,
        "phone": p.phone,
        "linkedin_url": p.linkedin_url,
        "updated_at": _iso(p.updated_at),
    }


def run_row(r: Run, automation: str | None = None) -> dict[str, Any]:
    return {
        "id": r.id,
        "source": r.source,
        "source_name": CONNECTORS[r.source].name if r.source in CONNECTORS else r.source,
        "params": {k: v for k, v in (r.params or {}).items() if k != "company_ids"}
        | ({"companies": len(r.params["company_ids"])} if (r.params or {}).get("company_ids") else {}),
        "status": r.status,
        "found": r.found,
        "created": r.created,
        "updated": r.updated,
        "errors": r.errors,
        "message": r.message,
        "automation_id": r.automation_id,
        "automation": automation,
        "started_at": _iso(r.started_at),
        "finished_at": _iso(r.finished_at),
        "created_at": _iso(r.created_at),
    }


def _provenance(s: Session, etype: str, eid: int) -> list[dict[str, Any]]:
    rows = s.execute(
        select(Observation, Evidence)
        .outerjoin(Evidence, Evidence.id == Observation.evidence_id)
        .where(Observation.entity_type == etype, Observation.entity_id == eid)
        .order_by(Observation.field, Observation.retrieved_at.desc())
    ).all()
    return [
        {
            "field": o.field,
            "value": o.value,
            "source": o.source,
            "url": o.url,
            "retrieved_at": o.retrieved_at.isoformat(),
            "confidence": o.confidence,
            "excerpt": o.excerpt,
            "receipt": (
                {
                    "id": e.id,
                    "url": e.url,
                    "kind": e.kind,
                    "sha256": e.sha256,
                    "title": e.title,
                    "size": e.size,
                    "fetched_at": e.fetched_at.isoformat(),
                    "archive_url": e.archive_url,
                }
                if e
                else None
            ),
        }
        for o, e in rows
    ]


# ---- company filters (shared by list, export and bulk-by-filter) -------------------------------------
class CompanyFilters(BaseModel):
    q: str | None = None
    tier: list[str] | None = None
    segment: list[str] | None = None
    country: list[str] | None = None
    status: list[str] | None = None
    email_provider: list[str] | None = None
    tech: list[str] | None = None
    signal: list[str] | None = None
    source: list[str] | None = None
    owner_id: list[int] | None = None
    min_quality: int | None = None
    min_fit: int | None = None
    has_email: bool | None = None
    has_phone: bool | None = None
    has_people: bool | None = None
    has_decision_maker: bool | None = None


def company_filters(
    q: str | None = Query(None, max_length=200),
    tier: list[str] | None = Query(None),
    segment: list[str] | None = Query(None),
    country: list[str] | None = Query(None),
    status: list[str] | None = Query(None),
    email_provider: list[str] | None = Query(None),
    tech: list[str] | None = Query(None),
    signal: list[str] | None = Query(None),
    source: list[str] | None = Query(None),
    owner_id: list[int] | None = Query(None),
    min_quality: int | None = Query(None, ge=0, le=100),
    min_fit: int | None = Query(None, ge=0, le=100),
    has_email: bool | None = None,
    has_phone: bool | None = None,
    has_people: bool | None = None,
    has_decision_maker: bool | None = None,
) -> CompanyFilters:
    return CompanyFilters(**{k: v for k, v in locals().items()})


def company_where(f: CompanyFilters):
    conds = []
    if f.q:
        t = _like(f.q.strip())
        conds.append(
            or_(
                Company.name.ilike(t, escape="\\"),
                Company.domain.ilike(t, escape="\\"),
                Company.city.ilike(t, escape="\\"),
            )
        )
    if f.tier:
        conds.append(Company.tier.in_(f.tier))
    if f.segment:
        conds.append(Company.segment.in_(f.segment))
    if f.country:
        conds.append(Company.country.in_([c.upper() for c in f.country]))
    if f.status:
        conds.append(Company.status.in_(f.status))
    if f.email_provider:
        conds.append(Company.email_provider.in_(f.email_provider))
    if f.tech:
        conds.append(or_(*[cast(Company.tech, String).like(_like(f'"{t}"'), escape="\\") for t in f.tech]))
    if f.signal:
        kinds = [x for x in f.signal if x == "hiring"]
        titles = [x for x in f.signal if x != "hiring"]
        sig = select(Signal.company_id).where(
            or_(Signal.kind.in_(kinds) if kinds else False, Signal.title.in_(titles) if titles else False)
        )
        conds.append(Company.id.in_(sig))
    if f.source:
        conds.append(
            Company.id.in_(
                select(Observation.entity_id).where(
                    Observation.entity_type == "company", Observation.source.in_(f.source)
                )
            )
        )
    if f.owner_id:
        conds.append(Company.owner_id.in_(f.owner_id))
    if f.min_quality is not None:
        conds.append(Company.quality_score >= f.min_quality)
    if f.min_fit is not None:
        conds.append(Company.fit_score >= f.min_fit)
    if f.has_email is not None:
        has = or_(
            Company.general_email.is_not(None),
            Company.id.in_(select(Person.company_id).where(Person.email.is_not(None))),
        )
        conds.append(has if f.has_email else ~has)
    if f.has_phone is not None:
        conds.append(Company.phone.is_not(None) if f.has_phone else Company.phone.is_(None))
    if f.has_people is not None:
        sub = Company.id.in_(select(Person.company_id))
        conds.append(sub if f.has_people else ~sub)
    if f.has_decision_maker is not None:
        sub = Company.id.in_(select(Person.company_id).where(Person.seniority.in_(SENIOR)))
        conds.append(sub if f.has_decision_maker else ~sub)
    return and_(*conds) if conds else None


def _company_query(f: CompanyFilters, sort: str):
    q = select(Company)
    where = company_where(f)
    if where is not None:
        q = q.where(where)
    return q, q.order_by(*SORTS[sort], Company.id)


# ---- input models ------------------------------------------------------------------------------------
class CompanyIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    website: str | None = Field(None, max_length=500)
    segment: str | None = Field(None, max_length=48)
    country: str | None = Field(None, max_length=60)
    city: str | None = Field(None, max_length=120)
    address: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=40)
    general_email: str | None = Field(None, max_length=320)
    linkedin_url: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=2000)
    size_band: str | None = Field(None, max_length=32)
    status: str | None = Field(None, pattern="^(new|ready|rejected|in_outreach)$")
    owner_id: int | None = None


class CompanyPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=300)
    website: str | None = Field(None, max_length=500)
    segment: str | None = Field(None, max_length=48)
    country: str | None = Field(None, max_length=60)
    city: str | None = Field(None, max_length=120)
    address: str | None = Field(None, max_length=500)
    phone: str | None = Field(None, max_length=40)
    general_email: str | None = Field(None, max_length=320)
    linkedin_url: str | None = Field(None, max_length=500)
    description: str | None = Field(None, max_length=2000)
    size_band: str | None = Field(None, max_length=32)
    status: str | None = Field(None, pattern="^(new|ready|rejected|in_outreach)$")
    owner_id: int | None = None


class PersonIn(BaseModel):
    company_id: int
    name: str = Field(min_length=3, max_length=200)
    title: str | None = Field(None, max_length=300)
    email: str | None = Field(None, max_length=320)
    phone: str | None = Field(None, max_length=40)
    linkedin_url: str | None = Field(None, max_length=500)


class PersonPatch(BaseModel):
    name: str | None = Field(None, min_length=3, max_length=200)
    title: str | None = Field(None, max_length=300)
    email: str | None = Field(None, max_length=320)
    phone: str | None = Field(None, max_length=40)
    linkedin_url: str | None = Field(None, max_length=500)


class NoteIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=2000)
    action: str = Field(pattern="^(status|owner|enrich|delete)$")
    status: str | None = Field(None, pattern="^(new|ready|rejected)$")
    owner_id: int | None = None
    exclude: bool = False  # with delete: never add these companies again


class RunIn(BaseModel):
    source: str = Field(max_length=48)
    params: dict[str, Any] = Field(default_factory=dict)


class AutomationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: str = Field(max_length=48)
    params: dict[str, Any] = Field(default_factory=dict)
    rotate_field: str | None = Field(None, max_length=48)
    rotate_values: list[str] | None = Field(None, max_length=300)
    cadence_minutes: int = Field(60, ge=5, le=7 * 24 * 60)
    enabled: bool = True


class AutomationPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=120)
    params: dict[str, Any] | None = None
    rotate_field: str | None = Field(None, max_length=48)
    rotate_values: list[str] | None = Field(None, max_length=300)
    cadence_minutes: int | None = Field(None, ge=5, le=7 * 24 * 60)
    enabled: bool | None = None


# ---- write helpers -----------------------------------------------------------------------------------
def _manual(c_name: str) -> CompanyRecord:
    return CompanyRecord(source="manual", url=None, name=c_name, confidence=1.0)


def _clean_email(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    v = value.strip().lower()
    if not EMAIL_RE.match(v):
        raise HTTPException(400, "Email address is not valid")
    return v


def apply_company_changes(s: Session, c: Company, changes: dict[str, Any]) -> list[str]:
    """Normalize and apply manual edits; returns the changed field names."""
    changed: list[str] = []
    if "country" in changes:
        raw = changes["country"]
        cc = country_code(raw) if raw else None
        if raw and not cc:
            raise HTTPException(400, "Country must be a country name or a 2-letter code, e.g. GB")
        if cc != c.country:
            c.country = cc
            changed.append("country")
    if "website" in changes:
        website = website_of(changes["website"]) if changes["website"] else None
        domain = domain_of(website) if website else None
        if changes["website"] and not domain:
            raise HTTPException(400, "Website is not a valid address")
        if domain and domain != c.domain:
            other = s.scalar(select(Company).where(Company.domain == domain, Company.id != c.id))
            if other:
                raise HTTPException(
                    409, {"message": f"{domain} already belongs to {other.name}", "existing_id": other.id}
                )
        if website != c.website:
            c.website, c.domain = website, domain
            changed.append("website")
    if "phone" in changes:
        raw = changes["phone"]
        phone = phone_e164(raw, c.country) if raw else None
        if raw and not phone:
            raise HTTPException(400, "Phone number is not valid (use international format, e.g. +44 161 832 1234)")
        if phone != c.phone:
            c.phone = phone
            changed.append("phone")
    if "general_email" in changes:
        email = _clean_email(changes["general_email"])
        if email != c.general_email:
            c.general_email = email
            changed.append("general_email")
    if "name" in changes and changes["name"]:
        name = " ".join(changes["name"].split())
        if name != c.name:
            c.name, c.name_key = name, name_key(name)
            changed.append("name")
    if changes.get("owner_id") is not None and not s.get(User, changes["owner_id"]):
        raise HTTPException(400, "Unknown team member")  # same rule as the bulk owner action
    for f in ("segment", "city", "address", "description", "size_band", "linkedin_url", "status", "owner_id"):
        if f in changes:
            v = changes[f]
            v = v.strip() if isinstance(v, str) else v
            v = v or None if f != "status" else (v or CompanyStatus.NEW)
            if f == "linkedin_url" and v:
                v = v.split("?")[0].rstrip("/")
            if v != getattr(c, f):
                setattr(c, f, v)
                changed.append(f)
    if changed:
        s.flush()
        rec = _manual(c.name)
        for f in changed:
            if f not in ("status", "owner_id"):
                _observe(s, "company", c.id, f, getattr(c, f), rec, None)
                if f == "website":
                    _observe(s, "company", c.id, "domain", c.domain, rec, None)
    return changed


def delete_companies(s: Session, ids: list[int], exclude: bool, user_id: int) -> int:
    companies = s.scalars(select(Company).where(Company.id.in_(ids))).all()
    if not companies:
        return 0
    cids = [c.id for c in companies]
    if exclude:
        for c in companies:
            for key in exclusion_keys(c.domain, c.name, c.country):
                if not s.scalar(select(Exclusion.id).where(Exclusion.value == key)):
                    s.add(Exclusion(value=key, label=c.name, reason="Deleted by the team", created_by=user_id))
    pids = list(s.scalars(select(Person.id).where(Person.company_id.in_(cids))))
    if pids:
        s.execute(delete(Observation).where(Observation.entity_type == "person", Observation.entity_id.in_(pids)))
    s.execute(delete(Observation).where(Observation.entity_type == "company", Observation.entity_id.in_(cids)))
    s.execute(delete(QualityCheck).where(QualityCheck.entity_type == "company", QualityCheck.entity_id.in_(cids)))
    s.execute(delete(Person).where(Person.company_id.in_(cids)))
    s.execute(delete(Signal).where(Signal.company_id.in_(cids)))
    s.execute(delete(Note).where(Note.company_id.in_(cids)))
    s.execute(delete(Task).where(Task.company_id.in_(cids)))
    s.execute(delete(Company).where(Company.id.in_(cids)))
    return len(cids)


def _rescore(s: Session, c: Company) -> None:
    # rdap=False: a manual edit re-scores instantly; the hourly re-verify job fills in domain age
    verify_company(s, c, DnsCache(rdap=False))


def register(app: FastAPI, db: Database, viewer, sdr, admin, settings, runtime: dict[str, Any]) -> None:
    # ---- companies: read ------------------------------------------------------------------------------
    @app.get("/api/companies")
    def list_companies(
        f: CompanyFilters = Depends(company_filters),
        sort: str = Query("best", pattern="^(best|fit|quality|name|newest|updated)$"),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        user: User = Depends(viewer),
    ) -> dict[str, Any]:
        with db.session() as s:
            base, ordered = _company_query(f, sort)
            total = s.scalar(select(func.count()).select_from(base.subquery()))
            rows = s.scalars(ordered.offset((page - 1) * page_size).limit(page_size)).all()
            counts = dict(
                s.execute(
                    select(Person.company_id, func.count())
                    .where(Person.company_id.in_([c.id for c in rows]))
                    .group_by(Person.company_id)
                ).all()
            )
            return {"items": [company_row(c, counts.get(c.id, 0)) for c in rows], "total": total}

    @app.get("/api/companies/facets")
    def facets(user: User = Depends(viewer)) -> dict[str, Any]:
        def compute() -> dict[str, Any]:
            with db.session() as s:

                def grp(col):
                    return [
                        {"value": v, "count": n}
                        for v, n in s.execute(
                            select(col, func.count())
                            .where(col.is_not(None))
                            .group_by(col)
                            .order_by(func.count().desc())
                        ).all()
                    ]

                tech = Counter()
                for (t,) in s.execute(select(Company.tech).where(Company.tech.is_not(None))):
                    tech.update(t or [])
                signals = [
                    {"value": t, "count": n}
                    for t, n in s.execute(
                        select(Signal.title, func.count(func.distinct(Signal.company_id)))
                        .where(Signal.kind == "site_mention")
                        .group_by(Signal.title)
                        .order_by(func.count(func.distinct(Signal.company_id)).desc())
                    ).all()
                ]
                hiring = s.scalar(select(func.count(func.distinct(Signal.company_id))).where(Signal.kind == "hiring"))
                if hiring:
                    signals.append({"value": "hiring", "count": hiring})
                sources = [
                    {"value": src, "count": n}
                    for src, n in s.execute(
                        select(Observation.source, func.count(func.distinct(Observation.entity_id)))
                        .where(Observation.entity_type == "company")
                        .group_by(Observation.source)
                        .order_by(func.count(func.distinct(Observation.entity_id)).desc())
                    ).all()
                ]
                owners = [
                    {"value": uid, "label": name, "count": n}
                    for uid, name, n in s.execute(
                        select(Company.owner_id, User.name, func.count())
                        .join(User, User.id == Company.owner_id)
                        .group_by(Company.owner_id, User.name)
                    ).all()
                ]
                return {
                    "segment": grp(Company.segment),
                    "country": grp(Company.country),
                    "status": grp(Company.status),
                    "tier": grp(Company.tier),
                    "email_provider": grp(Company.email_provider),
                    "tech": [{"value": k, "count": n} for k, n in tech.most_common(40)],
                    "signal": signals,
                    "source": sources,
                    "owner": owners,
                    "total": s.scalar(select(func.count(Company.id))),
                    "people": s.scalar(select(func.count(Person.id))),
                }

        return cache.cached("facets", 60, compute)

    @app.get("/api/companies/{company_id}")
    def get_company(company_id: int, user: User = Depends(viewer)) -> dict[str, Any]:
        with db.session() as s:
            c = s.get(Company, company_id)
            if c is None:
                raise HTTPException(404, "Company not found")
            people = s.scalars(
                select(Person).where(Person.company_id == c.id).order_by(Person.seniority, Person.name)
            ).all()
            fit, reasons = evaluate_fit(s, c)
            owner = s.get(User, c.owner_id) if c.owner_id else None
            return {
                **company_row(c, len(people)),
                "address": c.address,
                "company_number": c.company_number,
                "size_band": c.size_band,
                "description": c.description,
                "socials": c.socials or {},
                "careers_url": c.careers_url,
                "ats": c.ats,
                "email_security": c.email_security,
                "domain_created_on": c.domain_created_on.isoformat() if c.domain_created_on else None,
                "employees": c.employees,
                "founded": c.founded,
                "tier_rules": TIER_RULES,
                "owner": {"id": owner.id, "name": owner.name} if owner else None,
                "fit_reasons": reasons,
                "last_verified_at": _iso(c.last_verified_at),
                "people_list": [person_row(p) for p in people],
                "signals": [
                    {
                        "id": g.id,
                        "kind": g.kind,
                        "title": g.title,
                        "url": g.url,
                        "source": g.source,
                        "created_at": _iso(g.created_at),
                    }
                    for g in s.scalars(
                        select(Signal).where(Signal.company_id == c.id).order_by(Signal.created_at.desc())
                    )
                ],  # fmt: skip
                "sources": _provenance(s, "company", c.id),
                "checks": [
                    {
                        "check": q.check,
                        "label": CHECK_BY_KEY[q.check].label if q.check in CHECK_BY_KEY else q.check,
                        "passed": q.passed,
                        "detail": q.detail,
                    }
                    for q in s.scalars(
                        select(QualityCheck).where(
                            QualityCheck.entity_type == "company", QualityCheck.entity_id == c.id
                        )
                    )
                ],
                "notes_count": s.scalar(select(func.count(Note.id)).where(Note.company_id == c.id)),
            }

    # ---- companies: write -----------------------------------------------------------------------------
    @app.post("/api/companies", status_code=201)
    def create_company(body: CompanyIn, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            probe = CompanyRecord(source="manual", url=None, name=body.name, website=body.website,
                                  country=body.country).normalized()  # fmt: skip
            existing = find_company(s, probe)
            if existing:
                raise HTTPException(
                    409, {"message": f"{existing.name} is already in the database", "existing_id": existing.id}
                )
            c = Company(name=" ".join(body.name.split()), name_key=name_key(body.name))
            s.add(c)
            s.flush()
            apply_company_changes(s, c, body.model_dump(exclude_unset=True))
            _observe(s, "company", c.id, "name", c.name, _manual(c.name), None)
            _rescore(s, c)
            audit(s, "company_created", user.id, target=f"company:{c.id}", name=c.name)
            cache.bump()
            return company_row(c)

    @app.patch("/api/companies/{company_id}")
    def patch_company(company_id: int, body: CompanyPatch, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            c = s.get(Company, company_id)
            if c is None:
                raise HTTPException(404, "Company not found")
            changed = apply_company_changes(s, c, body.model_dump(exclude_unset=True))
            if changed and set(changed) - {"status", "owner_id"}:
                _rescore(s, c)
            audit(s, "company_updated", user.id, target=f"company:{company_id}", fields=changed)
            cache.bump()
            return company_row(c)

    @app.delete("/api/companies/{company_id}")
    def delete_company(company_id: int, exclude: bool = False, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            c = s.get(Company, company_id)
            if c is None:
                raise HTTPException(404, "Company not found")
            name = c.name
            delete_companies(s, [company_id], exclude, user.id)
            audit(s, "company_deleted", user.id, target=f"company:{company_id}", name=name, exclude=exclude)
        cache.bump()
        return {"deleted": 1}

    @app.post("/api/companies/bulk")
    def bulk(body: BulkIn, user: User = Depends(sdr)) -> dict[str, Any]:
        if body.action == "enrich":
            with db.session() as s:
                ids = list(s.scalars(select(Company.id).where(Company.id.in_(body.ids), Company.website.is_not(None))))
            if not ids:
                raise HTTPException(400, "None of the selected companies has a website to crawl")
            run_ids = [
                enqueue(db, "website", {"company_ids": ids[i : i + 50], "recrawl_days": 0}, user.id)
                for i in range(0, len(ids), 50)
            ]
            with db.session() as s:
                audit(s, "companies_enrich", user.id, count=len(ids), runs=run_ids)
            return {"queued": len(ids), "runs": run_ids, "skipped": len(body.ids) - len(ids)}
        with db.session() as s:
            if body.action == "delete":
                n = delete_companies(s, body.ids, body.exclude, user.id)
                audit(s, "companies_deleted", user.id, count=n, exclude=body.exclude)
                result = {"deleted": n}
            elif body.action == "status":
                if not body.status:
                    raise HTTPException(400, "Choose a status")
                n = s.execute(
                    update(Company)
                    .where(Company.id.in_(body.ids), Company.status != CompanyStatus.IN_OUTREACH)
                    .values(status=body.status, updated_at=utcnow())
                ).rowcount
                audit(s, "companies_bulk_status", user.id, count=n, status=body.status)
                result = {"updated": n}
            else:  # owner
                if body.owner_id is not None and not s.get(User, body.owner_id):
                    raise HTTPException(400, "Unknown team member")
                n = s.execute(
                    update(Company).where(Company.id.in_(body.ids)).values(owner_id=body.owner_id, updated_at=utcnow())
                ).rowcount
                audit(s, "companies_bulk_owner", user.id, count=n, owner_id=body.owner_id)
                result = {"updated": n}
        cache.bump()
        return result

    @app.post("/api/companies/{company_id}/enrich")
    def enrich_one(company_id: int, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            if s.get(Company, company_id) is None:
                raise HTTPException(404, "Company not found")
        return bulk(BulkIn(ids=[company_id], action="enrich"), user)

    # ---- notes ----------------------------------------------------------------------------------------
    def note_row(n: Note, u: User | None) -> dict[str, Any]:
        return {"id": n.id, "body": n.body, "user_id": n.user_id, "user": u.name if u else None,
                "created_at": _iso(n.created_at), "updated_at": _iso(n.updated_at)}  # fmt: skip

    @app.get("/api/companies/{company_id}/notes")
    def list_notes(company_id: int, user: User = Depends(viewer)) -> list[dict[str, Any]]:
        with db.session() as s:
            return [
                note_row(n, u)
                for n, u in s.execute(
                    select(Note, User)
                    .outerjoin(User, User.id == Note.user_id)
                    .where(Note.company_id == company_id)
                    .order_by(Note.created_at.desc())
                )  # fmt: skip
            ]

    @app.post("/api/companies/{company_id}/notes", status_code=201)
    def add_note(company_id: int, body: NoteIn, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            if not s.get(Company, company_id):
                raise HTTPException(404, "Company not found")
            text = body.body.strip()
            if not text:
                raise HTTPException(400, "Note cannot be empty")
            n = Note(company_id=company_id, user_id=user.id, body=text)
            s.add(n)
            s.flush()
            audit(s, "note_added", user.id, target=f"company:{company_id}")
            return note_row(n, user)

    def _own_note(s: Session, note_id: int, user: User) -> Note:
        n = s.get(Note, note_id)
        if n is None:
            raise HTTPException(404, "Note not found")
        if n.user_id != user.id and user.role != "admin":
            raise HTTPException(403, "You can only change your own notes")
        return n

    @app.patch("/api/notes/{note_id}")
    def edit_note(note_id: int, body: NoteIn, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            n = _own_note(s, note_id, user)
            text = body.body.strip()
            if not text:
                raise HTTPException(400, "Note cannot be empty")
            n.body = text
            return note_row(n, user)

    @app.delete("/api/notes/{note_id}")
    def delete_note(note_id: int, user: User = Depends(sdr)) -> dict[str, int]:
        with db.session() as s:
            s.delete(_own_note(s, note_id, user))
            return {"deleted": 1}

    # ---- people ---------------------------------------------------------------------------------------
    @app.get("/api/people")
    def list_people(
        q: str | None = Query(None, max_length=200),
        seniority: list[str] | None = Query(None),
        segment: list[str] | None = Query(None),
        country: list[str] | None = Query(None),
        has_email: bool | None = None,
        company_id: int | None = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        user: User = Depends(viewer),
    ) -> dict[str, Any]:
        with db.session() as s:
            base = _people_query(q, seniority, segment, country, has_email, company_id)
            total = s.scalar(select(func.count()).select_from(base.subquery()))
            order = (Person.seniority.is_(None), Person.seniority, Company.name, Person.name)
            rows = s.execute(base.order_by(*order).offset((page - 1) * page_size).limit(page_size)).all()
            return {"items": [person_row(p, c) for p, c in rows], "total": total}

    @app.get("/api/people/{person_id}")
    def get_person(person_id: int, user: User = Depends(viewer)) -> dict[str, Any]:
        with db.session() as s:
            p = s.get(Person, person_id)
            if p is None:
                raise HTTPException(404, "Person not found")
            return {**person_row(p, s.get(Company, p.company_id)), "sources": _provenance(s, "person", p.id)}

    def _apply_person(s: Session, p: Person, c: Company, changes: dict[str, Any]) -> list[str]:
        changed = []
        if changes.get("name"):
            name = " ".join(changes["name"].split())
            key = person_key(name)
            if len(key.split()) < 2:
                raise HTTPException(400, "Enter a first and last name")
            clash = s.scalar(
                select(Person.id).where(Person.company_id == c.id, Person.name_key == key, Person.id != p.id)
            )
            if clash:
                raise HTTPException(409, {"message": f"{name} is already listed at {c.name}", "existing_id": clash})
            if name != p.name:
                p.name, p.name_key = name, key
                changed.append("name")
        if "title" in changes:
            title = (changes["title"] or "").strip() or None
            if title != p.title:
                p.title, p.seniority = title, seniority_of(title)
                changed.append("title")
        if "email" in changes:
            email = _clean_email(changes["email"])
            if email != p.email:
                p.email = email
                p.email_status = email_kind(email, c.domain) if email else None
                changed.append("email")
        if "phone" in changes:
            raw = changes["phone"]
            phone = phone_e164(raw, c.country) if raw else None
            if raw and not phone:
                raise HTTPException(400, "Phone number is not valid")
            if phone != p.phone:
                p.phone = phone
                changed.append("phone")
        if "linkedin_url" in changes:
            li = (changes["linkedin_url"] or "").split("?")[0].rstrip("/") or None
            if li != p.linkedin_url:
                p.linkedin_url = li
                changed.append("linkedin_url")
        if changed:
            s.flush()
            for f in changed:
                _observe(s, "person", p.id, f, getattr(p, f), _manual(c.name), None)
        return changed

    @app.post("/api/people", status_code=201)
    def create_person(body: PersonIn, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            c = s.get(Company, body.company_id)
            if c is None:
                raise HTTPException(404, "Company not found")
            name = " ".join(body.name.split())
            key = person_key(name)
            if len(key.split()) < 2:
                raise HTTPException(400, "Enter a first and last name")
            clash = s.scalar(select(Person.id).where(Person.company_id == c.id, Person.name_key == key))
            if clash:
                raise HTTPException(409, {"message": f"{name} is already listed at {c.name}", "existing_id": clash})
            p = Person(company_id=c.id, name=name, name_key=key)
            s.add(p)
            s.flush()
            _observe(s, "person", p.id, "name", name, _manual(c.name), None)
            _apply_person(s, p, c, body.model_dump(exclude_unset=True, exclude={"company_id", "name"}))
            _rescore(s, c)
            audit(s, "person_created", user.id, target=f"person:{p.id}", company=c.id)
            cache.bump()
            return person_row(p, c)

    @app.patch("/api/people/{person_id}")
    def patch_person(person_id: int, body: PersonPatch, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            p = s.get(Person, person_id)
            if p is None:
                raise HTTPException(404, "Person not found")
            c = s.get(Company, p.company_id)
            changed = _apply_person(s, p, c, body.model_dump(exclude_unset=True))
            if changed:
                _rescore(s, c)
            audit(s, "person_updated", user.id, target=f"person:{person_id}", fields=changed)
            cache.bump()
            return person_row(p, c)

    @app.delete("/api/people/{person_id}")
    def delete_person(person_id: int, user: User = Depends(sdr)) -> dict[str, int]:
        with db.session() as s:
            p = s.get(Person, person_id)
            if p is None:
                raise HTTPException(404, "Person not found")
            c = s.get(Company, p.company_id)
            s.execute(delete(Observation).where(Observation.entity_type == "person", Observation.entity_id == p.id))
            s.delete(p)
            s.flush()
            if c:
                _rescore(s, c)
            audit(s, "person_deleted", user.id, target=f"person:{person_id}")
        cache.bump()
        return {"deleted": 1}

    # ---- exports --------------------------------------------------------------------------------------
    def _download(content: bytes, filename: str, media: str) -> Response:
        return Response(
            content,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"},
        )

    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

    @app.get("/api/export/companies")
    def export_companies(
        f: CompanyFilters = Depends(company_filters),
        sort: str = Query("best", pattern="^(best|fit|quality|name|newest|updated)$"),
        format: str = Query("xlsx", pattern="^(csv|xlsx)$"),
        ids: list[int] | None = Query(None),
        user: User = Depends(sdr),
    ) -> Response:
        with db.session() as s:
            _, ordered = _company_query(f, sort)
            if ids:
                ordered = ordered.where(Company.id.in_(ids))
            companies = s.scalars(ordered.limit(settings.export_max_rows)).all()
            rows = company_rows(s, companies)
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            audit(s, "export_companies", user.id, rows=len(rows), format=format)
            if format == "csv":
                return _download(to_csv(rows, COMPANY_COLUMNS), f"companies-{stamp}.csv", "text/csv; charset=utf-8")
            people = s.execute(
                select(Person, Company).join(Company, Company.id == Person.company_id)
                .where(Person.company_id.in_([c.id for c in companies])).order_by(Company.name, Person.name)
            ).all()  # fmt: skip
            prow = people_rows(s, people)
            content = to_xlsx(
                [("Companies", rows, COMPANY_COLUMNS), ("People", prow, PEOPLE_COLUMNS)],
                readme_rows("Companies and their people", len(rows), f.model_dump(), user),
            )
            return _download(content, f"companies-{stamp}.xlsx", XLSX)

    @app.get("/api/export/people")
    def export_people(
        q: str | None = Query(None, max_length=200),
        seniority: list[str] | None = Query(None),
        segment: list[str] | None = Query(None),
        country: list[str] | None = Query(None),
        has_email: bool | None = None,
        format: str = Query("xlsx", pattern="^(csv|xlsx)$"),
        user: User = Depends(sdr),
    ) -> Response:
        with db.session() as s:
            base = _people_query(q, seniority, segment, country, has_email, None)
            rows = people_rows(
                s, s.execute(base.order_by(Company.name, Person.name).limit(settings.export_max_rows)).all()
            )
            stamp = datetime.now().strftime("%Y%m%d-%H%M")
            audit(s, "export_people", user.id, rows=len(rows), format=format)
            if format == "csv":
                return _download(to_csv(rows, PEOPLE_COLUMNS), f"people-{stamp}.csv", "text/csv; charset=utf-8")
            filters = {"q": q, "seniority": seniority, "segment": segment, "country": country, "has_email": has_email}
            content = to_xlsx([("People", rows, PEOPLE_COLUMNS)], readme_rows("People", len(rows), filters, user))
            return _download(content, f"people-{stamp}.xlsx", XLSX)

    @app.get("/api/companies/{company_id}/profile.docx")
    def company_profile(company_id: int, user: User = Depends(sdr)) -> Response:
        with db.session() as s:
            c = s.get(Company, company_id)
            if c is None:
                raise HTTPException(404, "Company not found")
            content = company_docx(s, c, user)
            audit(s, "export_profile", user.id, target=f"company:{company_id}")
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", c.name).strip("-")[:60] or "company"
            return _download(content, f"{safe}-profile.docx", DOCX)

    # ---- discovery ------------------------------------------------------------------------------------
    @app.get("/api/sources")
    def sources(user: User = Depends(viewer)) -> list[dict[str, Any]]:
        with db.session() as s:
            last = {}
            for r in s.scalars(select(Run).order_by(Run.created_at.desc()).limit(500)):
                last.setdefault(r.source, r)
            out = []
            for k, cls in CONNECTORS.items():
                available = not cls.needs_key or bool(getattr(settings, cls.needs_key, None))
                out.append({**cls.describe(available), "last_run": run_row(last[k]) if k in last else None})
            return out

    @app.get("/api/runs")
    def runs(
        limit: int = Query(50, ge=1, le=500),
        status: list[str] | None = Query(None),
        source: list[str] | None = Query(None),
        automation_id: int | None = None,
        user: User = Depends(viewer),
    ) -> list[dict[str, Any]]:
        with db.session() as s:
            q = select(Run, Automation.name).outerjoin(Automation, Automation.id == Run.automation_id)
            if status:
                q = q.where(Run.status.in_(status))
            if source:
                q = q.where(Run.source.in_(source))
            if automation_id:
                q = q.where(Run.automation_id == automation_id)
            return [run_row(r, a) for r, a in s.execute(q.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit))]

    @app.post("/api/runs")
    def start_run(body: RunIn, user: User = Depends(sdr)) -> dict[str, Any]:
        cls = CONNECTORS.get(body.source)
        if cls and cls.needs_key and not getattr(settings, cls.needs_key, None):
            raise HTTPException(400, f"{cls.name} needs an API key. An admin can add it in the server settings.")
        try:
            run_id = enqueue(db, body.source, body.params, user.id)
        except RunError as exc:
            raise HTTPException(400, str(exc)) from exc
        with db.session() as s:
            audit(s, "run_queued", user.id, target=f"run:{run_id}", source=body.source)
            return run_row(s.get(Run, run_id))

    @app.post("/api/runs/{run_id}/cancel")
    def cancel_run(run_id: int, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            r = s.get(Run, run_id)
            if r is None:
                raise HTTPException(404, "Run not found")
            if r.status != "queued":
                raise HTTPException(409, "Only queued runs can be cancelled")
            r.status, r.finished_at, r.message = "cancelled", utcnow(), f"Cancelled by {user.name}"
            audit(s, "run_cancelled", user.id, target=f"run:{run_id}")
            return run_row(r)

    @app.post("/api/runs/{run_id}/retry")
    def retry_run(run_id: int, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            r = s.get(Run, run_id)
            if r is None:
                raise HTTPException(404, "Run not found")
            if r.status in ("queued", "running"):
                raise HTTPException(409, "This run is still queued or in progress; wait for it to finish")
            source, params = r.source, dict(r.params or {})
        try:
            new_id = enqueue(db, source, params, user.id)
        except RunError as exc:
            raise HTTPException(400, str(exc)) from exc
        with db.session() as s:
            audit(s, "run_retried", user.id, target=f"run:{new_id}", original=run_id)
            return run_row(s.get(Run, new_id))

    # ---- automations ----------------------------------------------------------------------------------
    def automation_row(a: Automation, last: Run | None) -> dict[str, Any]:
        preview, _ = next_params(a)
        return {
            "id": a.id,
            "name": a.name,
            "source": a.source,
            "source_name": CONNECTORS[a.source].name if a.source in CONNECTORS else a.source,
            "params": a.params or {},
            "rotate_field": a.rotate_field,
            "rotate_values": a.rotate_values or [],
            "rotate_index": a.rotate_index,
            "next_value": preview.get(a.rotate_field) if a.rotate_field else None,
            "cadence_minutes": a.cadence_minutes,
            "enabled": a.enabled,
            "builtin": a.builtin,
            "next_run_at": _iso(a.next_run_at),
            "last_run_at": _iso(a.last_run_at),
            "last_run": run_row(last) if last else None,
        }

    def _validate_automation(source: str, params: dict[str, Any], field: str | None, values: list[str] | None):
        cls = CONNECTORS.get(source)
        if cls is None:
            raise HTTPException(400, f"Unknown source: {source}")
        names = {p.name for p in cls.params if not p.hidden}
        if field and field not in names:
            raise HTTPException(400, f"{cls.name} has no setting called {field}")
        if field and not [v for v in values or [] if str(v).strip()]:
            raise HTTPException(400, "Add at least one value to rotate through")
        probe = dict(params)
        if field:
            probe[field] = next(v for v in values if str(v).strip())
        try:
            return validate_params(source, probe)
        except RunError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/automations")
    def list_automations(user: User = Depends(viewer)) -> list[dict[str, Any]]:
        with db.session() as s:
            rows = s.scalars(select(Automation).order_by(Automation.builtin.desc(), Automation.name)).all()
            last = {
                r.id: r
                for r in s.scalars(select(Run).where(Run.id.in_([a.last_run_id for a in rows if a.last_run_id])))
            }
            return [automation_row(a, last.get(a.last_run_id)) for a in rows]

    @app.post("/api/automations", status_code=201)
    def create_automation(body: AutomationIn, user: User = Depends(sdr)) -> dict[str, Any]:
        clean = _validate_automation(body.source, body.params, body.rotate_field, body.rotate_values)
        params = {k: v for k, v in clean.items() if k != body.rotate_field}
        with db.session() as s:
            a = Automation(
                name=body.name.strip(),
                source=body.source,
                params=params,
                rotate_field=body.rotate_field or None,
                rotate_values=[str(v).strip() for v in body.rotate_values or [] if str(v).strip()] or None,
                cadence_minutes=body.cadence_minutes,
                enabled=body.enabled,
                next_run_at=utcnow() + timedelta(minutes=1) if body.enabled else None,
                created_by=user.id,
            )
            s.add(a)
            s.flush()
            audit(s, "automation_created", user.id, target=f"automation:{a.id}", name=a.name)
            return automation_row(a, None)

    @app.patch("/api/automations/{automation_id}")
    def patch_automation(automation_id: int, body: AutomationPatch, user: User = Depends(sdr)) -> dict[str, Any]:
        changes = body.model_dump(exclude_unset=True)
        with db.session() as s:
            a = s.get(Automation, automation_id)
            if a is None:
                raise HTTPException(404, "Automation not found")
            params = changes.get("params", a.params or {})
            field = changes.get("rotate_field", a.rotate_field) or None
            values = changes.get("rotate_values", a.rotate_values)
            if {"params", "rotate_field", "rotate_values"} & changes.keys():
                clean = _validate_automation(a.source, params, field, values)
                a.params = {k: v for k, v in clean.items() if k != field}
                a.rotate_field = field
                a.rotate_values = [str(v).strip() for v in values or [] if str(v).strip()] or None
                a.rotate_index = 0
            if "name" in changes:
                a.name = changes["name"].strip()
            if "cadence_minutes" in changes:
                a.cadence_minutes = changes["cadence_minutes"]
                if a.enabled and a.last_run_at:
                    a.next_run_at = a.last_run_at + timedelta(minutes=a.cadence_minutes)
            if "enabled" in changes:
                if changes["enabled"] and not a.enabled:
                    a.next_run_at = utcnow() + timedelta(minutes=1)
                if not changes["enabled"]:
                    a.next_run_at = None
                a.enabled = changes["enabled"]
            audit(s, "automation_updated", user.id, target=f"automation:{automation_id}", fields=list(changes))
            last = s.get(Run, a.last_run_id) if a.last_run_id else None
            return automation_row(a, last)

    @app.delete("/api/automations/{automation_id}")
    def delete_automation(automation_id: int, user: User = Depends(sdr)) -> dict[str, int]:
        with db.session() as s:
            a = s.get(Automation, automation_id)
            if a is None:
                raise HTTPException(404, "Automation not found")
            if user.role != "admin" and a.created_by != user.id:
                raise HTTPException(403, "Only the creator or an admin can delete this automation")
            s.execute(update(Run).where(Run.automation_id == automation_id).values(automation_id=None))
            s.delete(a)
            audit(s, "automation_deleted", user.id, target=f"automation:{automation_id}")
            return {"deleted": 1}

    @app.post("/api/automations/{automation_id}/run")
    def run_automation(automation_id: int, user: User = Depends(sdr)) -> dict[str, Any]:
        with db.session() as s:
            a = s.get(Automation, automation_id)
            if a is None:
                raise HTTPException(404, "Automation not found")
            cls = CONNECTORS.get(a.source)
            if cls and cls.needs_key and not getattr(settings, cls.needs_key, None):
                raise HTTPException(400, f"{cls.name} needs an API key before it can run")
            automation_name = a.name
        run_id = trigger(db, automation_id, user.id)
        if run_id is None:
            raise HTTPException(409, "This automation already has a run queued or in progress")
        with db.session() as s:
            audit(s, "automation_run", user.id, target=f"automation:{automation_id}", run=run_id)
            return run_row(s.get(Run, run_id), automation_name)

    # ---- signals feed (live news, hiring, site mentions across all companies) --------------------------
    @app.get("/api/signals")
    def signals_feed(
        kind: list[str] | None = Query(None),
        q: str | None = Query(None, max_length=200),
        limit: int = Query(100, ge=1, le=500),
        user: User = Depends(viewer),
    ) -> list[dict[str, Any]]:
        with db.session() as s:
            stmt = select(Signal, Company).join(Company, Company.id == Signal.company_id)
            if kind:
                stmt = stmt.where(Signal.kind.in_(kind))
            if q:
                t = _like(q.strip())
                stmt = stmt.where(or_(Signal.title.ilike(t, escape="\\"), Company.name.ilike(t, escape="\\")))
            rows = s.execute(stmt.order_by(Signal.created_at.desc(), Signal.id.desc()).limit(limit)).all()
            return [
                {
                    "id": g.id,
                    "kind": g.kind,
                    "title": g.title,
                    "url": g.url,
                    "source": g.source,
                    "created_at": _iso(g.created_at),
                    "company_id": c.id,
                    "company": c.name,
                    "tier": c.tier,
                    "fit_score": c.fit_score,
                    "segment": c.segment,
                    "country": c.country,
                }  # fmt: skip
                for g, c in rows
            ]

    # ---- exclusions -----------------------------------------------------------------------------------
    @app.get("/api/exclusions")
    def list_exclusions(user: User = Depends(sdr)) -> list[dict[str, Any]]:
        with db.session() as s:
            return [
                {"id": e.id, "value": e.value, "label": e.label, "reason": e.reason, "created_at": _iso(e.created_at)}
                for e in s.scalars(select(Exclusion).order_by(Exclusion.created_at.desc()).limit(1000))
            ]

    @app.delete("/api/exclusions/{exclusion_id}")
    def delete_exclusion(exclusion_id: int, user: User = Depends(admin)) -> dict[str, int]:
        with db.session() as s:
            e = s.get(Exclusion, exclusion_id)
            if e is None:
                raise HTTPException(404, "Not found")
            s.delete(e)
            audit(s, "exclusion_removed", user.id, target=e.value)
            return {"deleted": 1}

    # ---- engine status --------------------------------------------------------------------------------
    @app.get("/api/engine")
    def engine(user: User = Depends(viewer)) -> dict[str, Any]:
        now = utcnow()
        day_ago = now - timedelta(hours=24)
        with db.session() as s:
            hb = s.get(Meta, "heartbeat:scheduler")
            hb_at = datetime.fromisoformat(hb.value) if hb and hb.value else None
            running = [
                run_row(r, a)
                for r, a in s.execute(
                    select(Run, Automation.name)
                    .outerjoin(Automation, Automation.id == Run.automation_id)
                    .where(Run.status == "running")
                    .order_by(Run.started_at)
                )  # fmt: skip
            ]
            queued = s.scalar(select(func.count()).where(Run.status == "queued")) or 0
            last24 = s.execute(
                select(func.count(), func.coalesce(func.sum(Run.created), 0), func.coalesce(func.sum(Run.found), 0))
                .where(Run.created_at >= day_ago)
            ).one()  # fmt: skip
            failed24 = s.scalar(select(func.count()).where(Run.created_at >= day_ago, Run.status == "failed")) or 0
            new_companies = s.scalar(select(func.count()).where(Company.created_at >= day_ago)) or 0
            new_people = s.scalar(select(func.count()).where(Person.created_at >= day_ago)) or 0
            enabled = s.scalar(select(func.count()).where(Automation.enabled.is_(True))) or 0
        if not db_has_today(db):
            snapshot(db)
        with db.session() as s:
            series = [
                {
                    "day": m.day.isoformat(),
                    "companies": m.companies,
                    "people": m.people,
                    "with_website": m.with_website,
                    "crawled": m.crawled,
                    "with_email": m.with_email,
                    "with_phone": m.with_phone,
                    "with_decision_maker": m.with_decision_maker,
                    "high_quality": m.high_quality,
                    "avg_quality": m.avg_quality,
                    "runs": m.runs,
                    "run_errors": m.run_errors,
                }  # fmt: skip
                for m in s.scalars(select(DailyMetric).order_by(DailyMetric.day.desc()).limit(90))
            ][::-1]
        worker = runtime.get("worker")
        hb_age = (now - hb_at).total_seconds() if hb_at else None
        return {
            "status": "running"
            if hb_age is not None and hb_age < 120
            else ("stopped" if worker is None else "starting"),
            "heartbeat_age_s": round(hb_age) if hb_age is not None else None,
            "worker_threads": worker.threads if worker else 0,
            "started_at": _iso(worker.started_at) if worker else None,
            "automations_enabled": enabled,
            "queue": {"queued": queued, "running": running},
            "last_24h": {
                "runs": last24[0],
                "records_found": int(last24[2]),
                "new_companies": new_companies,
                "new_people": new_people,
                "failed_runs": failed24,
            },  # fmt: skip
            "series": series,
            "cache": cache.stats(),
        }

    def db_has_today(d: Database) -> bool:
        with d.session() as s:
            return s.get(DailyMetric, utcnow().date()) is not None

    # ---- data quality ---------------------------------------------------------------------------------
    @app.get("/api/quality")
    def quality(user: User = Depends(viewer)) -> dict[str, Any]:
        def compute() -> dict[str, Any]:
            with db.session() as s:
                per_check = defaultdict(lambda: [0, 0])
                for check, passed, n in s.execute(
                    select(QualityCheck.check, QualityCheck.passed, func.count())
                    .where(QualityCheck.entity_type == "company")
                    .group_by(QualityCheck.check, QualityCheck.passed)
                ):
                    per_check[check][0 if passed else 1] += n
                scores = list(s.scalars(select(Company.quality_score).where(Company.quality_score.is_not(None))))
                fits = list(s.scalars(select(Company.fit_score).where(Company.fit_score.is_not(None))))

                def bands(values):
                    b = Counter("90–100" if x >= 90 else "70–89" if x >= 70 else "50–69" if x >= 50 else "0–49"
                                for x in values)  # fmt: skip
                    return [{"band": k, "count": b.get(k, 0)} for k in ("90–100", "70–89", "50–69", "0–49")]

                return {
                    "checks": [
                        {
                            "check": c.key,
                            "label": c.label,
                            "weight": c.weight,
                            "passed": per_check[c.key][0],
                            "failed": per_check[c.key][1],
                        }
                        for c in CHECKS
                    ],  # fmt: skip
                    "bands": bands(scores),
                    "fit_bands": bands(fits),
                    "tiers": [
                        {
                            "tier": t,
                            "rule": TIER_RULES[t],
                            "count": s.scalar(select(func.count()).where(Company.tier == t)) or 0,
                        }
                        for t in TIERS
                    ],
                    "scored": len(scores),
                    "unscored": s.scalar(select(func.count()).where(Company.quality_score.is_(None))),
                    "stale": s.scalar(
                        select(func.count()).where(
                            or_(
                                Company.last_verified_at.is_(None),
                                Company.last_verified_at < utcnow() - timedelta(days=7),
                            )
                        )
                    ),
                }

        return cache.cached("quality", 60, compute)


def _people_query(q, seniority, segment, country, has_email, company_id):
    base = select(Person, Company).join(Company, Company.id == Person.company_id)
    if q:
        t = _like(q.strip())
        base = base.where(
            or_(
                Person.name.ilike(t, escape="\\"),
                Person.title.ilike(t, escape="\\"),
                Company.name.ilike(t, escape="\\"),
                Person.email.ilike(t, escape="\\"),
            )
        )
    if seniority:
        base = base.where(Person.seniority.in_(seniority))
    if segment:
        base = base.where(Company.segment.in_(segment))
    if country:
        base = base.where(Company.country.in_([c.upper() for c in country]))
    if has_email is not None:
        base = base.where(Person.email.is_not(None) if has_email else Person.email.is_(None))
    if company_id:
        base = base.where(Person.company_id == company_id)
    return base
