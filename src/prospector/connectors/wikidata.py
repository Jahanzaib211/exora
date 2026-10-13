"""Wikidata: organisations by type and country, with website, head office, employees and founding year (CC0)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..resolve import CompanyRecord, api_evidence
from .base import Connector, ParamSpec

ENDPOINT = "https://query.wikidata.org/sparql"
# Segment -> Wikidata classes (verified via wbsearchentities): law firm, accountancy firm, clinic,
# private hospital, dental clinic.
CLASSES = {
    "Law": ["Q613142"],
    "CPA": ["Q2781630"],
    "Healthcare": ["Q1774898", "Q4284971", "Q2979300"],
}

QUERY = """
SELECT ?item ?itemLabel ?website ?hqLabel ?employees ?inception WHERE {{
  VALUES ?class {{ {classes} }}
  ?item wdt:P31 ?class ; wdt:P17 ?country .
  ?country wdt:P297 "{country}" .
  OPTIONAL {{ ?item wdt:P856 ?website }}
  OPTIONAL {{ ?item wdt:P159 ?hq }}
  OPTIONAL {{ ?item wdt:P1128 ?employees }}
  OPTIONAL {{ ?item wdt:P571 ?inception }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}
LIMIT {limit}
"""


def size_band(employees: int | None) -> str | None:
    if not employees:
        return None
    for limit, label in ((10, "1–9"), (50, "10–49"), (250, "50–249"), (1000, "250–999")):
        if employees < limit:
            return f"{label} employees"
    return "1,000+ employees"


def _qid_label(value: str | None) -> bool:
    return bool(value) and value.startswith("Q") and value[1:].isdigit()


class WikidataConnector(Connector):
    key = "wikidata"
    name = "Wikidata organisations"
    description = (
        "Law firms, accountancy firms, clinics and hospitals by country, with website, head office, "
        "employees and founding year."
    )
    licence = "CC0"
    params = [
        ParamSpec("segment", "Segment", "select", True, list(CLASSES), "Law"),
        ParamSpec("country", "Country (ISO-2)", "text", True, default="GB"),
        ParamSpec("limit", "Max records", "int", False, default=500),
    ]

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        country = str(params["country"]).upper()[:2]
        if not country.isalpha():
            raise ValueError("country must be a 2-letter ISO code")
        classes = " ".join(f"wd:{q}" for q in CLASSES[params["segment"]])
        limit = int(params.get("limit") or 500)
        query = QUERY.format(classes=classes, country=country, limit=limit * 3)  # rows repeat per value
        body = self.http.get(
            ENDPOINT, params={"query": query, "format": "json"}, interval=2.0, timeout=90,
            headers={"Accept": "application/sparql-results+json"},
        ).json()  # fmt: skip
        merged: dict[str, dict[str, Any]] = {}
        for row in body.get("results", {}).get("bindings", []):
            item = row["item"]["value"]
            m = merged.setdefault(item, {"rows": []})
            m["rows"].append(row)
            for key in ("itemLabel", "website", "hqLabel", "employees", "inception"):
                if key in row and key not in m:
                    m[key] = row[key]["value"]
        n = 0
        for item, m in merged.items():
            name = m.get("itemLabel", "")
            if not name or _qid_label(name):
                continue  # unlabeled items have their QID as label
            employees = None
            if m.get("employees"):
                try:
                    employees = int(float(m["employees"]))
                except ValueError:
                    employees = None
            founded = int(m["inception"][:4]) if m.get("inception", "")[:4].isdigit() else None
            hq = m.get("hqLabel")
            yield CompanyRecord(
                source="wikidata",
                url=item,
                name=name,
                website=m.get("website"),
                country=country,
                city=hq if hq and not _qid_label(hq) else None,
                segment=params["segment"],
                employees=employees,
                founded=founded,
                size_band=size_band(employees),
                confidence=0.8,
                evidence=[api_evidence(item, m["rows"])],
            )
            n += 1
            if n >= limit:
                return
