"""Verification and quality scoring. Every check is stored, so the UI can show exactly why a score is low.

No mailbox probing (SMTP RCPT) is done: it is unreliable, gets sending IPs blocklisted, and is not needed to
judge a business address that the company itself published. The email *provider* is read from public MX
records (e.g. Microsoft 365, Google Workspace): a buying signal for private AI, not a deliverability test.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import dns.exception
import dns.resolver
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .fit import evaluate_fit
from .models import Company, Observation, Person, QualityCheck, utcnow
from .normalize import email_kind


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    weight: int


CHECKS = [
    Check("has_website", "Has a website", 15),
    Check("domain_resolves", "Domain resolves (DNS)", 15),
    Check("domain_has_mx", "Domain accepts email (MX record)", 15),
    Check("has_contact", "Has a business email or phone", 15),
    Check("email_on_domain", "Email is on the company's own domain", 10),
    Check("has_named_person", "Has at least one named person", 10),
    Check("has_decision_maker", "Has a senior contact (owner, partner, director, C-level)", 10),
    Check("corroborated", "Confirmed by two or more sources", 5),
    Check("registry_active", "Registry status is active (when registered)", 5),
]
CHECK_BY_KEY = {c.key: c for c in CHECKS}

# MX host suffix -> provider name (most specific first)
MX_PROVIDERS = [
    (".mail.protection.outlook.com", "Microsoft 365"),
    (".outlook.com", "Microsoft 365"),
    ("aspmx.l.google.com", "Google Workspace"),
    (".googlemail.com", "Google Workspace"),
    (".google.com", "Google Workspace"),
    (".zoho.com", "Zoho Mail"),
    (".zoho.eu", "Zoho Mail"),
    (".pphosted.com", "Proofpoint (gateway)"),
    (".ppe-hosted.com", "Proofpoint (gateway)"),
    (".mimecast.com", "Mimecast (gateway)"),
    (".mimecast.co.za", "Mimecast (gateway)"),
    (".barracudanetworks.com", "Barracuda (gateway)"),
    (".messagelabs.com", "Broadcom (gateway)"),
    (".secureserver.net", "GoDaddy"),
    (".emailsrvr.com", "Rackspace"),
    (".icloud.com", "Apple iCloud"),
    (".yandex.net", "Yandex"),
    (".ovh.net", "OVH"),
]


def email_provider(mx_hosts: list[str]) -> str | None:
    if not mx_hosts:
        return None
    hosts = [h.lower().rstrip(".") for h in mx_hosts]
    for suffix, name in MX_PROVIDERS:
        if any(h.endswith(suffix) or h == suffix.lstrip(".") for h in hosts):
            return name
    return "Self-hosted / other"


class DnsCache:
    """DNS (and RDAP) lookups cached for the life of a run.

    resolver(domain, rtype) -> list of record strings; rdap(domain) -> registration date or None.
    rdap=False skips registration lookups (used for instant re-scoring after a manual edit).
    """

    def __init__(
        self,
        resolver: Callable[[str, str], list[str]] | None = None,
        rdap: Callable[[str], date | None] | bool | None = None,
    ) -> None:
        self._cache: dict[tuple[str, str], list[str]] = {}
        self._resolver = resolver or _resolve
        self._rdap = None if rdap is False else (rdap if callable(rdap) else _rdap_created)
        self._rdap_cache: dict[str, date | None] = {}

    def records(self, domain: str, rtype: str) -> list[str]:
        k = (domain, rtype)
        if k not in self._cache:
            self._cache[k] = self._resolver(domain, rtype)
        return self._cache[k]

    def has(self, domain: str, rtype: str) -> bool:
        return bool(self.records(domain, rtype))

    def created(self, domain: str) -> date | None:
        if self._rdap is None:
            return None
        if domain not in self._rdap_cache:
            self._rdap_cache[domain] = self._rdap(domain)
        return self._rdap_cache[domain]


def _rdap_created(domain: str) -> date | None:
    """Registration date from public RDAP data (rdap.org redirects to the registry's server)."""
    import httpx

    from .connectors.base import user_agent  # local: connectors import resolve, which imports this module

    try:
        r = httpx.get(
            f"https://rdap.org/domain/{domain}",
            follow_redirects=True,
            timeout=8,
            headers={"Accept": "application/rdap+json", "User-Agent": user_agent()},
        )
        if r.status_code != 200:
            return None
        for ev in r.json().get("events", []):
            if ev.get("eventAction") == "registration" and ev.get("eventDate"):
                return date.fromisoformat(ev["eventDate"][:10])
    except Exception:  # registry without RDAP, timeout, bad JSON: the date simply stays unknown
        return None
    return None


def dmarc_policy(txt_records: list[str]) -> str:
    """'reject' | 'quarantine' | 'none' (monitoring only) | 'missing'."""
    for rec in txt_records:
        flat = rec.replace('" "', "").strip('"').lower()
        if flat.startswith("v=dmarc1"):
            for part in flat.split(";"):
                k, _, v = part.strip().partition("=")
                if k == "p" and v.strip() in ("reject", "quarantine", "none"):
                    return v.strip()
            return "none"
    return "missing"


def _resolve(domain: str, rtype: str) -> list[str]:
    r = dns.resolver.Resolver()
    r.lifetime = 4.0
    try:
        answer = r.resolve(domain, rtype)
    except (dns.exception.DNSException, OSError):
        return []
    if rtype == "MX":
        return [str(a.exchange).rstrip(".") for a in answer]
    return [a.to_text() for a in answer]


def evaluate(s: Session, c: Company, dns_cache: DnsCache) -> dict[str, tuple[bool | None, str]]:
    """check -> (passed or None if not applicable, detail)."""
    people = s.scalars(select(Person).where(Person.company_id == c.id)).all()
    sources = (
        s.scalar(
            select(func.count(func.distinct(Observation.source))).where(
                Observation.entity_type == "company", Observation.entity_id == c.id, Observation.source != "manual"
            )
        )
        or 0
    )
    emails = [e for e in [c.general_email, *(p.email for p in people)] if e]
    res: dict[str, tuple[bool | None, str]] = {}
    res["has_website"] = (bool(c.website), c.website or "no website on record")
    if c.domain:
        a = dns_cache.has(c.domain, "A") or dns_cache.has(c.domain, "AAAA")
        res["domain_resolves"] = (a, f"{c.domain} {'resolves' if a else 'does not resolve'}")
        mx = dns_cache.records(c.domain, "MX")
        res["domain_has_mx"] = (bool(mx), f"{c.domain}: {', '.join(mx[:2])}" if mx else f"{c.domain} has no MX record")
    else:
        res["domain_resolves"] = (False, "no domain")
        res["domain_has_mx"] = (False, "no domain")
    res["has_contact"] = (bool(emails or c.phone or any(p.phone for p in people)), f"{len(emails)} email(s)")
    if emails:
        off = [e for e in emails if email_kind(e, c.domain) in ("off_domain", "free_mail")]
        res["email_on_domain"] = (not off, "all on company domain" if not off else f"off-domain: {', '.join(off)}")
    else:
        res["email_on_domain"] = (None, "no email")
    res["has_named_person"] = (bool(people), f"{len(people)} person(s)")
    senior = [p for p in people if p.seniority in ("owner", "c_level", "partner", "director")]
    res["has_decision_maker"] = (bool(senior), ", ".join(f"{p.name} ({p.title})" for p in senior[:3]) or "none found")
    res["corroborated"] = (sources >= 2, f"{sources} source(s)")
    if c.lei or c.registry_status:
        active = (c.registry_status or "").upper() == "ACTIVE"
        res["registry_active"] = (active, c.registry_status or "status unknown")
    else:
        res["registry_active"] = (None, "not in a registry source")
    return res


TIERS = ("golden", "verified", "partial", "raw")
TIER_RULES = {
    "golden": "Quality 80+, fit 60+, working domain that accepts email, named decision-maker, a business contact, "
    "and confirmed by two or more sources",
    "verified": "Quality 60+, working domain and a business contact",
    "partial": "Has a website or a contact, but not yet verified to the bar above",
    "raw": "Name and location only",
}
GOLDEN_QUALITY, GOLDEN_FIT, VERIFIED_QUALITY = 80, 60, 60


def tier_of(results: dict[str, tuple[bool | None, str]], quality: int, fit: int) -> str:
    ok = {k: bool(v[0]) for k, v in results.items()}
    if (
        quality >= GOLDEN_QUALITY
        and fit >= GOLDEN_FIT
        and ok.get("domain_resolves")
        and ok.get("domain_has_mx")
        and ok.get("has_decision_maker")
        and ok.get("has_contact")
        and ok.get("corroborated")
    ):
        return "golden"
    if quality >= VERIFIED_QUALITY and ok.get("domain_resolves") and ok.get("has_contact"):
        return "verified"
    if ok.get("has_website") or ok.get("has_contact"):
        return "partial"
    return "raw"


def score(results: dict[str, tuple[bool | None, str]]) -> int:
    applicable = [(CHECK_BY_KEY[k].weight, ok) for k, (ok, _) in results.items() if ok is not None]
    total = sum(w for w, _ in applicable)
    return round(100 * sum(w for w, ok in applicable if ok) / total) if total else 0


def verify_company(s: Session, c: Company, dns_cache: DnsCache) -> int:
    results = evaluate(s, c, dns_cache)
    # Every network lookup is done here, *before* the first write is flushed. The QualityCheck loop below
    # autoflushes an INSERT, which takes SQLite's write lock; doing DNS/RDAP afterwards holds that lock
    # across multi-second lookups, starving the second worker and every API handler past busy_timeout
    # (30s), whose writes then fail outright with "database is locked" and are not retried -- 252 such
    # failures were in the live log, each losing a record or an audit row. See tests/test_write_lock.py.
    if c.domain:
        mx = dns_cache.records(c.domain, "MX")
        c.email_provider = email_provider(mx) or c.email_provider
        if mx:
            c.email_security = dmarc_policy(dns_cache.records(f"_dmarc.{c.domain}", "TXT"))
        if c.domain_created_on is None:
            c.domain_created_on = dns_cache.created(c.domain)
    for key, (ok, detail) in results.items():
        row = s.scalar(
            select(QualityCheck).where(
                QualityCheck.entity_type == "company", QualityCheck.entity_id == c.id, QualityCheck.check == key
            )
        )
        if ok is None:
            if row is not None:
                s.delete(row)
            continue
        row = row or QualityCheck(entity_type="company", entity_id=c.id, check=key)
        row.passed, row.detail, row.checked_at = ok, detail[:1000], utcnow()
        s.add(row)
    c.quality_score = score(results)
    c.fit_score, _ = evaluate_fit(s, c)
    c.tier = tier_of(results, c.quality_score, c.fit_score)
    c.last_verified_at = utcnow()
    return c.quality_score
