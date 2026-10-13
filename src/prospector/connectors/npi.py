"""US NPI Registry (CMS NPPES): health-care organisations with their authorised official.

Free, keyless public API of the US Centers for Medicare & Medicaid Services. Every organisation record names
an authorised official (name, title, phone) who is legally responsible for it: a verified decision-maker for
the Healthcare segment. Only organisation records (NPI-2) are used, never individual practitioners.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from ..resolve import CompanyRecord, PersonRecord, api_evidence
from .base import Connector, ParamSpec

API = "https://npiregistry.cms.hhs.gov/api/"
PUBLIC = "https://npiregistry.cms.hhs.gov/provider-view/"
PAGE = 200  # API maximum per request
MAX_SKIP = 1000  # API maximum offset


KEEP_UPPER = {"LLC", "PLLC", "LLP", "PC", "PA", "INC", "LTD", "LP", "DDS", "MD", "DO", "PT", "OT", "USA", "US"}
SMALL = {"of", "and", "the", "for", "at", "in", "on", "&"}


def _title(value: str | None) -> str | None:
    """Title-case an ALL-CAPS registry name, keeping acronyms and legal suffixes: 'A BALANCED LIFE ATX, PLLC'
    -> 'A Balanced Life ATX, PLLC'."""
    words = []
    for i, w in enumerate((value or "").split()):
        core = re.sub(r"[^A-Za-z]", "", w)
        if core.upper() in ("ST", "MT", "FT"):  # Saint, Mount, Fort
            words.append(w[:1].upper() + w[1:].lower())
        elif core.upper() in KEEP_UPPER or (0 < len(core) <= 3 and core.lower() not in SMALL and w.isupper()):
            words.append(w.upper())
        elif i and core.lower() in SMALL:
            words.append(w.lower())
        else:
            words.append(w[:1].upper() + w[1:].lower())
    return " ".join(words) or None


class NpiConnector(Connector):
    key = "npi"
    name = "US NPI Registry (health care)"
    description = "US clinics and medical groups from the federal NPI register, each with its authorised official."
    licence = "US public data (CMS)"
    params = [
        ParamSpec("state", "State (2 letters)", "text", True, default="TX"),
        ParamSpec("city", "City", "text", False, help="Optional, e.g. Austin"),
        ParamSpec("taxonomy", "Specialty", "text", False, default="clinic",
                  help="Words from the provider type, e.g. clinic, dental, physical therapy"),
        ParamSpec("limit", "Max records", "int", False, default=200),
    ]  # fmt: skip

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        state = str(params["state"]).upper()[:2]
        limit = int(params.get("limit") or 200)
        skip, n = 0, 0
        while n < limit and skip <= MAX_SKIP:
            q = {
                "version": "2.1",
                "enumeration_type": "NPI-2",
                "state": state,
                "limit": min(PAGE, limit - n),
                "skip": skip,
            }
            if params.get("city"):
                q["city"] = str(params["city"]).upper()
            if params.get("taxonomy"):
                q["taxonomy_description"] = params["taxonomy"]
            body = self.http.get(API, params=q, interval=1.0).json()
            results = body.get("results") or []
            if not results:
                return
            for r in results:
                basic = r.get("basic") or {}
                name = basic.get("organization_name")
                if not name:
                    continue
                addrs = r.get("addresses") or []
                loc = next((a for a in addrs if a.get("address_purpose") == "LOCATION"), addrs[0] if addrs else {})
                url = PUBLIC + str(r.get("number"))
                people = []
                first, last = basic.get("authorized_official_first_name"), basic.get("authorized_official_last_name")
                if first and last:
                    title = _title(basic.get("authorized_official_title_or_position"))
                    cred = basic.get("authorized_official_credential")
                    people.append(
                        PersonRecord(
                            name=f"{first} {last}".title(),
                            title=f"{title} ({cred})" if title and cred else title,
                            phone=basic.get("authorized_official_telephone_number"),
                            url=url,
                            excerpt=f"NPI {r.get('number')} authorised official: {first} {last}, "
                            f"{basic.get('authorized_official_title_or_position', '')}",
                        )
                    )
                taxonomy = next((t.get("desc") for t in r.get("taxonomies") or [] if t.get("primary")), None)
                yield CompanyRecord(
                    source="npi",
                    url=url,
                    name=_title(name) or name,
                    country="US",
                    city=(loc.get("city") or "").title() or None,
                    address=", ".join(
                        x
                        for x in (
                            loc.get("address_1"),
                            loc.get("address_2"),
                            (loc.get("city") or "").title(),
                            loc.get("state"),
                            (loc.get("postal_code") or "")[:5],
                        )
                        if x
                    )
                    or None,  # fmt: skip
                    phone=loc.get("telephone_number"),
                    segment="Healthcare",
                    company_number=str(r.get("number")),
                    registry_status="ACTIVE" if basic.get("status") == "A" else (basic.get("status") or None),
                    description=f"{taxonomy} (NPI organisation)" if taxonomy else None,
                    confidence=0.95,
                    people=people,
                    evidence=[api_evidence(url, r)],
                )
                n += 1
                if n >= limit:
                    return
            skip += len(results)
