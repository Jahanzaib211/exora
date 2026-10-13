"""New connectors against recorded responses (no network): GitHub, SEC Form D, French register."""

import hashlib
import time

import httpx
import pytest
from sqlalchemy import select

from prospector.connectors import CONNECTORS
from prospector.connectors.base import PoliteClient
from prospector.connectors.france import FranceRegistryConnector
from prospector.connectors.github import GithubConnector
from prospector.connectors.github import headers as github_headers
from prospector.connectors.sec_formd import SecFormDConnector, sec_headers
from prospector.models import Company, Evidence, utcnow
from prospector.resolve import CompanyRecord, api_evidence, store_evidence

FORM_D = """<?xml version="1.0"?>
<xml>
  <submissionType>D</submissionType>
  <primaryIssuer>
    <entityName>Exora Labs Inc</entityName>
    <issuerAddress>
      <street1>1 King Street</street1>
      <city>Manchester</city>
      <stateOrCountry>NH</stateOrCountry>
      <zipCode>03101</zipCode>
    </issuerAddress>
    <issuerPhoneNumber>+1 603 555 0100</issuerPhoneNumber>
    <entityType>Corporation</entityType>
  </primaryIssuer>
  <industryGroup>
    <industryGroupType>Technology</industryGroupType>
  </industryGroup>
  <typeOfFiling>
    <dateOfFirstSale><value>2024-05-15</value></dateOfFirstSale>
  </typeOfFiling>
  <offeringSalesAmounts>
    <totalOfferingAmount>5000000</totalOfferingAmount>
    <totalAmountSold>317561</totalAmountSold>
  </offeringSalesAmounts>
  <relatedPersonsList>
    <relatedPersonInfo>
      <relatedPersonName><firstName>Ada</firstName><lastName>Lovelace</lastName></relatedPersonName>
      <relatedPersonRelationshipList><relationship>director</relationship></relatedPersonRelationshipList>
    </relatedPersonInfo>
    <relatedPersonInfo>
      <relatedPersonName><firstName>N/A</firstName><lastName>N/A</lastName></relatedPersonName>
      <relatedPersonRelationshipList><relationship>10% owner</relationship></relatedPersonRelationshipList>
    </relatedPersonInfo>
    <relatedPersonInfo>
      <relatedPersonName><firstName>Acme</firstName><lastName>Holdings LLC</lastName></relatedPersonName>
      <relatedPersonRelationshipList><relationship>10% owner</relationship></relatedPersonRelationshipList>
    </relatedPersonInfo>
  </relatedPersonsList>
</xml>"""


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """The connectors pace themselves; a test must never wait on the interval clock or a retry back-off."""
    monkeypatch.setattr(PoliteClient, "_wait", lambda self, host, interval: None)
    monkeypatch.setattr(time, "sleep", lambda seconds: None)


def client(handler) -> PoliteClient:
    return PoliteClient(min_interval=0, transport=httpx.MockTransport(handler))


def seed_company(db, **kw) -> None:
    with db.session() as s:
        base = dict(name="Exora Labs", name_key="exora labs", domain="exora.com", website="https://exora.com")
        base.update(kw)
        s.add(Company(**base))
        s.commit()


# -- registry -------------------------------------------------------------
def test_new_connectors_are_registered_keyless_and_described():
    for key in ("github", "sec-formd", "france-registry", "contact-check"):
        assert key in CONNECTORS, key
        cls = CONNECTORS[key]
        assert cls.needs_key is None  # all three sources are keyless
        described = cls.describe()
        assert described["key"] == key and described["available"] is True
        assert described["licence"]
    assert [c.key for c in CONNECTORS.values()] == [
        "osm",
        "gleif",
        "companies-house",
        "npi",
        "wikidata",
        "france-registry",
        "domains",
        "seed-sheet",
        "sec-formd",
        "website",
        "jobs",
        "news",
        "github",
        "verify",
        "archive",
        "outreach",
        "contact-check",
    ]


# -- GitHub ---------------------------------------------------------------
def github_handler(request: httpx.Request, seen: list[httpx.Request]) -> httpx.Response:
    seen.append(request)
    if request.url.path == "/search/repositories":
        return httpx.Response(200, json={"items": [{"full_name": "exora/hero"}]})
    if request.url.path == "/repos/exora/hero/commits":
        return httpx.Response(
            200,
            json=[
                {
                    "commit": {"author": {"name": "Ada Lovelace", "email": "ada@exora.com"}, "message": "Ship it"},
                    "html_url": "https://github.com/exora/hero/commit/a",
                },
                {  # the same person again: one contact, not two
                    "commit": {"author": {"name": "Ada Lovelace", "email": "ada@exora.com"}, "message": "Again"},
                    "html_url": "https://github.com/exora/hero/commit/b",
                },
                {  # personal free-mail address: never stored
                    "commit": {"author": {"name": "Bot", "email": "bot@gmail.com"}, "message": "chore"},
                    "html_url": "https://github.com/exora/hero/commit/c",
                },
                {  # role mailbox: not a named person
                    "commit": {"author": {"name": "Press", "email": "info@exora.com"}, "message": "docs"},
                    "html_url": "https://github.com/exora/hero/commit/d",
                },
            ],
        )
    return httpx.Response(404)


def test_github_keeps_only_on_domain_named_addresses(db, monkeypatch):
    monkeypatch.delenv("PROSPECTOR_GITHUB_TOKEN", raising=False)
    seed_company(db)
    seen: list[httpx.Request] = []
    conn = GithubConnector(client(lambda r: github_handler(r, seen)), db=db)
    recs = list(conn.fetch({"limit": 5}))
    assert len(recs) == 1
    r = recs[0]
    assert r.source == "github" and r.url == "https://github.com/exora/hero"
    assert r.confidence == 0.75 and r.fetched_at is not None
    assert [(p.name, p.email) for p in r.people] == [("Ada Lovelace", "ada@exora.com")]
    assert r.people[0].url.endswith("/commit/a") and r.people[0].excerpt == "Ship it"
    assert len(r.evidence) == 1 and r.evidence[0].kind == "api"
    assert r.evidence[0].url.endswith("/commits")
    # politeness: GitHub requires a UA; the token is only sent when configured
    assert seen[0].headers["User-Agent"]
    assert "Authorization" not in seen[0].headers
    assert seen[0].headers["Accept"] == "application/vnd.github+json"
    assert "q=exora.com" in str(seen[0].url)


def test_github_sends_the_bearer_token_when_configured(monkeypatch):
    monkeypatch.setenv("PROSPECTOR_GITHUB_TOKEN", "tok123")
    assert github_headers()["Authorization"] == "Bearer tok123"
    monkeypatch.delenv("PROSPECTOR_GITHUB_TOKEN")
    assert "Authorization" not in github_headers()


def test_github_rate_limit_ends_the_run_quietly(db):
    seed_company(db)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "API rate limit exceeded"})

    conn = GithubConnector(client(handler), db=db)
    assert list(conn.fetch({"limit": 5})) == []


def test_github_missing_company_records_degrade_to_nothing(db):
    seed_company(db)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/search/repositories":
            return httpx.Response(200, json={"items": [{"full_name": "exora/hero"}]})
        return httpx.Response(404)  # the repository vanished between the two calls

    conn = GithubConnector(client(handler), db=db)
    assert list(conn.fetch({"limit": 5})) == []


def test_github_empty_database_makes_no_requests(db):
    calls: list[httpx.Request] = []
    conn = GithubConnector(client(lambda r: calls.append(r) or httpx.Response(200, json={})), db=db)
    assert list(conn.fetch({"limit": 5})) == []
    assert calls == []


# -- SEC Form D -----------------------------------------------------------
def sec_handler(request: httpx.Request, seen: list[httpx.Request]) -> httpx.Response:
    seen.append(request)
    if request.url.path == "/LATEST/search-index":
        return httpx.Response(
            200,
            json={
                "hits": {
                    "hits": [
                        {
                            "_id": "0001234567-24-000123:primary_doc.xml",
                            "_source": {
                                "ciks": ["0001234567"],
                                "file_date": "2024-06-01",
                                "display_names": ["Exora Labs, Inc."],
                            },
                        }
                    ]
                }
            },
        )
    if request.url.path.endswith("/primary_doc.xml"):
        return httpx.Response(200, text=FORM_D, headers={"content-type": "text/xml"})
    return httpx.Response(404)


def test_sec_formd_yields_filing_signal_address_and_people(monkeypatch):
    monkeypatch.delenv("PROSPECTOR_SEC_USER_AGENT", raising=False)
    monkeypatch.setenv("PROSPECTOR_CONTACT_EMAIL", "research@example.com")
    seen: list[httpx.Request] = []
    conn = SecFormDConnector(client(lambda r: sec_handler(r, seen)))
    recs = list(conn.fetch({"window_days": 30, "limit": 10}))
    assert len(recs) == 1
    r = recs[0]
    assert r.source == "sec-formd"
    assert r.name == "Exora Labs Inc"
    assert r.url == "https://www.sec.gov/Archives/edgar/data/1234567/000123456724000123/primary_doc.xml"
    assert r.country == "US" and r.city == "Manchester"
    assert r.address == "1 King Street, Manchester, NH, 03101"
    assert r.phone == "+1 603 555 0100"  # the issuer's own switchboard, kept as a data field
    assert r.company_number == "1234567" and r.segment == "Technology"
    assert r.description == "Corporation" and r.confidence == 0.8
    # the funding signal is the point of this source
    assert [s[0] for s in r.signals] == ["funding"]
    assert "raised $317,561 of $5,000,000 offered" in r.signals[0][1]
    assert "first sale 2024-05-15" in r.signals[0][1]
    # people: the natural person only (N/A and entity rows skipped), role as the title
    assert [(p.name, p.title) for p in r.people] == [("Ada Lovelace", "director")]
    # receipts: the search batch plus the filing itself, hashed as received
    assert len(r.evidence) == 2
    file_ev = r.evidence[1]
    assert file_ev.kind == "file" and file_ev.url == r.url
    assert file_ev.sha256 == hashlib.sha256(FORM_D.encode("utf-8", "replace")).hexdigest()
    # SEC etiquette: a descriptive User-Agent carries a contact address
    assert seen[0].headers["User-Agent"] == "Exora Research (research@example.com)"
    assert seen[1].headers["User-Agent"] == seen[0].headers["User-Agent"]
    assert "forms=D" in str(seen[0].url)


def test_sec_user_agent_is_configurable(monkeypatch):
    monkeypatch.setenv("PROSPECTOR_CONTACT_EMAIL", "research@example.com")
    monkeypatch.setenv("PROSPECTOR_SEC_USER_AGENT", "Exora Research (privacy@exora.com)")
    assert sec_headers()["User-Agent"] == "Exora Research (privacy@exora.com)"
    monkeypatch.delenv("PROSPECTOR_SEC_USER_AGENT")
    assert sec_headers()["User-Agent"] == "Exora Research (research@example.com)"
    monkeypatch.delenv("PROSPECTOR_CONTACT_EMAIL")
    assert sec_headers()["User-Agent"].startswith("Exora/")


def test_discovery_user_agent_names_the_product_and_contact(monkeypatch):
    from prospector.connectors.base import user_agent

    monkeypatch.delenv("PROSPECTOR_USER_AGENT", raising=False)
    monkeypatch.delenv("PROSPECTOR_CONTACT_EMAIL", raising=False)
    assert user_agent().startswith("Exora/") and "github.com" in user_agent()
    monkeypatch.setenv("PROSPECTOR_CONTACT_EMAIL", "research@example.com")
    assert user_agent().endswith("; research@example.com)")
    monkeypatch.setenv("PROSPECTOR_USER_AGENT", "Custom/1.0")
    assert user_agent() == "Custom/1.0"


def test_sec_formd_unavailable_search_returns_nothing():
    conn = SecFormDConnector(client(lambda r: httpx.Response(500)))
    assert list(conn.fetch({})) == []


def test_sec_formd_blocked_without_contact_fails_loudly():
    # EDGAR answers 403 to clients without a contact address; that must not pass as "no filings this week".
    conn = SecFormDConnector(client(lambda r: httpx.Response(403)))
    with pytest.raises(RuntimeError, match="PROSPECTOR_CONTACT_EMAIL"):
        list(conn.fetch({}))


def test_sec_formd_unreadable_filing_is_skipped():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/LATEST/search-index":
            return sec_handler(request, [])
        return httpx.Response(200, text="<not>xml</broken")  # a filing that will not parse

    conn = SecFormDConnector(client(handler))
    assert list(conn.fetch({})) == []


# -- French register ------------------------------------------------------
def france_item(i: int = 0, **kw) -> dict:
    item = {
        "siren": f"1234567{i:02d}",
        "nom_complet": "SARL EXORA CONSEIL",
        "siege": {"adresse": "12 Rue de la Paix", "libelle_commune": "paris"},
        "date_creation": "2015-04-01",
        "etat_administratif": "A",
        "statut_diffusion": "O",
        "dirigeants": [
            {"type_dirigeant": "personne physique", "nom": "DUPONT", "prenoms": "Marie Jeanne", "qualite": "gérant"},
            {"type_dirigeant": "personne morale", "nom": "EXORA HOLDINGS", "qualite": "gérant"},
            {"type_dirigeant": "personne physique", "nom": "SANS PRENOM", "qualite": "gérant"},
        ],
    }
    item.update(kw)
    return item


def test_france_registry_record_officers_and_privacy_gates():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    france_item(0),
                    france_item(1, siren="999999999", statut_diffusion="N"),  # non-diffusable: skipped
                    france_item(2, nom_complet=""),  # no usable name: skipped
                ],
                "total_results": 3,
            },
        )

    conn = FranceRegistryConnector(client(handler))
    recs = list(conn.fetch({"query": "cabinet paris", "limit": 25}))
    assert len(recs) == 1
    r = recs[0]
    assert r.source == "france-registry"
    assert r.name == "SARL EXORA CONSEIL"
    assert r.url == "https://annuaire-entreprises.data.gouv.fr/entreprise/123456700"
    assert r.country == "FR" and r.city == "Paris" and r.address == "12 Rue de la Paix"
    assert r.company_number == "123456700" and r.registry_status == "ACTIVE" and r.founded == 2015
    assert r.confidence == 0.95
    # natural-person officers only (corporate officer and a first-name-less entry skipped)
    assert [(p.name, p.title) for p in r.people] == [("Marie Jeanne Dupont", "Gérant")]
    assert "Official register entry" in r.people[0].excerpt
    assert len(r.evidence) == 1 and r.evidence[0].kind == "api"
    url = str(seen[0].url)
    assert "q=cabinet" in url and url.startswith("https://recherche-entreprises.api.gouv.fr/")


def test_france_registry_maps_closing_statuses():
    statuses = {"A": "ACTIVE", "C": "CLOSED", "F": "CLOSED", "I": "INACTIVE"}
    for etat, want in statuses.items():

        def handler(request, etat=etat):
            return httpx.Response(200, json={"results": [france_item(0, etat_administratif=etat)], "total_results": 1})

        recs = list(FranceRegistryConnector(client(handler)).fetch({"query": "x"}))
        assert recs[0].registry_status == want


def test_france_registry_paginates_up_to_the_limit():
    pool = [france_item(i, siren=f"1000000{i:02d}", nom_complet=f"SARL NUMBER {i}") for i in range(30)]
    pending = list(pool)
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        n = int(request.url.params["per_page"])
        sizes.append(n)
        batch = pending[:n]
        del pending[:n]
        return httpx.Response(200, json={"results": batch, "total_results": 50})

    recs = list(FranceRegistryConnector(client(handler)).fetch({"query": "sarl", "limit": 30}))
    assert len(recs) == 30
    assert sizes == [25, 5]  # the API's page maximum is 25


def test_france_registry_requires_a_query():
    conn = FranceRegistryConnector(client(lambda r: httpx.Response(200, json={})))
    with pytest.raises(ValueError):
        list(conn.fetch({"query": "   "}))


def test_france_registry_api_errors_surface_and_outages_do_not():
    broken = FranceRegistryConnector(client(lambda r: httpx.Response(200, json={"erreur": "too many requests"})))
    with pytest.raises(ValueError, match="too many requests"):
        list(broken.fetch({"query": "sarl"}))

    not_json = FranceRegistryConnector(client(lambda r: httpx.Response(200, text="<html>busy</html>")))
    assert list(not_json.fetch({"query": "sarl"})) == []

    down = FranceRegistryConnector(client(lambda r: httpx.Response(503)))
    assert list(down.fetch({"query": "sarl"})) == []


# -- provenance: fetched_at stamps the evidence rows ----------------------
def test_record_fetched_at_stamps_evidence(db):
    # Captured before the record exists: `CompanyRecord.fetched_at` defaults to `utcnow()` at
    # construction, so reading it afterwards would demand a timestamp that is later than the
    # construction that produced it. The window between the two calls is about a millisecond, and
    # the assertion failed whenever the clock advanced across it.
    before = utcnow()
    rec = CompanyRecord(
        source="github",
        url="https://api.github.com/repos/exora/hero/commits",
        name="Exora Labs",
        evidence=[api_evidence("https://api.github.com/repos/exora/hero/commits", [{"a": 1}])],
    )
    assert rec.fetched_at is not None
    with db.session() as s:
        store_evidence(s, rec)
        row = s.scalar(select(Evidence))
    assert row is not None
    assert row.source == "github"
    assert row.fetched_at == rec.fetched_at  # the stored receipt keeps the record's own stamp
    assert before <= row.fetched_at <= utcnow()  # the receipt says when the source was read
