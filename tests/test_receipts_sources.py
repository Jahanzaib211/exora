"""Receipts, the new free sources (NPI, archive, news, Hacker News) and evidence on observations."""

import httpx
from sqlalchemy import select

from prospector.connectors.base import PoliteClient
from prospector.connectors.maintenance import ArchiveConnector
from prospector.connectors.news import headline_mentions, search_name
from prospector.connectors.npi import NpiConnector
from prospector.connectors.website import WebsiteConnector
from prospector.crawl.extract import phone_context
from prospector.models import Evidence, Observation, Person
from prospector.resolve import CompanyRecord, api_evidence, upsert_company

TEAM = """<html><head><title>Our team | Smith Law</title></head><body>
<p>Call us on 0161 832 1234 or email info@smithlaw.co.uk for advice.</p>
<div class="team-member"><h3>Jane Doe</h3><p>Managing Partner</p></div></body></html>"""


def client(handler) -> PoliteClient:
    return PoliteClient(min_interval=0, transport=httpx.MockTransport(handler))


def test_api_evidence_is_deterministic():
    a = api_evidence("https://x/1", {"b": 2, "a": 1})
    b = api_evidence("https://x/1", {"a": 1, "b": 2})
    assert a.sha256 == b.sha256 and len(a.sha256) == 64


def test_phone_context_matches_local_formats():
    assert "0161 832 1234" in phone_context("Call us on 0161 832 1234 today", "+441618321234")
    assert phone_context("No numbers here", "+441618321234") is None


def test_website_receipts_end_to_end(db):
    rec = WebsiteConnector.build_record(
        ("Smith Law", "smithlaw.co.uk"), "https://smithlaw.co.uk/", [("https://smithlaw.co.uk/team", TEAM)]
    )
    rec.phone = "+441618321234"
    with db.session() as s:
        c, _ = upsert_company(s, rec)
        ev = s.scalar(select(Evidence))
        assert ev.kind == "page" and ev.title == "Our team | Smith Law" and len(ev.sha256) == 64
        email_obs = s.scalar(select(Observation).where(Observation.field == "general_email"))
        assert email_obs.evidence_id == ev.id and "info@smithlaw.co.uk" in email_obs.excerpt
        person = s.scalar(select(Person))
        person_obs = s.scalar(select(Observation).where(Observation.entity_type == "person"))
        assert person.name == "Jane Doe" and person_obs.evidence_id == ev.id and "Jane Doe" in person_obs.excerpt
        # the same page seen again is the same receipt (no duplicates)
        upsert_company(s, rec)
        assert s.query(Evidence).count() == 1


def test_npi_connector():
    body = {"result_count": 1, "results": [{
        "number": "1184285140",
        "basic": {"organization_name": "A BALANCED LIFE ATX, PLLC", "status": "A",
                  "authorized_official_first_name": "COLLEEN", "authorized_official_last_name": "SEVILLA",
                  "authorized_official_title_or_position": "Managing Member", "authorized_official_credential": "PsyD",
                  "authorized_official_telephone_number": "5127451779"},
        "addresses": [{"address_purpose": "LOCATION", "address_1": "4131 SPICEWOOD SPRINGS RD", "city": "AUSTIN",
                       "state": "TX", "postal_code": "787598658", "telephone_number": "512-745-1779"}],
        "taxonomies": [{"desc": "Psychologist, Clinical", "primary": True}],
    }]}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["enumeration_type"] == "NPI-2" and request.url.params["state"] == "TX"
        return httpx.Response(200, json=body if request.url.params["skip"] == "0" else {"results": []})

    recs = list(NpiConnector(client(handler)).fetch({"state": "tx", "taxonomy": "clinic", "limit": 5}))
    assert len(recs) == 1
    r = recs[0]
    assert r.name == "A Balanced Life ATX, PLLC" and r.registry_status == "ACTIVE" and r.city == "Austin"
    assert r.people[0].name == "Colleen Sevilla" and r.people[0].title == "Managing Member (PsyD)"
    assert r.evidence and r.evidence[0].kind == "api"


def test_archive_receipts(db):
    with db.session() as s:
        upsert_company(s, WebsiteConnector.build_record(("Smith Law", "smithlaw.co.uk"), "https://smithlaw.co.uk/",
                                                        [("https://smithlaw.co.uk/team", TEAM)]))  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"archived_snapshots": {"closest": {
            "available": True, "status": "200", "url": "http://web.archive.org/web/2026/https://smithlaw.co.uk/team",
        }}})  # fmt: skip

    found, linked, msg = ArchiveConnector(client(handler), db=db).run_maintenance({"limit": 10})
    assert (found, linked) == (1, 1) and "1 of 1" in msg
    with db.session() as s:
        assert s.scalar(select(Evidence.archive_url)).startswith("http://web.archive.org/")


def test_news_name_matching():
    assert search_name("Smith & Partners LLP") is None  # too generic to match safely
    assert search_name("Kingsley Napley LLP") == "Kingsley Napley"
    assert search_name("Cue") is None
    assert headline_mentions("Kingsley Napley advises on landmark AI case", "Kingsley Napley")
    assert not headline_mentions("Kingsley Napleys? No: Napley Kingsley sports", "Kingsley Napley")


def test_observation_without_receipt_still_recorded(db):
    with db.session() as s:
        c, _ = upsert_company(s, CompanyRecord("import", None, "acme.com", website="acme.com"))
        obs = s.scalars(select(Observation).where(Observation.entity_id == c.id)).all()
        assert obs and all(o.evidence_id is None for o in obs)
