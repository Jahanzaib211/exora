"""GLEIF: the global Legal Entity Identifier registry (CC0). Strong for KSA, EU and UK registered entities."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..resolve import CompanyRecord, api_evidence
from .base import Connector, ParamSpec

API = "https://api.gleif.org/api/v1/lei-records"


class GleifConnector(Connector):
    key = "gleif"
    name = "GLEIF legal entities"
    description = "Registered legal entities by country and keyword, with registry status and legal address."
    licence = "CC0"
    params = [
        ParamSpec("country", "Country (ISO-2)", "text", True, default="SA"),
        ParamSpec("query", "Keyword", "text", True, default="law", help="Matched against entity names"),
        ParamSpec(
            "segment", "Segment", "select", False, ["Law", "CPA", "MSP", "Healthcare", "KSA SME", "EU mid-market"]
        ),
        ParamSpec("limit", "Max records", "int", False, default=200),
    ]

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        limit = int(params.get("limit") or 200)
        page, n_out = 1, 0
        while n_out < limit:
            q = {
                "filter[entity.legalAddress.country]": params["country"].upper(),
                "filter[fulltext]": params["query"],
                "page[size]": min(100, limit),
                "page[number]": page,
            }
            body = self.http.get(API, params=q, interval=1.0).json()
            rows = body.get("data", [])
            for row in rows:
                ent = row["attributes"]["entity"]
                addr = ent.get("legalAddress") or {}
                status = ent.get("status")
                url = f"https://search.gleif.org/#/record/{row['id']}"
                yield CompanyRecord(
                    source="gleif",
                    url=url,
                    evidence=[api_evidence(url, row)],
                    name=ent["legalName"]["name"],
                    country=addr.get("country"),
                    city=(addr.get("city") or "").title() or None,
                    address=", ".join(x for x in [*addr.get("addressLines", []), addr.get("postalCode")] if x),
                    segment=params.get("segment"),
                    lei=row["id"],
                    company_number=(ent.get("registeredAs") or None),
                    registry_status=status if status and status != "NULL" else None,
                    confidence=0.95,
                )
                n_out += 1
                if n_out >= limit:
                    return
            if page >= body.get("meta", {}).get("pagination", {}).get("lastPage", page):
                return
            page += 1
