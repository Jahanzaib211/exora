"""Normalization used by every connector, so the same company looks the same whatever its source."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlparse

import tldextract

from .contact.phone import e164

# Offline: use the public-suffix snapshot bundled with tldextract, never fetch it at runtime.
_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)

LEGAL_SUFFIXES = re.compile(
    r"\b(llp|llc|ltd|limited|inc|incorporated|corp|corporation|plc|gmbh|ag|sa|sas|sarl|bv|nv|srl|spa|pllc|pc|lp|co|"
    r"company|group|holdings?|partners?hip|& co|and co|est|w\.?l\.?l|l\.?l\.?c)\b\.?",
    re.IGNORECASE,
)
COUNTRY_ALIASES = {
    "united states": "US",
    "usa": "US",
    "us": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "gb": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "saudi arabia": "SA",
    "ksa": "SA",
    "sa": "SA",
    "germany": "DE",
    "de": "DE",
    "france": "FR",
    "fr": "FR",
    "netherlands": "NL",
    "nl": "NL",
    "ireland": "IE",
    "ie": "IE",
    "spain": "ES",
    "es": "ES",
    "italy": "IT",
    "it": "IT",
    "sweden": "SE",
    "se": "SE",
    "denmark": "DK",
    "dk": "DK",
    "belgium": "BE",
    "be": "BE",
    "portugal": "PT",
    "pt": "PT",
    "poland": "PL",
    "pl": "PL",
    "austria": "AT",
    "at": "AT",
    "finland": "FI",
    "fi": "FI",
    "united arab emirates": "AE",
    "uae": "AE",
    "ae": "AE",
    "switzerland": "CH",
    "ch": "CH",
}
FREE_MAIL = {
    "gmail.com",
    "googlemail.com",
    "outlook.com",
    "hotmail.com",
    "yahoo.com",
    "icloud.com",
    "live.com",
    "aol.com",
    "proton.me",
    "protonmail.com",
    "gmx.de",
    "web.de",
    "yandex.com",
    "mail.com",
    "msn.com",
}
ROLE_LOCALPARTS = {
    "info",
    "hello",
    "contact",
    "enquiries",
    "enquiry",
    "inquiries",
    "office",
    "admin",
    "sales",
    "support",
    "help",
    "team",
    "mail",
    "reception",
    "general",
    "accounts",
    "billing",
    "marketing",
    "press",
    "media",
    "pr",
    "publicrelations",
    "webmaster",
    "careers",
    "jobs",
    "hr",
    "legal",
    "privacy",
    "noreply",
    "no-reply",
    "wecare",
    "service",
    "services",
    "clients",
    "security",
    "abuse",
    "dpo",
    "dataprotection",
    "gdpr",
    "compliance",
    "postmaster",
    "hostmaster",
    "investors",
    "ir",
    "recruitment",
    "vacancies",
}
# Shared inboxes that never count as a company's sales contact, even though they are role addresses.
NON_CONTACT_LOCALPARTS = {
    "security", "abuse", "privacy", "dpo", "dataprotection", "gdpr", "compliance", "legal", "postmaster",
    "hostmaster", "webmaster", "noreply", "no-reply", "careers", "jobs", "hr", "recruitment", "vacancies",
    "investors", "ir", "press", "media", "pr", "publicrelations", "billing", "accounts",
}  # fmt: skip
# Preferred order when choosing the company's general contact address.
CONTACT_PREFERENCE = [
    "info",
    "hello",
    "contact",
    "enquiries",
    "enquiry",
    "inquiries",
    "office",
    "sales",
    "team",
    "mail",
]


def best_contact_email(emails: list[str]) -> str | None:
    """Pick the shared inbox a prospect would expect sales mail at; never security@, privacy@, careers@..."""
    usable = [e for e in emails if e.split("@", 1)[0].lower() not in NON_CONTACT_LOCALPARTS]
    if not usable:
        return None
    rank = {name: i for i, name in enumerate(CONTACT_PREFERENCE)}
    return min(usable, key=lambda e: (rank.get(e.split("@", 1)[0].lower(), len(rank)), e))


SENIORITY = [
    ("owner", r"\b(owner|founder|co-?founder|proprietor|managing member|sole member)\b"),
    ("c_level", r"\b(ceo|cfo|cto|cio|coo|ciso|chief\b.*\b(officer|executive)|president)\b"),
    ("partner", r"\b(managing partner|senior partner|equity partner|partner|principal)\b"),
    ("director", r"\b(managing director|director|head of|vp|vice president)\b"),
    ("manager", r"\b(manager|lead|practice manager|office manager|administrator)\b"),
]


def domain_of(url_or_host: str | None) -> str | None:
    """Registrable domain: 'https://www.smithlaw.co.uk/team' -> 'smithlaw.co.uk'."""
    if not url_or_host:
        return None
    raw = url_or_host.strip()
    if "@" in raw and "://" not in raw:
        raw = raw.rsplit("@", 1)[1]
    host = urlparse(raw if "://" in raw else "http://" + raw).hostname or ""
    ext = _extract(host)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}".lower()


def website_of(url: str | None) -> str | None:
    if not url:
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc.lower()}/" if p.netloc else None


def country_code(value: str | None) -> str | None:
    if not value:
        return None
    v = re.sub(r"\(.*?\)", "", value).strip()  # "US (HQ not stated in source)" -> "US"
    if v.lower() in COUNTRY_ALIASES:  # before the ISO check: "UK" is not ISO-3166 (it is GB)
        return COUNTRY_ALIASES[v.lower()]
    if len(v) == 2 and v.isalpha():
        return v.upper()
    return None


def phone_e164(raw: str | None, country: str | None) -> str | None:
    """E.164 of a number that is valid for the country (or for the number itself when written in + form).

    The judging lives in contact.phone (offline `phonenumbers`): possible-but-unallocated and impossible
    numbers are never stored as usable, so every consumer of a stored number sees the same verdict.
    """
    return e164(raw, country)


def ascii_fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def name_key(name: str) -> str:
    """Matching key for a company name: case/accents/punctuation/legal suffix insensitive."""
    s = ascii_fold(name).lower().replace("&", " and ")
    s = LEGAL_SUFFIXES.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def person_key(name: str) -> str:
    s = ascii_fold(name).lower()
    s = re.sub(r"\b(mr|mrs|ms|miss|dr|prof|sir|dame)\.?\s+", "", s)
    return re.sub(r"[^a-z]+", " ", s).strip()


def seniority_of(title: str | None) -> str | None:
    if not title:
        return None
    for level, pattern in SENIORITY:
        if re.search(pattern, title, re.IGNORECASE):
            return level
    return "staff"


def email_kind(email: str, company_domain: str | None) -> str:
    """published (named, on the company's domain) | role (info@...) | off_domain | free_mail."""
    local, _, dom = email.lower().partition("@")
    reg = domain_of(dom)
    if reg in FREE_MAIL:
        return "free_mail"
    if company_domain and reg != company_domain:
        return "off_domain"
    if local in ROLE_LOCALPARTS or re.fullmatch(r"(info|office|admin)[0-9._-]*\w*", local):
        return "role"
    return "published"
