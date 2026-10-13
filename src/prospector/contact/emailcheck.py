"""Email address validation with provenance: RFC parsing + IDN normalization, then public DNS.

`email-validator` (Unlicense) does what a regex cannot: RFC 5321/5322 parsing, Unicode (IDN) domain
normalization to punycode, quoted local parts, domain literals. Deliverability is then judged from DNS
only - MX records, RFC 7505 null-MX, and an SPF record that ends in `-all` (a domain that says nothing
may ever be mailed from it). No mailbox is ever probed from this module.

Verdicts (each comes with a reason, a source and a checked_at timestamp):
    valid   syntax is fine, the domain has a real mail exchanger, nothing rejects the domain outright
    invalid it cannot be delivered: bad syntax, no mail exchanger, null-MX, or a domain that does not exist
    risky   deliverable in theory, but the domain argues against it: reject-all SPF, several SPF records,
            a non-ASCII local part (needs an SMTPUTF8 relay), or an implicit MX (no MX, A-record fallback)
    unknown the DNS could not answer (timeout, SERVFAIL): nothing is claimed either way

The resolver is injectable: `(domain, rtype) -> list[str]` where `[]` means "definitively no records of
this type" and raising means "the lookup failed". That keeps tests and the offline run paths off the
network while production uses `_resolve` (dnspython, 4s lifetime).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import dns.exception
import dns.resolver
import email_validator
from email_validator import EmailNotValidError

from ..models import utcnow

SOURCE = "email-validator+dns"
VERDICTS = ("valid", "risky", "invalid", "unknown")
Resolver = Callable[[str, str], list[str]]


class DnsTemporaryError(Exception):
    """The DNS could not answer in time: the verdict for this address is "unknown", not "invalid"."""


@dataclass(frozen=True)
class EmailVerdict:
    """One judgement of one address: what was decided, why, by whom, and when."""

    email: str
    verdict: str  # valid | risky | invalid | unknown
    reason: str
    checked_at: datetime = field(default_factory=utcnow)
    source: str = SOURCE


def _flatten(record: str) -> str:
    """TXT chunks arrive quoted ('"v=spf1" "-all"'): quotes are not part of SPF semantics."""
    return record.replace('"', "").strip()


def spf_issues(txt_records: Sequence[str]) -> str | None:
    """Why this domain's SPF is a problem, or None when it is (at most) a normal policy.

    Reject-all (`-all`) means the domain states that no host may send mail for it, so an address there
    will bounce whatever we send. Several SPF records are a permanent error (RFC 7208): mail is unreliable.
    """
    policies = [flat for flat in map(_flatten, txt_records) if flat.lower().startswith("v=spf1")]
    if not policies:
        return None
    if len(policies) > 1:
        return "risky: domain publishes several SPF records (a permanent error for senders)"
    terms = policies[0].split()[1:]
    alls = [t.lower() for t in terms if t.lower() in ("+all", "-all", "~all", "?all")]
    if alls and alls[-1] == "-all":
        return "risky: domain publishes SPF -all, it rejects all mail"
    return None


def _resolve(domain: str, rtype: str) -> list[str]:
    """Public DNS. `[]` = no records of that type; raising DnsTemporaryError = the query failed."""
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4.0
    try:
        answer = resolver.resolve(domain, rtype)
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except (dns.exception.DNSException, OSError) as exc:
        raise DnsTemporaryError(str(exc) or type(exc).__name__) from exc
    if rtype == "TXT":
        out = []
        for rec in answer:
            strings = getattr(rec, "strings", None)
            out.append(b"".join(strings).decode("utf-8", "replace") if strings else rec.to_text())
        return out
    if rtype == "MX":
        return [str(a.exchange).rstrip(".") for a in answer]
    return [a.to_text() for a in answer]


def check_email(email: str | None, resolver: Resolver | None = None) -> EmailVerdict:
    """Judge one address. `resolver` overrides DNS (tests, offline runs); default is dnspython."""
    now = utcnow()
    raw = (email or "").strip()
    if not raw:
        return EmailVerdict(email="", verdict="invalid", reason="empty address", checked_at=now)
    try:
        info = email_validator.validate_email(raw, check_deliverability=False)
    except EmailNotValidError as exc:
        return EmailVerdict(email=raw.lower(), verdict="invalid", reason=f"RFC syntax: {exc}", checked_at=now)
    normalized = info.normalized.lower()
    if info.smtputf8:
        return EmailVerdict(
            email=normalized,
            verdict="risky",
            reason="non-ASCII local part: only an SMTPUTF8 relay can deliver it",
            checked_at=now,
        )
    look = resolver or _resolve
    domain = info.ascii_domain
    try:
        mx = look(domain, "MX")
    except Exception as exc:
        return EmailVerdict(
            email=normalized, verdict="unknown", reason=f"DNS query for MX failed ({exc})", checked_at=now
        )
    if mx:
        hosts = [h.strip().lower().rstrip(".") for h in mx]
        if any(not h for h in hosts):
            return EmailVerdict(
                email=normalized,
                verdict="invalid",
                reason="null MX: the domain publishes RFC 7505 '.' and accepts no mail",
                checked_at=now,
            )
    else:
        try:
            resolves = bool(look(domain, "A") or look(domain, "AAAA"))
        except Exception as exc:
            return EmailVerdict(
                email=normalized, verdict="unknown", reason=f"DNS query for A failed ({exc})", checked_at=now
            )
        if not resolves:
            return EmailVerdict(
                email=normalized,
                verdict="invalid",
                reason="no MX record and the domain does not resolve: it probably does not exist",
                checked_at=now,
            )
        return EmailVerdict(
            email=normalized,
            verdict="risky",
            reason="no MX record: mail can only fall back to the domain itself (implicit MX)",
            checked_at=now,
        )
    try:
        issue = spf_issues(look(domain, "TXT"))
    except Exception:
        issue = None  # SPF is an extra argument, not the verdict: a failed TXT query claims nothing
    if issue:
        return EmailVerdict(email=normalized, verdict="risky", reason=issue, checked_at=now)
    return EmailVerdict(
        email=normalized,
        verdict="valid",
        reason=f"MX present ({', '.join(h for h in hosts[:2])})",
        checked_at=now,
    )
