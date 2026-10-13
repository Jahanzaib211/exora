"""UK Companies House: active companies by SIC code and location, with their current officers.

Needs a free API key (https://developer.company-information.service.gov.uk/), set as
PROSPECTOR_COMPANIES_HOUSE_KEY. Public register data under the Open Government Licence. Officers are listed
publicly by statute; they are stored as business contacts with the register as the source.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..resolve import CompanyRecord, PersonRecord, api_evidence
from .base import Connector, ParamSpec

API = "https://api.company-information.service.gov.uk"
PUBLIC = "https://find-and-update.company-information.service.gov.uk/company/"
# Segment -> UK SIC 2007 codes
SIC = {
    "Law": ["69102", "69101", "69109"],
    "CPA": ["69201", "69202", "69203"],
    "MSP": ["62020", "62030", "62090", "63110"],
    "Healthcare": ["86210", "86220", "86230", "86900"],
}
ROLE_TITLE = {
    "director": "Director",
    "llp-designated-member": "Designated Member",
    "llp-member": "Member",
    "corporate-director": None,  # a company, not a person
    "secretary": "Company Secretary",
}


def officer_name(raw: str) -> str:
    """'SMITH, Jane Elizabeth' -> 'Jane Elizabeth Smith'."""
    if "," not in raw:
        return " ".join(w.capitalize() for w in raw.split())
    surname, given = raw.split(",", 1)
    given = given.strip()
    for title in ("Mr ", "Mrs ", "Ms ", "Miss ", "Dr "):
        given = given.removeprefix(title)
    return f"{given} {surname.strip().title()}".strip()


class CompaniesHouseConnector(Connector):
    key = "companies-house"
    name = "Companies House (UK)"
    description = "Active UK companies by industry and location, with registered office and current directors."
    licence = "UK Open Government Licence"
    needs_key = "companies_house_key"
    params = [
        ParamSpec("segment", "Segment", "select", True, list(SIC), "Law"),
        ParamSpec("location", "Location", "text", True, default="Manchester", help="Town or city in the address"),
        ParamSpec("limit", "Max companies", "int", False, default=200),
        ParamSpec("officers", "Include directors", "select", False, ["yes", "no"], "yes"),
    ]

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        if not self.api_key:
            raise RuntimeError("Companies House API key is not configured (PROSPECTOR_COMPANIES_HOUSE_KEY)")
        auth = (self.api_key, "")
        limit = int(params.get("limit") or 200)
        segment = params["segment"]
        start, n = 0, 0
        while n < limit:
            body = self.http.get(
                f"{API}/advanced-search/companies",
                params={
                    "company_status": "active",
                    "sic_codes": ",".join(SIC[segment]),
                    "location": params["location"],
                    "size": min(100, limit - n),
                    "start_index": start,
                },
                auth=auth,
                interval=0.6,  # API limit: 600 requests per 5 minutes
            ).json()
            items = body.get("items") or []
            if not items:
                return
            for it in items:
                number = it.get("company_number")
                addr = it.get("registered_office_address") or {}
                url = PUBLIC + number if number else f"{API}/advanced-search/companies"
                evidence = [api_evidence(url, it)]
                people: list[PersonRecord] = []
                if params.get("officers", "yes") == "yes" and number:
                    people, officers_body = self._officers(number, auth)
                    if officers_body is not None:
                        evidence.append(api_evidence(PUBLIC + number + "/officers", officers_body))
                yield CompanyRecord(
                    source="companies-house",
                    url=url,
                    evidence=evidence,
                    name=it.get("company_name", "").strip(),
                    country="GB",
                    city=(addr.get("locality") or "").title() or None,
                    address=", ".join(
                        x
                        for x in (
                            addr.get("address_line_1"),
                            addr.get("address_line_2"),
                            addr.get("locality"),
                            addr.get("postal_code"),
                        )
                        if x
                    )
                    or None,  # fmt: skip
                    segment=segment,
                    company_number=number,
                    registry_status=(it.get("company_status") or "").upper() or None,
                    confidence=0.95,
                    people=people,
                )
                n += 1
                if n >= limit:
                    return
            start += len(items)
            hits = body.get("hits")
            if isinstance(hits, int) and start >= hits:
                return  # all results fetched; never loop on a repeated last page

    def _officers(self, number: str, auth: tuple[str, str]) -> tuple[list[PersonRecord], dict | None]:
        try:
            body = self.http.get(
                f"{API}/company/{number}/officers", params={"items_per_page": 20}, auth=auth, interval=0.6
            ).json()
        except Exception:
            return [], None
        out = []
        for o in body.get("items") or []:
            title = ROLE_TITLE.get(o.get("officer_role", ""), "Officer")
            if o.get("resigned_on") or title is None or not o.get("name"):
                continue
            out.append(
                PersonRecord(
                    name=officer_name(o["name"]),
                    title=title,
                    url=PUBLIC + number + "/officers",
                    excerpt=f"Companies House officer: {o['name']} · {o.get('officer_role', '')}"
                    + (f" · appointed {o['appointed_on']}" if o.get("appointed_on") else ""),
                )
            )
        return out, body
