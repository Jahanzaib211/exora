"""Offline phone validation with `phonenumbers` (Apache-2.0) - a data-quality field only.

Parses, classifies and normalizes phone numbers to E.164 without any network access or API key:
invalid vs merely possible, fixed-line vs mobile (where the number plans allow it), and the region the
number belongs to. The campaign does not dial or text anyone: a phone number is stored so the team can
see what the company published and so exports stay consistent (KSA PDPL / UK PECR - phone is never a
marketing channel here).

The module is deliberately dependency-free apart from `phonenumbers`, so the crawler, the resolver and the
maintenance checks all judge a number the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

import phonenumbers

from ..models import utcnow

SOURCE = "phonenumbers"
VERDICTS = ("valid", "possible", "invalid")
TYPE_NAMES = {
    phonenumbers.PhoneNumberType.FIXED_LINE: "fixed-line",
    phonenumbers.PhoneNumberType.MOBILE: "mobile",
    phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed-line or mobile",
    phonenumbers.PhoneNumberType.TOLL_FREE: "toll-free",
    phonenumbers.PhoneNumberType.PREMIUM_RATE: "premium-rate",
    phonenumbers.PhoneNumberType.SHARED_COST: "shared-cost",
    phonenumbers.PhoneNumberType.VOIP: "VoIP",
    phonenumbers.PhoneNumberType.PERSONAL_NUMBER: "personal",
    phonenumbers.PhoneNumberType.PAGER: "pager",
    phonenumbers.PhoneNumberType.UAN: "universal access",
    phonenumbers.PhoneNumberType.VOICEMAIL: "voicemail",
    phonenumbers.PhoneNumberType.UNKNOWN: "unknown type",
}
STRIP = re.compile(r"[^\d+]")


@dataclass(frozen=True)
class PhoneVerdict:
    """How a number stands, with the reason and when it was judged (stored as a contact check)."""

    raw: str
    verdict: str  # valid | possible | invalid
    reason: str
    e164: str | None = None  # set only when verdict is "valid"
    number_type: str | None = None  # mobile | fixed-line | fixed-line or mobile | ...
    region: str | None = None  # ISO region inferred from the number itself, else the region we parsed with
    source: str = SOURCE
    checked_at: datetime = field(default_factory=utcnow)


def analyze(raw: str | None, region: str | None = None) -> PhoneVerdict:
    """Judge one number. `region` (ISO-2) is the hint used for local formats without a country code."""
    text = (raw or "").strip()
    if not text:
        return PhoneVerdict(raw=str(raw or ""), verdict="invalid", reason="empty value")
    try:
        number = phonenumbers.parse(text, region or None)
    except phonenumbers.NumberParseException as exc:
        return PhoneVerdict(raw=text, verdict="invalid", reason=f"cannot parse: {exc}")
    inferred = phonenumbers.region_code_for_number(number)
    if not inferred and region and len(region) == 2:
        inferred = region.upper()
    where = f"region {inferred}" if inferred else "region unknown"
    ntype = TYPE_NAMES.get(phonenumbers.number_type(number), "unknown type")
    if phonenumbers.is_valid_number(number):
        return PhoneVerdict(
            raw=text,
            verdict="valid",
            reason=f"valid {ntype} number, {where}",
            e164=phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164),
            number_type=ntype,
            region=inferred,
        )
    if phonenumbers.is_possible_number(number):
        return PhoneVerdict(
            raw=text,
            verdict="possible",
            reason=f"possible but unconfirmed: the {ntype} pattern is not a known allocation, {where}",
            number_type=ntype,
            region=inferred,
        )
    return PhoneVerdict(raw=text, verdict="invalid", reason=f"not a possible number (wrong length), {where}")


def e164(raw: str | None, region: str | None = None) -> str | None:
    """E.164 form of a number that is valid for its region; None when it must not be stored as usable."""
    v = analyze(raw, region)
    return v.e164 if v.verdict == "valid" else None


def candidate(raw: str | None, region: str | None = None) -> str | None:
    """Clean a telephone token found on a page before it reaches the database.

    International numbers (+...) come back as E.164 when valid, as digits when merely possible, and are
    dropped when impossible. Local formats survive as digits when no region is known yet: the company's
    country (filled in during resolve) finishes the job through `e164`.
    """
    if not raw:
        return None
    cleaned = STRIP.sub("", str(raw).strip())
    if not cleaned or "+" in cleaned[1:]:
        return None
    digits = cleaned[1:] if cleaned.startswith("+") else cleaned
    if not 7 <= len(digits) <= 15:
        return None
    if cleaned.startswith("+"):
        try:
            number = phonenumbers.parse(cleaned, None)
        except phonenumbers.NumberParseException:
            return None
        if phonenumbers.is_valid_number(number):
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        return cleaned if phonenumbers.is_possible_number(number) else None
    if region:
        try:
            number = phonenumbers.parse(digits, region)
        except phonenumbers.NumberParseException:
            return None
        if phonenumbers.is_valid_number(number):
            return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
        return digits if phonenumbers.is_possible_number(number) else None
    return digits
