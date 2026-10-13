"""Connector registry. Order = order shown in the UI."""

from __future__ import annotations

from .base import Connector, ParamSpec, PoliteClient
from .companies_house import CompaniesHouseConnector
from .contact_checks import ContactCheckConnector
from .domains import DomainsConnector
from .france import FranceRegistryConnector
from .github import GithubConnector
from .gleif import GleifConnector
from .jobs import JobsConnector
from .maintenance import ArchiveConnector, OutreachTickConnector, VerifyConnector
from .news import NewsConnector
from .npi import NpiConnector
from .osm import OsmConnector
from .sec_formd import SecFormDConnector
from .seed import SeedSheetConnector
from .website import WebsiteConnector
from .wikidata import WikidataConnector

CONNECTORS: dict[str, type[Connector]] = {
    c.key: c
    for c in (
        OsmConnector,
        GleifConnector,
        CompaniesHouseConnector,
        NpiConnector,
        WikidataConnector,
        FranceRegistryConnector,
        DomainsConnector,
        SeedSheetConnector,
        SecFormDConnector,
        WebsiteConnector,
        JobsConnector,
        NewsConnector,
        GithubConnector,
        VerifyConnector,
        ArchiveConnector,
        OutreachTickConnector,
        ContactCheckConnector,
    )
}

__all__ = ["CONNECTORS", "Connector", "ParamSpec", "PoliteClient"]
