"""Identity resolution, provenance and receipts.

Every connector produces CompanyRecord / PersonRecord values. `upsert_company` finds the existing company
(domain, then registry id, then fuzzy name within the same country), fills empty fields, and writes one
Observation per field so the UI can show where each value came from. Existing non-empty values are never
overwritten by a later source; conflicting values are kept as observations for review.

Receipts: a record can carry Evidence (the page or API record it came from, with a SHA-256 fingerprint) and,
per field, the words around the value on that page. Each Observation then links to its Evidence, so a fact
can be shown with exactly what the source said and when.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Company, Evidence, Exclusion, Observation, Person, Signal, utcnow
from .normalize import (
    best_contact_email,
    country_code,
    domain_of,
    email_kind,
    name_key,
    person_key,
    phone_e164,
    seniority_of,
    website_of,
)

FUZZY_THRESHOLD = 92  # token_sort_ratio on normalized names, same country only
COMPANY_FIELDS = (
    "name",
    "website",
    "domain",
    "country",
    "city",
    "address",
    "segment",
    "phone",
    "general_email",
    "linkedin_url",
    "lei",
    "company_number",
    "registry_status",
    "size_band",
    "description",
    "careers_url",
)


@dataclass
class EvidenceRecord:
    url: str
    kind: str  # page | api | file
    sha256: str
    title: str | None = None
    size: int | None = None


def page_evidence(url: str, html: str, title: str | None = None) -> EvidenceRecord:
    raw = html.encode("utf-8", "replace")
    return EvidenceRecord(url=url, kind="page", sha256=hashlib.sha256(raw).hexdigest(), title=title, size=len(raw))


def api_evidence(url: str, payload: Any, kind: str = "api") -> EvidenceRecord:
    """Fingerprint of exactly the record the API returned (canonical JSON: sorted keys, no whitespace)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    return EvidenceRecord(url=url, kind=kind, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw))


@dataclass
class PersonRecord:
    name: str
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    url: str | None = None  # page the person was seen on
    excerpt: str | None = None  # the words around the name on that page


@dataclass
class CompanyRecord:
    source: str
    url: str | None  # where this record was observed
    name: str
    website: str | None = None
    country: str | None = None
    city: str | None = None
    address: str | None = None
    segment: str | None = None
    phone: str | None = None
    general_email: str | None = None
    linkedin_url: str | None = None
    lei: str | None = None
    company_number: str | None = None
    registry_status: str | None = None
    size_band: str | None = None
    description: str | None = None
    careers_url: str | None = None
    employees: int | None = None
    founded: int | None = None
    site_name: str | None = None  # the organisation's own name for itself (og:site_name / schema.org)
    socials: dict[str, str] = field(default_factory=dict)
    tech: list[str] = field(default_factory=list)
    ats: tuple[str, str] | None = None
    confidence: float = 0.8
    people: list[PersonRecord] = field(default_factory=list)
    signals: list[tuple[str, str, str | None]] = field(default_factory=list)  # (kind, title, url)
    evidence: list[EvidenceRecord] = field(default_factory=list)
    receipts: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)  # field -> (page url, excerpt)
    fetched_at: datetime = field(default_factory=utcnow)  # when the source was read (provenance)

    def normalized(self) -> dict[str, str | None]:
        country = country_code(self.country)
        website = website_of(self.website)
        domain = domain_of(website) if website else None
        email = (self.general_email or "").strip().lower() or None
        if email and email_kind(email, domain) in ("free_mail", "off_domain"):
            email = None  # only the company's own business addresses
        if email and not best_contact_email([email]):
            email = None  # press@ / webmaster@ / security@ are not sales contacts (kept as an observation)
        return {
            "name": " ".join(self.name.split())[:300],
            "website": website,
            "domain": domain,
            "country": country,
            "city": (self.city or "").strip()[:120] or None,
            "address": (self.address or "").strip() or None,
            "segment": self.segment,
            "phone": phone_e164(self.phone, country),
            "general_email": email,
            "linkedin_url": (self.linkedin_url or "").split("?")[0].rstrip("/") or None,
            "lei": self.lei,
            "company_number": self.company_number,
            "registry_status": self.registry_status,
            "size_band": self.size_band,
            "description": (self.description or "").strip()[:1000] or None,
            "careers_url": (self.careers_url or "").strip() or None,
        }


def find_company(s: Session, data: dict[str, str | None]) -> Company | None:
    if data.get("domain"):
        hit = s.scalar(select(Company).where(Company.domain == data["domain"]))
        if hit:
            return hit
    if data.get("lei"):
        hit = s.scalar(select(Company).where(Company.lei == data["lei"]))
        if hit:
            return hit
    if data.get("company_number") and data.get("country"):
        hit = s.scalar(
            select(Company).where(Company.company_number == data["company_number"], Company.country == data["country"])
        )
        if hit:
            return hit
    key = name_key(data["name"] or "")
    if not key:
        return None
    q = select(Company).where(Company.name_key == key)
    if data.get("country"):
        q = q.where(Company.country == data["country"])
    exact = s.scalar(q.limit(1))
    if exact:
        return exact
    # fuzzy, same country, same first token (keeps the candidate set small)
    first = key.split(" ", 1)[0]
    cands = s.scalars(
        select(Company).where(Company.name_key.like(f"{first}%"), Company.country == data.get("country")).limit(200)
    ).all()
    best = max(cands, key=lambda c: fuzz.token_sort_ratio(key, c.name_key), default=None)
    if best and fuzz.token_sort_ratio(key, best.name_key) >= FUZZY_THRESHOLD and not _conflicts(best, data):
        return best
    return None


def _conflicts(c: Company, data: dict[str, str | None]) -> bool:
    """A fuzzy name match is rejected when hard identifiers disagree (different site, LEI or registry number)."""
    return any(getattr(c, f) and data.get(f) and getattr(c, f) != data[f] for f in ("domain", "lei", "company_number"))


def _observe(
    s: Session,
    etype: str,
    eid: int,
    fieldname: str,
    value,
    rec: CompanyRecord,
    run_id: int | None,
    evidence_id: int | None = None,
    excerpt: str | None = None,
    url: str | None = None,
) -> None:
    if value in (None, ""):
        return
    value = str(value)[:2000]
    excerpt = (excerpt or "")[:600] or None
    # Re-seeing the same value from the same source refreshes it instead of duplicating the trail.
    seen = s.scalar(
        select(Observation).where(
            Observation.entity_type == etype,
            Observation.entity_id == eid,
            Observation.field == fieldname,
            Observation.source == rec.source,
            Observation.value == value,
        )
    )
    if seen is not None:
        seen.retrieved_at, seen.run_id = utcnow(), run_id
        seen.url = url or rec.url or seen.url
        seen.evidence_id = evidence_id or seen.evidence_id
        seen.excerpt = excerpt or seen.excerpt
        return
    s.add(
        Observation(
            entity_type=etype,
            entity_id=eid,
            field=fieldname,
            value=value,
            source=rec.source,
            url=url or rec.url,
            confidence=rec.confidence,
            run_id=run_id,
            evidence_id=evidence_id,
            excerpt=excerpt,
        )
    )


def store_evidence(s: Session, rec: CompanyRecord) -> dict[str, int]:
    """Save the record's receipts; returns page/API URL -> Evidence id."""
    ids: dict[str, int] = {}
    for ev in rec.evidence:
        url = ev.url[:1000]
        row = s.scalar(select(Evidence).where(Evidence.url == url, Evidence.sha256 == ev.sha256))
        if row is None:
            row = Evidence(
                url=url, kind=ev.kind, source=rec.source, sha256=ev.sha256,
                title=(ev.title or "")[:500] or None, size=ev.size, fetched_at=rec.fetched_at,
            )  # fmt: skip
            s.add(row)
            s.flush()
        ids[ev.url] = row.id
    return ids


def is_placeholder_name(company: Company) -> bool:
    """A company added from a bare domain is named after the domain until its website tells us better."""
    return bool(company.domain) and name_key(company.name) in {
        name_key(company.domain),
        name_key(company.domain.split(".")[0]),
    }


def _apply_site_name(company: Company, rec: CompanyRecord) -> None:
    name = " ".join((rec.site_name or "").split())
    if not name or len(name) > 120 or rec.source != "website":
        return
    if is_placeholder_name(company):
        company.name, company.name_key = name, name_key(name)


class ExcludedRecord(Exception):
    """The team deleted this company and asked for it not to be added again."""


def exclusion_keys(domain: str | None, name: str | None, country: str | None) -> list[str]:
    keys = []
    if domain:
        keys.append(f"domain:{domain}")
    if name:
        keys.append(f"name:{country or ''}:{name_key(name)}")
    return keys


def upsert_company(s: Session, rec: CompanyRecord, run_id: int | None = None) -> tuple[Company, bool]:
    """Returns (company, created). Raises ExcludedRecord for companies the team removed for good."""
    data = rec.normalized()
    keys = exclusion_keys(data.get("domain"), data.get("name"), data.get("country"))
    if keys and s.scalar(select(Exclusion.id).where(Exclusion.value.in_(keys)).limit(1)):
        raise ExcludedRecord(data.get("name"))
    company = find_company(s, data)
    created = company is None
    if created:
        company = Company(name=data["name"], name_key=name_key(data["name"] or ""))
        s.add(company)
    if rec.phone and not data["phone"] and company.country:
        # local-format numbers (e.g. "0161 832 1234") need the company's country to normalize
        data["phone"] = phone_e164(rec.phone, company.country)
    for f in COMPANY_FIELDS:
        value = data.get(f)
        if value in (None, ""):
            continue
        # A domain may already belong to another record (a duplicate we did not match by name): keep both,
        # leave this one without the domain and record the observation for review.
        if (
            f == "domain"
            and company.domain is None
            and s.scalar(select(Company.id).where(Company.domain == value, Company.id != company.id))
        ):
            continue
        if getattr(company, f) in (None, ""):
            setattr(company, f, value)
    _apply_site_name(company, rec)
    if rec.socials:
        merged = dict(company.socials or {})
        for network, url in rec.socials.items():
            merged.setdefault(network, url)
        company.socials = merged  # reassign so the JSON column is marked dirty
        if not company.linkedin_url and merged.get("linkedin"):
            company.linkedin_url = merged["linkedin"]
    if rec.tech:
        company.tech = sorted(set(company.tech or []) | set(rec.tech))
    if rec.ats and not company.ats:
        company.ats = {"provider": rec.ats[0], "token": rec.ats[1]}
    if rec.employees and not company.employees:
        company.employees = rec.employees
    if rec.founded and not company.founded:
        company.founded = rec.founded
    s.flush()

    ev_ids = store_evidence(s, rec)
    default_ev = (
        ev_ids.get(rec.url) if rec.url in ev_ids else (next(iter(ev_ids.values())) if len(ev_ids) == 1 else None)
    )

    def observe(field_name: str, value, receipt_key: str | None = None) -> None:
        page_url, excerpt = rec.receipts.get(receipt_key or field_name, (None, None))
        ev = ev_ids.get(page_url) if page_url else default_ev
        _observe(s, "company", company.id, field_name, value, rec, run_id, ev, excerpt, page_url)

    for f in COMPANY_FIELDS:
        observe(f, data.get(f))
    for network, url in (rec.socials or {}).items():
        observe(f"social:{network}", url)
    for t in rec.tech:
        observe("tech", t, f"tech:{t}")
    if rec.employees:
        observe("employees", rec.employees)
    if rec.founded:
        observe("founded", rec.founded)
    raw_email = (rec.general_email or "").strip().lower()
    if raw_email and raw_email != data.get("general_email"):
        observe("other_email", raw_email, "general_email")
    for p in rec.people:
        upsert_person(s, company, p, rec, run_id, ev_ids.get(p.url, default_ev) if p.url else default_ev)
    for kind, title, url in rec.signals:
        exists = s.scalar(
            select(Signal.id).where(Signal.company_id == company.id, Signal.title == title[:1000], Signal.kind == kind)
        )
        if not exists:
            s.add(Signal(company_id=company.id, kind=kind, title=title[:1000], url=url, source=rec.source))
    company.updated_at = utcnow()
    return company, created


def upsert_person(
    s: Session,
    company: Company,
    p: PersonRecord,
    rec: CompanyRecord,
    run_id: int | None,
    evidence_id: int | None = None,
) -> Person | None:
    key = person_key(p.name)
    if len(key.split()) < 2:  # need at least a first and last name
        return None
    person = s.scalar(select(Person).where(Person.company_id == company.id, Person.name_key == key))
    if person is None:
        person = Person(company_id=company.id, name=" ".join(p.name.split())[:200], name_key=key)
        s.add(person)
    email = (p.email or "").strip().lower() or None
    kind = email_kind(email, company.domain) if email else None
    if email and kind in ("free_mail", "off_domain"):
        email, kind = None, None  # never store personal or third-party addresses
    if p.title and not person.title:
        person.title = p.title[:300]
        person.seniority = seniority_of(p.title)
    if email and not person.email:
        person.email, person.email_status = email, kind
    phone = phone_e164(p.phone, company.country)
    if phone and not person.phone:
        person.phone = phone
    linkedin = p.linkedin_url.split("?")[0].rstrip("/") if p.linkedin_url else None
    if linkedin and not person.linkedin_url:
        person.linkedin_url = linkedin
    s.flush()
    obs_rec = CompanyRecord(source=rec.source, url=p.url or rec.url, name=company.name, confidence=rec.confidence)
    # `linkedin_url` sat outside this loop, so a profile that did land had no receipt behind it;
    # every field the person keeps now leaves an observation naming the page it came from.
    for f, v in (
        ("name", person.name),
        ("title", p.title),
        ("email", email),
        ("phone", phone),
        ("linkedin_url", linkedin),
    ):
        _observe(s, "person", person.id, f, v, obs_rec, run_id, evidence_id, p.excerpt)
    return person
