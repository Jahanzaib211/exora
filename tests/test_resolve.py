from sqlalchemy import func, select

from prospector.models import Company, Observation, Person
from prospector.normalize import best_contact_email, country_code, domain_of, email_kind, name_key, phone_e164
from prospector.resolve import CompanyRecord, PersonRecord, upsert_company


def test_normalization():
    assert domain_of("https://www.smithlaw.co.uk/team") == "smithlaw.co.uk"
    assert domain_of("jane@mail.acme.com.sa") == "acme.com.sa"
    assert country_code("UK") == "GB" and country_code("US (HQ not stated in source)") == "US"
    assert country_code("Saudi Arabia") == "SA" and country_code("US/Europe (distributed)") is None
    assert phone_e164("0161 832 1234", "GB") == "+441618321234" and phone_e164("12", "US") is None
    assert name_key("Smith & Partners LLP") == name_key("smith and partners")
    assert email_kind("x@gmail.com", "acme.com") == "free_mail"
    assert email_kind("info@acme.com", "acme.com") == "role"
    assert best_contact_email(["security@acme.com", "press@acme.com"]) is None
    assert best_contact_email(["sales@acme.com", "info@acme.com"]) == "info@acme.com"


def test_same_company_from_two_sources_merges_and_keeps_both_sources(db):
    with db.session() as s:
        a, created_a = upsert_company(
            s, CompanyRecord("osm", "https://osm.org/n/1", "Smith Law LLP", website="smithlaw.co.uk", country="GB")
        )
        b, created_b = upsert_company(
            s,
            CompanyRecord(
                "website",
                "https://smithlaw.co.uk/",
                "Smith Law",
                website="https://www.smithlaw.co.uk/",
                phone="0161 832 1234",
                people=[PersonRecord("Jane Doe", "Managing Partner")],
            ),
        )
        assert created_a and not created_b and a.id == b.id
        assert b.phone == "+441618321234"  # local number normalized with the company's country
        sources = set(s.scalars(select(Observation.source).where(Observation.entity_id == a.id)))
        assert sources == {"osm", "website"}
        person = s.scalar(select(Person))
        assert person.seniority == "partner"


def test_rerun_refreshes_instead_of_duplicating_observations(db):
    rec = CompanyRecord("osm", "https://osm.org/n/2", "Acme Accounting", website="acme.com", country="US")
    with db.session() as s:
        upsert_company(s, rec)
        n1 = s.scalar(select(func.count(Observation.id)))
        upsert_company(s, rec)
        assert s.scalar(select(func.count(Observation.id))) == n1


def test_fuzzy_match_never_merges_conflicting_identifiers(db):
    with db.session() as s:
        a, _ = upsert_company(
            s, CompanyRecord("gleif", None, "Al Mihdar and Yamani Law Firm", country="SA", lei="LEI1")
        )
        b, created = upsert_company(
            s, CompanyRecord("gleif", None, "Al Mihdar Yamani Law Firm", country="SA", lei="LEI2")
        )
        assert created and a.id != b.id
        c, created = upsert_company(s, CompanyRecord("osm", None, "Al Mihdar & Yamani Law Firm", country="SA"))
        assert not created  # no conflicting identifier: fuzzy match is allowed


def test_person_phone_and_profile_are_stored_with_a_receipt(db):
    """A person's direct dial and profile are kept, and each leaves an observation naming its page.

    `linkedin_url` used to be written without an observation, so a stored profile had no receipt.
    """
    with db.session() as s:
        upsert_company(
            s,
            CompanyRecord(
                "website",
                None,
                "Acme",
                website="acme.example",
                country="GB",
                people=[
                    PersonRecord(
                        "Jane Doe",
                        "Managing Partner",
                        url="https://acme.example/team",
                        phone="0161 832 1234",
                        linkedin_url="https://www.linkedin.com/in/jane-doe?trk=profile",
                        excerpt="Jane Doe, Managing Partner, 0161 832 1234",
                    )
                ],
            ),
        )
        person = s.scalar(select(Person))
        assert person.phone == "+441618321234"  # local format finished off with the company's country
        assert person.linkedin_url == "https://www.linkedin.com/in/jane-doe"
        fields = set(s.scalars(select(Observation.field).where(Observation.entity_type == "person")))
        assert fields == {"name", "title", "phone", "linkedin_url"}  # no email was published, so none is logged
        li = s.scalar(select(Observation).where(Observation.field == "linkedin_url"))
        assert li.value == "https://www.linkedin.com/in/jane-doe"
        assert li.url == "https://acme.example/team"
        assert li.excerpt  # the receipt shows the words the value was read from


def test_personal_and_third_party_emails_are_never_stored(db):
    with db.session() as s:
        c, _ = upsert_company(
            s,
            CompanyRecord(
                "website",
                None,
                "Acme",
                website="acme.com",
                general_email="owner@gmail.com",
                people=[PersonRecord("John Smith", "Director", email="john@gmail.com")],
            ),
        )
        assert c.general_email is None
        assert s.scalar(select(Person.email)) is None
        assert s.scalar(select(func.count(Company.id))) == 1
