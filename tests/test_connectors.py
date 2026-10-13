"""Connectors against recorded API responses (no network): Companies House, job boards, Wikidata."""

import json

import httpx

from prospector.connectors.base import PoliteClient
from prospector.connectors.companies_house import CompaniesHouseConnector, officer_name
from prospector.connectors.jobs import parse_roles
from prospector.connectors.wikidata import WikidataConnector


def client(handler) -> PoliteClient:
    c = PoliteClient(min_interval=0, transport=httpx.MockTransport(handler))
    return c


def test_companies_house_search_and_officers():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/advanced-search/companies":
            return httpx.Response(200, json={"items": [{
                "company_name": "SMITH & CO LLP", "company_number": "OC123456", "company_status": "active",
                "registered_office_address": {"address_line_1": "1 King St", "locality": "MANCHESTER",
                                              "postal_code": "M2 4LQ"},
            }], "hits": 1})  # fmt: skip
        return httpx.Response(200, json={"items": [
            {"name": "SMITH, Jane Elizabeth", "officer_role": "llp-designated-member"},
            {"name": "DOE, John", "officer_role": "director", "resigned_on": "2020-01-01"},
            {"name": "ACME HOLDINGS LTD", "officer_role": "corporate-director"},
        ]})  # fmt: skip

    conn = CompaniesHouseConnector(client(handler), api_key="test-key")
    recs = list(conn.fetch({"segment": "Law", "location": "Manchester", "limit": 5}))
    assert len(recs) == 1
    r = recs[0]
    assert r.company_number == "OC123456" and r.registry_status == "ACTIVE" and r.city == "Manchester"
    assert [(p.name, p.title) for p in r.people] == [("Jane Elizabeth Smith", "Designated Member")]
    assert seen[0].headers["Authorization"].startswith("Basic ")
    assert "sic_codes=69102%2C69101%2C69109" in str(seen[0].url)


def test_companies_house_requires_key():
    conn = CompaniesHouseConnector(client(lambda r: httpx.Response(500)), api_key=None)
    try:
        list(conn.fetch({"segment": "Law", "location": "Leeds"}))
    except RuntimeError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("expected an error without a key")


def test_officer_name():
    assert officer_name("SMITH, Mr Jonathan Paul") == "Jonathan Paul Smith"
    assert officer_name("O'BRIEN, Siobhan") == "Siobhan O'Brien"


def test_job_board_parsers():
    gh = {"jobs": [{"title": "IT Manager", "location": {"name": "London"}, "absolute_url": "https://x/1"}]}
    lever = [{"text": "Data Engineer", "categories": {"location": "Remote"}, "hostedUrl": "https://y/2"}]
    ashby = {"jobs": [{"title": "Paralegal", "location": "Leeds", "jobUrl": "https://z/3"}]}
    assert parse_roles("greenhouse", gh) == [("IT Manager", "London", "https://x/1")]
    assert parse_roles("lever", lever) == [("Data Engineer", "Remote", "https://y/2")]
    assert parse_roles("ashby", ashby) == [("Paralegal", "Leeds", "https://z/3")]


def test_wikidata_dedupes_and_skips_unlabeled():
    body = {"results": {"bindings": [
        {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "itemLabel": {"value": "Big Law LLP"},
         "website": {"value": "https://biglaw.com"}, "hqLabel": {"value": "London"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q1"}, "itemLabel": {"value": "Big Law LLP"},
         "website": {"value": "https://biglaw.co.uk"}},
        {"item": {"value": "http://www.wikidata.org/entity/Q2"}, "itemLabel": {"value": "Q2"}},
    ]}}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Q613142" in request.url.params["query"] and '"GB"' in request.url.params["query"]
        return httpx.Response(200, content=json.dumps(body), headers={"content-type": "application/json"})

    recs = list(WikidataConnector(client(handler)).fetch({"segment": "Law", "country": "GB", "limit": 10}))
    assert [(r.name, r.website, r.city) for r in recs] == [("Big Law LLP", "https://biglaw.com", "London")]


def test_overpass_endpoints_put_the_operators_server_first(monkeypatch):
    from prospector.connectors.osm import OVERPASS, overpass_endpoints

    monkeypatch.delenv("PROSPECTOR_OVERPASS_URL", raising=False)
    assert overpass_endpoints() == OVERPASS
    monkeypatch.setenv("PROSPECTOR_OVERPASS_URL", f"https://op.example.com/api/interpreter, {OVERPASS[0]}")
    assert overpass_endpoints() == ["https://op.example.com/api/interpreter", *OVERPASS]
