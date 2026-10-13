"""Add companies by website: paste domains, each becomes a company and is then crawled automatically."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from ..normalize import domain_of
from ..resolve import CompanyRecord
from .base import Connector, ParamSpec

MAX_DOMAINS = 1000


def parse_domains(text: str) -> list[str]:
    out: list[str] = []
    for token in re.split(r"[\s,;]+", text or ""):
        d = domain_of(token.strip().strip("<>\"'()"))
        if d and d not in out:
            out.append(d)
    return out[:MAX_DOMAINS]


class DomainsConnector(Connector):
    key = "domains"
    name = "Add by domain"
    description = "Paste company websites. Each becomes a company record and its website is crawled next."
    licence = "Your own list"
    params = [
        ParamSpec("domains", "Websites or domains", "textarea", True,
                  help="One per line or comma-separated, e.g. smithlaw.co.uk"),
        ParamSpec("segment", "Segment", "select", False,
                  ["Law", "CPA", "MSP", "Healthcare", "AI startup", "KSA SME", "EU mid-market"]),
        ParamSpec("country", "Country (ISO-2)", "text", False),
    ]  # fmt: skip

    def fetch(self, params: dict[str, Any]) -> Iterator[CompanyRecord]:
        for domain in parse_domains(params.get("domains", "")):
            yield CompanyRecord(
                source="import",
                url=None,
                name=domain,  # replaced by the organisation's own name once the website is crawled
                website=f"https://{domain}/",
                segment=params.get("segment"),
                country=params.get("country"),
                confidence=0.6,
            )
