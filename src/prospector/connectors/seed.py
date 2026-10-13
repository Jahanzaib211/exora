"""Seed sheet: a CSV of hand-researched target accounts (PROSPECTOR_SEED_CSV, see playbook/)."""

from __future__ import annotations

import csv
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ..resolve import CompanyRecord, PersonRecord, api_evidence
from .base import Connector, ParamSpec

SEGMENT_MAP = {
    "Law firm": "Law",
    "Accounting/CPA": "CPA",
    "MSP/IT partner": "MSP",
    "Integrator/Reseller": "MSP",
    "AI startup": "AI startup",
    "Healthcare/Clinic": "Healthcare",
    "KSA SME/Enterprise": "KSA SME",
    "EU mid-market": "EU mid-market",
}


class SeedSheetConnector(Connector):
    key = "seed-sheet"
    name = "Target accounts sheet"
    description = "Imports the hand-researched target accounts, keeping each row's source URL and signal."
    licence = "Internal research; each row cites its public source"
    params = [
        ParamSpec(
            "path",
            "CSV path",
            "text",
            False,
            help="Relative to the docs folder. Empty = PROSPECTOR_SEED_CSV (playbook/accounts.example.csv).",
        )
    ]

    def __init__(self, http, api_key=None, root: Path | None = None, default_path: str | None = None) -> None:
        super().__init__(http, api_key)
        self.root = root
        self.default_path = default_path or "playbook/accounts.example.csv"

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        path = Path(params.get("path") or self.default_path)
        if not path.is_absolute() and self.root:
            path = self.root / path
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                people = []
                if (row.get("named_contact") or "").strip():
                    people.append(
                        PersonRecord(
                            name=row["named_contact"],
                            title=row.get("named_contact_title") or None,
                            url=row.get("contact_source_url") or row.get("source_url"),
                        )
                    )
                signals = []
                if (row.get("fit_signal") or "").strip():
                    signals.append(("fit", row["fit_signal"].strip(), row.get("source_url")))
                yield CompanyRecord(
                    source="seed-sheet",
                    url=row.get("source_url") or None,
                    # receipt: the exact sheet row this record came from
                    evidence=[api_evidence(f"file:{path.name}#{row.get('lead_id', '')}", row, kind="file")],
                    name=row["company"],
                    website=row.get("website") or None,
                    country=row.get("country") or None,
                    city=row.get("city") or None,
                    segment=SEGMENT_MAP.get(row.get("segment", ""), row.get("segment") or None),
                    phone=row.get("company_phone") or None,
                    general_email=row.get("company_general_email") or None,
                    linkedin_url=row.get("linkedin_company_url") or None,
                    size_band=row.get("size_or_rank") or None,
                    confidence=0.85,
                    people=people,
                    signals=signals,
                )
