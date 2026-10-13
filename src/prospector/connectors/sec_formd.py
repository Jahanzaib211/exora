"""SEC EDGAR Form D discovery: recent capital-raising notices, parsed from EDGAR (keyless).

A Form D is the public notice a company files with the SEC after selling securities: it carries the
issuer's name, registered address, an issuer switchboard phone, the offering amounts and the filing date -
a timestamped funding signal no other source here provides, plus a US address to match on.

Compliance: only what the issuer itself publishes in its filing (SEC EDGAR, public by statute). Officer
names are kept with their filing role and the filing as their receipt; the private addresses some filings
list for related persons are deliberately never read. The phone stored is the issuer's own published
number, kept as a data-quality field (never dialled or texted by the campaign).

Politeness: the SEC asks for a descriptive User-Agent and at most 10 requests/second; every request here
carries PROSPECTOR_SEC_USER_AGENT (with a contact address) and is paced by the shared PoliteClient.
"""

from __future__ import annotations

import hashlib
import os
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..resolve import CompanyRecord, EvidenceRecord, PersonRecord, api_evidence
from .base import Connector, ParamSpec, user_agent

SEARCH = "https://efts.sec.gov/LATEST/search-index"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
SEC_INTERVAL = 0.5  # SEC fair-access policy: at most 10 requests/second; we ask for far less

#: the 50 US states + DC, as spelled in an EDGAR issuer address (foreign issuers keep no country here)
US_STATES = frozenset(
    {
        "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
        "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM",
        "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
        "WV", "WI", "WY",
    }
)  # fmt: skip

#: a related person whose last word is one of these is a company, not a natural person
CORPORATE = frozenset(
    {
        "llc",
        "inc",
        "ltd",
        "limited",
        "co",
        "corp",
        "corporation",
        "lp",
        "llp",
        "plc",
        "pllc",
        "pc",
        "gmbh",
        "company",
        "holdings",
        "trust",
        "fund",
        "partners",
    }
)


def sec_headers() -> dict[str, str]:
    """EDGAR refuses anonymous clients: a descriptive User-Agent with contact details is mandatory.

    PROSPECTOR_SEC_USER_AGENT wins; otherwise "Exora Research (<PROSPECTOR_CONTACT_EMAIL>)"; otherwise the
    shared discovery User-Agent, which EDGAR may throttle because it carries no email address.
    """
    contact = os.environ.get("PROSPECTOR_CONTACT_EMAIL", "").strip()
    ua = (
        os.environ.get("PROSPECTOR_SEC_USER_AGENT", "").strip()
        or (f"Exora Research ({contact})" if contact else "")
        or user_agent()
    )
    return {"User-Agent": ua, "Accept": "application/json, text/xml;q=0.9, */*;q=0.5"}


def _tag(el: ET.Element) -> str:
    """Local tag name, ignoring any namespace."""
    return el.tag.rsplit("}", 1)[-1]


def _child(el: ET.Element, name: str) -> ET.Element | None:
    for sub in el:
        if _tag(sub) == name:
            return sub
    return None


def _or(el: ET.Element | None, fallback: ET.Element) -> ET.Element:
    """Never truth-test an Element (a childless one is falsy): test against None."""
    return el if el is not None else fallback


def _path(el: ET.Element | None, *names: str) -> str:
    """Text at el/name1/name2/... ('' as soon as a hop is missing)."""
    for name in names:
        if el is None:
            return ""
        el = _child(el, name)
    return (el.text or "").strip() if el is not None else ""


def _find(el: ET.Element, name: str) -> ET.Element | None:
    """First descendant with a matching local tag (namespace-agnostic)."""
    for sub in el.iter():
        if _tag(sub) == name:
            return sub
    return None


def _is_person(first: str, last: str) -> bool:
    """False for the 'N/A' placeholders EDGAR uses for entities and for company names."""
    if not first or not last or first.upper() == "N/A" or last.upper() == "N/A":
        return False
    words = f"{first} {last}".strip().lower().split()
    return words[-1].rstrip(".") not in CORPORATE and words[0].rstrip(".") not in CORPORATE


def _money(raw: str) -> str | None:
    """'317561' -> '$317,561'; anything not plainly a number stays out of the signal."""
    digits = raw.replace(",", "").strip()
    if not digits.isdigit():
        return None
    return f"${int(digits):,}"


def _file_evidence(url: str, text: str, title: str | None) -> EvidenceRecord:
    """A receipt for the XML exactly as received (SHA-256 of the raw bytes)."""
    raw = text.encode("utf-8", "replace")
    return EvidenceRecord(url=url, kind="file", sha256=hashlib.sha256(raw).hexdigest(), title=title, size=len(raw))


class SecFormDConnector(Connector):
    key = "sec-formd"
    name = "SEC Form D filings"
    description = (
        "Recent Form D notices on EDGAR: who just raised money, with the filing amount, the issuer's "
        "address and switchboard. Keyless."
    )
    licence = "SEC EDGAR public filings"
    params = [
        ParamSpec("window_days", "Look back (days)", "int", False, default=30),
        ParamSpec("limit", "Max filings", "int", False, default=25),
    ]

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        window = max(1, min(int(params.get("window_days") or 30), 365))
        limit = max(1, min(int(params.get("limit") or 25), 500))
        today = datetime.now(UTC).date()
        start = today - timedelta(days=window)
        query = {"forms": "D", "startdt": start.isoformat(), "enddt": today.isoformat(), "from": 0}
        try:
            body = self.http.get(SEARCH, params=query, headers=sec_headers(), interval=SEC_INTERVAL, timeout=60).json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                # EDGAR blocks clients without a contact address. Reporting "0 found" here would look like a
                # quiet week, so fail the run with the fix instead.
                raise RuntimeError(
                    "SEC EDGAR refused the request (HTTP 403): it requires a contact email in the User-Agent. "
                    "Set PROSPECTOR_CONTACT_EMAIL (or PROSPECTOR_SEC_USER_AGENT) in .env and restart."
                ) from exc
            return  # EDGAR unavailable or rate-limited: the run ends quietly, nothing is claimed
        except Exception:
            return  # EDGAR unavailable or rate-limited: the run ends quietly, nothing is claimed
        hits = ((body or {}).get("hits") or {}).get("hits") or []
        evidence = [api_evidence(f"{SEARCH}?forms=D&startdt={start}&enddt={today}", body)]
        for hit in hits[:limit]:
            rec = self._filing(hit, evidence)
            if rec is not None:
                yield rec

    def _filing(self, hit: dict[str, Any], evidence: list[EvidenceRecord]) -> CompanyRecord | None:
        src = hit.get("_source") or {}
        hit_id = str(hit.get("_id") or "")
        ciks = src.get("ciks") or []
        if ":" not in hit_id or not ciks:
            return None
        adsh, primary = hit_id.split(":", 1)
        cik = str(ciks[0])
        url = f"{ARCHIVES}/{int(cik)}/{adsh.replace('-', '')}/{primary}"
        try:
            xml_text = self.http.get(url, headers=sec_headers(), interval=SEC_INTERVAL, timeout=60).text
        except Exception:
            return None  # one unreadable filing must not sink the batch
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None
        issuer = _or(_child(root, "primaryIssuer"), root)
        name = _path(issuer, "entityName")
        if not name:
            return None
        address = _child(issuer, "issuerAddress")
        street = _path(address, "street1")
        street2 = _path(address, "street2")
        city = _path(address, "city")
        state = _path(address, "stateOrCountry")
        zip_code = _path(address, "zipCode")
        phone = _path(issuer, "issuerPhoneNumber") or None
        file_date = str(src.get("file_date") or "")
        signals = [(kind, title, link) for kind, title, link in self._signals(root, url, file_date)]

        return CompanyRecord(
            source="sec-formd",
            url=url,
            name=name,
            country="US" if state.upper() in US_STATES else None,
            city=city.title() or None,
            address=", ".join(x for x in (street, street2, city, state, zip_code) if x) or None,
            phone=phone,
            company_number=cik.lstrip("0") or cik,
            segment=_path(_or(_find(root, "industryGroup"), issuer), "industryGroupType") or None,
            description=_path(issuer, "entityType") or None,
            confidence=0.8,
            people=self._people(root, url),
            signals=signals,
            evidence=[*evidence, _file_evidence(url, xml_text, name)],
        )

    def _signals(self, root: ET.Element, url: str, file_date: str) -> list[tuple[str, str, str]]:
        """('funding', ...) when the filing states how much was raised and when selling started."""
        filing = _or(_find(root, "typeOfFiling"), root)
        first_sale = _path(filing, "dateOfFirstSale", "value")
        amounts = _find(root, "offeringSalesAmounts")
        offered = _money(_path(amounts, "totalOfferingAmount"))
        sold = _money(_path(amounts, "totalAmountSold"))
        when = first_sale or file_date
        if not when:
            return []
        parts = ["Form D filed " + when]
        if sold and offered:
            parts.append(f"raised {sold} of {offered} offered")
        elif offered:
            parts.append(f"offering {offered}")
        parts.append(f"first sale {first_sale}" if first_sale else "no first-sale date stated")
        return [("funding", "; ".join(parts), url)]

    def _people(self, root: ET.Element, url: str) -> list[PersonRecord]:
        """Officers and directors named in the filing, with their filing role as the title."""
        out: list[PersonRecord] = []
        persons = _find(root, "relatedPersonsList")
        if persons is None:
            return out
        for info in persons:
            if _tag(info) != "relatedPersonInfo":
                continue
            first = _path(info, "relatedPersonName", "firstName")
            last = _path(info, "relatedPersonName", "lastName")
            if not _is_person(first, last):
                continue  # EDGAR's entity rows: not a natural person
            relationship = _path(info, "relatedPersonRelationshipList", "relationship")
            clarification = _path(info, "relationshipClarification")  # the filing's own words for the role
            name = f"{first} {last}".strip()
            out.append(
                PersonRecord(
                    name=name,
                    title=clarification or relationship or None,
                    url=url,
                    excerpt=f"SEC Form D: {relationship or 'related person'}"
                    + (f" - {clarification}" if clarification else ""),
                )
            )
        return out
