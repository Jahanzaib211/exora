"""Email pattern guesses and optional SMTP verification: in-house, keyless, off by default.

What this module does and does not do:
    - Guesses the structured addresses a company's own mail would plausibly use (first.last@, flast@,
      first@, firstl@, last@) from a known person name + the company's domain, each with a confidence
      score. A guess is a hypothesis, never a published contact: callers must verify before use and the
      campaign only ever mails addresses on the company's own domain.
    - Detects catch-all domains with a random local part probe. A server that accepts anything proves
      nothing about a real mailbox, so RCPT acceptance is only reported as verification when the domain
      rejects made-up addresses first.
    - Optionally probes one address with SMTP `RCPT TO` - default OFF (PROSPECTOR_SMTP_PROBE=1), throttled
      to one probe per second per MX host, using either the operator's own MAIL FROM
      (PROSPECTOR_PROBE_FROM) or the null reverse path `<>`, never a sender we do not control.
    - Caches probe verdicts by email with a TTL (PROSPECTOR_PROBE_TTL_HOURS, default 24h) so the same
      address is never probed needlessly.

SMTP probing is deliberately opt-in: outbound port 25 is blocked on most hosts, and greylisting makes a
one-shot probe unreliable, so every uncertain answer is reported as "unknown" instead of a verdict.
"""

from __future__ import annotations

import os
import re
import smtplib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from secrets import token_hex

import dns.exception
import dns.resolver

from ..models import utcnow
from ..normalize import ascii_fold, domain_of

PROBE_ENV = "PROSPECTOR_SMTP_PROBE"
SENDER_ENV = "PROSPECTOR_PROBE_FROM"
TTL_ENV = "PROSPECTOR_PROBE_TTL_HOURS"
PROBE_INTERVAL = 1.0  # at most one probe per second per MX host
DEFAULT_TTL_HOURS = 24.0
Probe = Callable[[str], bool | None]  # accepted | rejected | unknown

# (pattern name, builder, confidence): the shapes an email address usually takes in a company.
PATTERNS = (
    ("first.last", lambda first, last: f"{first}.{last}", 0.90),
    ("flast", lambda first, last: f"{first[0]}{last}", 0.75),
    ("first", lambda first, last: first, 0.60),
    ("firstl", lambda first, last: f"{first}{last[0]}", 0.65),
    ("last", lambda first, last: last, 0.45),
)
TITLES = re.compile(r"^(?:mr|mrs|ms|miss|dr|prof|sir|dame|rev|engr)\.?\s+", re.IGNORECASE)


@dataclass(frozen=True)
class PatternCandidate:
    email: str
    pattern: str  # first.last | flast | first | firstl | last
    confidence: float


def guess_patterns(name: str, domain: str) -> list[PatternCandidate]:
    """Candidate addresses for one person at one company domain, in the usual order of likelihood.

    Names are folded to ASCII (Jose Garcia -> jose) and titles (Dr, Mr...) stripped, so the local parts
    are always deliverable-by-syntax; whether the mailbox exists is for `verify_email` to answer.
    """
    registrable = domain_of(domain)
    if not registrable:
        return []
    cleaned = TITLES.sub("", ascii_fold(name or "").strip())
    parts = re.findall(r"[a-z0-9]+", cleaned.lower())
    if not parts:
        return []
    first, last = parts[0], parts[-1]
    single = len(parts) == 1
    out: list[PatternCandidate] = []
    seen: set[str] = set()
    for pattern, build, confidence in PATTERNS:
        if single and pattern != "first":
            continue  # one name only ("Cher"): first.last and last.initial guesses are meaningless
        local = build(first, last)
        if not local or local in seen:
            continue
        seen.add(local)
        out.append(PatternCandidate(f"{local}@{registrable}", pattern, confidence))
    return out


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def probe_enabled() -> bool:
    """SMTP probing stays off unless PROSPECTOR_SMTP_PROBE says otherwise (port 25 is usually blocked)."""
    return _flag(PROBE_ENV)


def probe_sender() -> str:
    """MAIL FROM: the operator's own address, or the null reverse path `<>` when none is configured."""
    return os.environ.get(SENDER_ENV, "").strip() or "<>"


def probe_ttl() -> float:
    """Seconds one probe verdict stays cached (PROSPECTOR_PROBE_TTL_HOURS, default 24h, floor 5 minutes)."""
    try:
        hours = float(os.environ.get(TTL_ENV, "") or DEFAULT_TTL_HOURS)
    except ValueError:
        hours = DEFAULT_TTL_HOURS
    return max(300.0, hours * 3600.0)


_last_probe: dict[str, float] = {}
_throttle_lock = threading.Lock()


def _throttle(host: str, interval: float = PROBE_INTERVAL) -> None:
    """Reserve the next probe slot for this host (shared by every thread), then sleep outside the lock."""
    with _throttle_lock:
        slot = max(time.monotonic(), _last_probe.get(host, 0.0) + interval)
        _last_probe[host] = slot
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


def mx_hosts(domain: str) -> list[str]:
    """MX exchanges of a domain, best preference first; empty when it has none (or DNS failed)."""
    resolver = dns.resolver.Resolver()
    resolver.lifetime = 4.0
    try:
        answer = resolver.resolve(domain, "MX")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, dns.exception.DNSException, OSError):
        return []
    hosts = [str(r.exchange).rstrip(".").lower() for r in sorted(answer, key=lambda r: r.preference)]
    return [h for h in hosts if h]


def _connect(host: str, timeout: float) -> smtplib.SMTP:
    return smtplib.SMTP(host=host, port=25, timeout=timeout)


def rcpt_probe(host: str, email: str, sender: str | None = None, timeout: float = 10.0) -> bool | None:
    """One RCPT TO against an MX host: True = accepted, False = rejected (5xx), None = unknown.

    Greylisting (4xx), timeouts and blocked ports answer nothing, so they are never turned into a
    verdict. `MAIL FROM` is either PROSPECTOR_PROBE_FROM or the null reverse path - never a sender we
    do not control.
    """
    _throttle(host)
    try:
        with _connect(host, timeout) as smtp:
            smtp.ehlo_or_helo_if_needed()
            code, _resp = smtp.mail(sender if sender is not None else probe_sender())
            if code >= 400:
                return None  # the server refused our sender: nothing can be concluded about the recipient
            code, _resp = smtp.rcpt(email)
    except (smtplib.SMTPException, OSError):
        return None
    if 250 <= code <= 252:
        return True
    if 500 <= code < 600:
        return False
    return None


def probe_address(email: str) -> bool | None:
    """RCPT-probe one address through the domain's first MX host; None when probing is off or blocked."""
    if not probe_enabled():
        return None
    domain = email.rsplit("@", 1)[-1].strip().lower()
    hosts = mx_hosts(domain)
    if not hosts:
        return None
    return rcpt_probe(hosts[0], email)


def random_localpart() -> str:
    """A local part that cannot already be somebody's mailbox."""
    return f"probe.{token_hex(8)}"


def is_catchall(domain: str, probe: Probe | None = None, attempts: int = 2) -> bool | None:
    """True when the domain accepts mail for addresses that were never created (a catch-all).

    One random local part accepted means the server accepts everything, so RCPT acceptance there proves
    nothing. False as soon as one attempt is rejected; None when the probes could not answer at all
    (probing off, DNS down, port 25 blocked).
    """
    fn = probe or probe_address
    wanted = max(1, attempts)
    accepted = 0
    for _ in range(wanted):
        answer = fn(f"{random_localpart()}@{domain}")
        if answer is False:
            return False
        if answer is True:
            accepted += 1
    return True if accepted == wanted else None


@dataclass(frozen=True)
class ProbeResult:
    """One probe verdict: what the mail server said, why, when - and whether it came from the cache."""

    email: str
    status: str  # accepted | rejected | catch-all | unknown | disabled
    reason: str
    source: str = "smtp-rcpt"
    checked_at: datetime = field(default_factory=utcnow)
    cached: bool = False


class _TtlCache:
    """Probe verdicts keyed by email, so a TTL window never probes the same address twice."""

    def __init__(self, max_entries: int = 512) -> None:
        self._store: dict[str, tuple[ProbeResult, float]] = {}
        self._lock = threading.Lock()
        self._max = max_entries

    def get(self, key: str) -> ProbeResult | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expires = entry
            if time.monotonic() > expires:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: ProbeResult) -> None:
        expires = time.monotonic() + probe_ttl()
        with self._lock:
            self._store[key] = (value, expires)
            while len(self._store) > self._max:
                self._store.pop(next(iter(self._store)))

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_cache = _TtlCache()


def verify_email(email: str, probe: Probe | None = None) -> ProbeResult:
    """Is this specific address accepted by its mail server? Config-gated, catch-all aware, cached.

    The domain is probed for catch-all behaviour first: if it accepts made-up addresses, the result is
    reported as "catch-all" instead of a false "accepted". Verdicts that concluded something are cached
    with a TTL; "unknown" and "disabled" are not (a retry may answer differently).
    """
    key = (email or "").strip().lower()
    if not key or key.count("@") != 1:
        return ProbeResult(email=key, status="unknown", reason="not an email address")
    hit = _cache.get(key)
    if hit is not None:
        return replace(hit, cached=True)
    if probe is None and not probe_enabled():
        return ProbeResult(email=key, status="disabled", reason=f"SMTP probing is off (set {PROBE_ENV}=1)")
    fn = probe or probe_address
    domain = key.rsplit("@", 1)[1]
    catchall = is_catchall(domain, probe=fn)
    if catchall is None:
        result = ProbeResult(email=key, status="unknown", reason="the mail server did not answer; nothing claimed")
    elif catchall:
        result = ProbeResult(
            email=key,
            status="catch-all",
            reason=f"{domain} accepts any local part (catch-all): acceptance proves nothing",
        )
    else:
        accepted = fn(key)
        if accepted is True:
            result = ProbeResult(email=key, status="accepted", reason="the mail server accepted the address (RCPT TO)")
        elif accepted is False:
            result = ProbeResult(email=key, status="rejected", reason="the mail server rejected the address (5xx)")
        else:
            result = ProbeResult(email=key, status="unknown", reason="the mail server did not answer; nothing claimed")
    if result.status != "unknown":
        _cache.set(key, result)
    return result
