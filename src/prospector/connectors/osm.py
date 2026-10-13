"""OpenStreetMap: professional offices by type within a city (Nominatim geocoding + Overpass query).

Data © OpenStreetMap contributors, ODbL. Usage policies: Nominatim max 1 request/s with an identifying
User-Agent; Overpass is shared infrastructure, so queries are bounded to one city's bounding box.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from ..resolve import CompanyRecord, api_evidence
from .base import Connector, ParamSpec

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"]


def overpass_endpoints() -> list[str]:
    """Public Overpass servers are shared and often busy; PROSPECTOR_OVERPASS_URL (comma-separated) puts your own
    or a paid instance first, with the public ones kept as fallbacks."""
    own = [u.strip() for u in os.environ.get("PROSPECTOR_OVERPASS_URL", "").split(",") if u.strip()]
    return own + [u for u in OVERPASS if u not in own]


# segment -> OSM tag filters
SEGMENT_TAGS: dict[str, list[str]] = {
    "Law": ['["office"="lawyer"]', '["office"="notary"]'],
    "CPA": ['["office"="accountant"]', '["office"="tax_advisor"]'],
    "MSP": ['["office"="it"]', '["shop"="computer"]["service:computer:repair"]'],
    "Healthcare": ['["healthcare"="clinic"]', '["amenity"="clinic"]', '["healthcare"="doctor"]'],
}


class OsmConnector(Connector):
    key = "osm"
    name = "OpenStreetMap offices"
    description = (
        "Law firms, accountants, IT providers and clinics mapped in a city, with website and phone where tagged."
    )
    licence = "ODbL (© OpenStreetMap contributors)"
    params = [
        ParamSpec("segment", "Segment", "select", True, list(SEGMENT_TAGS), "Law"),
        ParamSpec("city", "City", "text", True, default="Manchester", help="City name as on a map, e.g. Riyadh"),
        ParamSpec("country", "Country (ISO-2)", "text", False, default="GB"),
        ParamSpec("limit", "Max records", "int", False, default=200),
    ]

    def bbox(self, city: str, country: str | None) -> tuple[list[float], str]:
        params = {"city": city, "format": "jsonv2", "limit": 1, "addressdetails": 1}
        if country:
            params["countrycodes"] = country.lower()
        hits = self.http.get(NOMINATIM, params=params, interval=1.1).json()
        if not hits:
            raise ValueError(f"city not found: {city}")
        south, north, west, east = (float(x) for x in hits[0]["boundingbox"])
        cc = (hits[0].get("address", {}).get("country_code") or country or "").upper()
        return [south, west, north, east], cc

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        segment, city = params["segment"], params["city"]
        (s, w, n, e), cc = self.bbox(city, params.get("country"))
        filters = "".join(f"nwr{t}({s},{w},{n},{e});" for t in SEGMENT_TAGS[segment])
        query = f"[out:json][timeout:60];({filters});out tags center;"
        data = None
        last_exc: Exception | None = None
        for endpoint in overpass_endpoints():
            try:
                data = self.http.post(endpoint, data={"data": query}, interval=2.0, timeout=90).json()
                break
            except Exception as exc:  # busy mirror: try the next one
                last_exc = exc
        if data is None:
            raise RuntimeError(
                f"all Overpass endpoints failed ({last_exc}). The public servers are often overloaded: retry the "
                "run later, or set PROSPECTOR_OVERPASS_URL to your own Overpass instance."
            )
        limit = int(params.get("limit") or 200)
        n_out = 0
        for el in data.get("elements", []):
            t = el.get("tags", {})
            name = t.get("name") or t.get("operator")
            if not name:
                continue
            addr = ", ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street"), t.get("addr:postcode")) if x)
            url = f"https://www.openstreetmap.org/{el['type']}/{el['id']}"
            yield CompanyRecord(
                source="osm",
                url=url,
                evidence=[api_evidence(url, el)],
                name=name,
                website=t.get("website") or t.get("contact:website") or t.get("url"),
                country=cc or params.get("country"),
                city=t.get("addr:city") or city,
                address=addr or None,
                segment=segment,
                phone=t.get("phone") or t.get("contact:phone"),
                general_email=t.get("email") or t.get("contact:email"),
                confidence=0.7,
            )
            n_out += 1
            if n_out >= limit:
                return
