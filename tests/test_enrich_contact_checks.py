"""The contact-check maintenance job: one verdict per stored detail, with source and timestamp."""

import pytest

from prospector.connectors.base import PoliteClient
from prospector.connectors.contact_checks import ContactCheckConnector
from prospector.contact import emailcheck, patterns, phone
from prospector.models import Company, ContactCheck, Person, utcnow


@pytest.fixture(autouse=True)
def _clean():
    patterns._cache.clear()
    patterns._last_probe.clear()
    yield
    patterns._cache.clear()
    patterns._last_probe.clear()


def fake_dns(domain: str, rtype: str) -> list[str]:
    """Injected resolver: exora.com has a healthy mail setup, broken.org cannot answer at all."""
    if domain == "broken.org":
        raise RuntimeError("timed out")
    if domain == "exora.com":
        return {"MX": ["mail.exora.com"], "A": ["1.2.3.4"], "AAAA": [], "TXT": []}.get(rtype, [])
    return []


def seed(db) -> None:
    with db.session() as s:
        a = Company(
            name="Exora Labs",
            name_key="exora labs",
            domain="exora.com",
            website="https://exora.com",
            country="GB",
            general_email="info@exora.com",
            phone="+44 20 7946 0958",
        )
        s.add(a)
        s.flush()
        s.add(Person(company_id=a.id, name="Ada Lovelace", name_key="ada lovelace", email="ada@exora.com"))
        s.add(
            Company(
                name="Broken Co",
                name_key="broken co",
                domain="broken.org",
                general_email="bad@@address",
            )
        )
        s.commit()


def rows(db) -> list[ContactCheck]:
    with db.session() as s:
        return list(s.query(ContactCheck))


def run(db, **params):
    con = ContactCheckConnector(PoliteClient(), db=db, resolver=fake_dns)
    base = {"limit": 200, "older_than_days": 7, "smtp_probe": "off"}
    base.update(params)
    return con.run_maintenance(base)


def test_every_stored_detail_gets_a_verdict_with_provenance(db):
    seed(db)
    found, updated, message = run(db)
    assert found == updated == 4  # three addresses + one number
    assert "4 contact checks written" in message
    by_value = {r.value: r for r in rows(db)}
    assert by_value["info@exora.com"].verdict == "valid"
    assert by_value["ada@exora.com"].verdict == "valid"
    assert by_value["bad@@address"].verdict == "invalid" and "syntax" in by_value["bad@@address"].reason
    assert by_value["+44 20 7946 0958"].verdict == "valid"
    for r in rows(db):
        assert r.reason  # never a bare verdict
        assert r.source in (emailcheck.SOURCE, phone.SOURCE)
        assert r.checked_at is not None


def test_dns_failure_is_unknown_not_invalid(db):
    with db.session() as s:
        s.add(Company(name="Broken Co", name_key="broken co", domain="broken.org", general_email="post@broken.org"))
        s.commit()
    found, updated, _ = run(db)
    assert found == updated == 1
    row = rows(db)[0]
    assert row.verdict == "unknown" and "failed" in row.reason


def test_recently_checked_values_are_skipped(db):
    seed(db)
    assert run(db)[1] == 4
    found, updated, message = run(db, older_than_days=30)
    assert (found, updated) == (4, 0) and message is None


def test_phone_verdicts_use_the_offline_number_plans(db):
    with db.session() as s:
        c = Company(name="Exora Labs", name_key="exora labs", domain="exora.com", country="GB", phone="123")
        s.add(c)
        s.flush()
        s.add(Person(company_id=c.id, name="Ada", name_key="ada", phone="+44 20 7946 0958"))
        s.commit()
    found, updated, _ = run(db, older_than_days=0)
    assert found == updated == 2
    verdicts = {r.value: r for r in rows(db)}
    assert verdicts["123"].verdict == "invalid" and verdicts["123"].source == "phonenumbers"
    assert verdicts["+44 20 7946 0958"].verdict == "valid"


def test_smtp_probe_stays_off_without_the_env_switch(db, monkeypatch):
    monkeypatch.delenv("PROSPECTOR_SMTP_PROBE", raising=False)
    seed(db)
    run(db, smtp_probe="on")  # the run parameter alone is not enough
    assert all(r.source != "smtp-rcpt" for r in rows(db))


def test_smtp_probe_rows_when_both_switches_are_on(db, monkeypatch):
    monkeypatch.setenv("PROSPECTOR_SMTP_PROBE", "1")
    seed(db)

    def probe(address: str) -> bool | None:
        local = address.split("@", 1)[0]
        if local.startswith("probe."):
            return False  # not a catch-all
        if local == "ada":
            return True
        if local == "info":
            return False
        return None

    con = ContactCheckConnector(PoliteClient(), db=db, resolver=fake_dns, probe=probe)
    found, updated, _ = con.run_maintenance({"limit": 50, "older_than_days": 0, "smtp_probe": "on"})
    assert updated == found + 2  # every candidate judged twice: DNS verdict + RCPT probe
    rcpt = {r.value: r for r in rows(db) if r.source == "smtp-rcpt"}
    assert set(rcpt) == {"info@exora.com", "ada@exora.com"}
    assert rcpt["ada@exora.com"].verdict == "accepted"
    assert rcpt["info@exora.com"].verdict == "rejected"
    assert rcpt["ada@exora.com"].checked_at is not None


def test_connector_is_keyless_maintenance(db):
    assert ContactCheckConnector.needs_key is None
    assert ContactCheckConnector.maintenance is True
    assert ContactCheckConnector.needs_db is True
    described = ContactCheckConnector.describe()
    assert [p["name"] for p in described["params"]] == ["limit", "older_than_days", "smtp_probe"]
    assert described["available"] is True


def test_checked_at_is_a_fresh_timestamp():
    before = utcnow()
    v = emailcheck.check_email("jane@exora.com", resolver=lambda d, t: ["mx.exora.com"])
    assert before <= v.checked_at <= utcnow()
